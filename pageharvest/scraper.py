"""Session-based HTTP scraping with bounded retries, extraction and crawling."""

from collections import deque
from dataclasses import asdict, dataclass, field
from email.utils import parsedate_to_datetime
from email.message import Message
import json
import math
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests

from .extract import Field, extract_html, normalize_fields, normalize_items
from .profiles import BrowserProfile, profile


def _url(value):
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        raise ValueError("URL must be an HTTP(S) URL without control characters")
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise ValueError("URL must use HTTP(S), have a host, and omit embedded credentials")
        port = parts.port
    except ValueError as exc:
        raise ValueError("Invalid HTTP(S) URL") from exc
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port is not None and port != (443 if parts.scheme == "https" else 80):
        host += f":{port}"
    return urlunsplit((parts.scheme, host, parts.path or "/", parts.query, ""))


def _origin(url):
    parts = urlsplit(url)
    return parts.scheme + "://" + parts.netloc


@dataclass
class ScrapeResult:
    url: str
    status_code: int = 0
    title: str = ""
    text: str = ""
    links: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    structured_data: list = field(default_factory=list)
    fields: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    error: str | None = None
    needs_render: bool = False
    missing_fields: list = field(default_factory=list)
    next_url: str | None = None
    rendered: bool = False
    attempts: int = 0
    elapsed: float = 0
    data: object = None
    items: list = field(default_factory=list)
    item_errors: list = field(default_factory=list)
    items_missing: bool = False

    @property
    def ok(self):
        return (self.error is None and 200 <= self.status_code < 300
                and not self.missing_fields and not self.item_errors and not self.items_missing)

    def to_dict(self):
        return {**asdict(self), "ok": self.ok}


class _FetchError(Exception):
    def __init__(self, code, url, status=0, attempts=0):
        self.code, self.url, self.status, self.attempts = code, url, status, attempts


class Scraper:
    """Reuse connections, cookies and one browser identity across a scraping job.

    Use as a context manager. Instances are synchronous and not thread-safe.
    Rendering is optional and requires the render extra plus installed browsers.
    """

    def __init__(self, *, browser="chrome", os_type=None, browser_profile=None,
                 timeout=20, retries=2, delay=0.5, max_bytes=5_000_000,
                 max_retry_wait=30, respect_robots=True, render="never", client_hints=False):
        for name, value in (("timeout", timeout), ("delay", delay), ("max_retry_wait", max_retry_wait)):
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or
                    not math.isfinite(value) or value < 0 or (name == "timeout" and value == 0)):
                raise ValueError(f"{name} must be a finite {'positive' if name == 'timeout' else 'non-negative'} number")
        for name, value, minimum in (("retries", retries, 0), ("max_bytes", max_bytes, 1)):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if not isinstance(respect_robots, bool) or not isinstance(client_hints, bool):
            raise ValueError("respect_robots and client_hints must be booleans")
        if render not in ("never", "auto", "always"):
            raise ValueError("render must be never, auto, or always")
        if browser_profile is not None and not isinstance(browser_profile, BrowserProfile):
            raise ValueError("browser_profile must be a PageHarvest BrowserProfile")
        # Offline profiles keep construction quick and avoid an unrelated API call.
        self.profile = browser_profile or profile(os_type, browser=browser, offline=True)
        self.timeout, self.retries, self.delay = timeout, retries, delay
        self.max_bytes, self.max_retry_wait = max_bytes, max_retry_wait
        self.respect_robots, self.render, self.client_hints = respect_robots, render, client_hints
        self.session = requests.Session()
        self.session.headers.update(self.profile.headers())
        self._last_request = {}
        self._robots = {}
        self._renderer = None
        self._closed = False
        self._credential_origin = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if not self._closed:
            self.session.close()
            if self._renderer is not None:
                self._renderer.close()
            self._closed = True

    def _pace(self, url):
        origin = _origin(url)
        interval = self.delay
        robots = self._robots.get(origin)
        if isinstance(robots, RobotFileParser):
            interval = max(interval, robots.crawl_delay("PageHarvest") or 0)
        last = self._last_request.get(origin)
        if last is not None:
            time.sleep(max(0, interval - (time.monotonic() - last)))
        self._last_request[origin] = time.monotonic()

    def _allowed(self, url):
        if not self.respect_robots:
            return True
        origin = _origin(url)
        if origin not in self._robots:
            try:
                status, _, content, _, _ = self._fetch(origin + "/robots.txt", origin, check_robots=False)
                if status in (404, 410):
                    self._robots[origin] = True
                elif 200 <= status < 300:
                    parser = RobotFileParser()
                    parser.parse(content.decode("utf-8", errors="replace").splitlines())
                    self._robots[origin] = parser
                else:
                    self._robots[origin] = False
            except _FetchError:
                self._robots[origin] = False
        rule = self._robots[origin]
        return rule.can_fetch("PageHarvest", url) if isinstance(rule, RobotFileParser) else rule

    def _fetch(self, url, allowed_origin=None, *, check_robots=True):
        attempts = 0
        initial_origin = self._credential_origin or _origin(url)
        for redirect in range(11):
            if allowed_origin is not None and _origin(url) != allowed_origin:
                raise _FetchError("off_origin_redirect", url, attempts=attempts)
            if check_robots and not self._allowed(url):
                raise _FetchError("robots_disallowed_or_unavailable", url, attempts=attempts)
            for retry in range(self.retries + 1):
                self._pace(url)
                attempts += 1
                headers = self.profile.headers(client_hints=self.client_hints and url.startswith("https://"))
                extra = {}
                if _origin(url) != initial_origin:
                    # Manual redirects must preserve Requests' credential isolation.
                    headers.update({"Authorization": None, "Cookie": None, "Proxy-Authorization": None})
                    extra["auth"] = ()
                try:
                    with self.session.get(url, headers=headers, timeout=self.timeout,
                                          allow_redirects=False, stream=True, **extra) as response:
                        status = response.status_code
                        retry_after = response.headers.get("Retry-After")
                        if status in (408, 429, 500, 502, 503, 504) and retry < self.retries:
                            wait = min(2 ** retry, self.max_retry_wait)
                            if retry_after:
                                try:
                                    wait = max(0, float(retry_after))
                                except ValueError:
                                    try:
                                        wait = max(0, parsedate_to_datetime(retry_after).timestamp() - time.time())
                                    except (ValueError, TypeError, OverflowError):
                                        pass
                            if not math.isfinite(wait) or wait > self.max_retry_wait:
                                raise _FetchError("retry_after_exceeds_limit", url, status, attempts)
                            response.close()
                            time.sleep(wait)
                            continue
                        if status in (301, 302, 303, 307, 308) and response.headers.get("Location"):
                            try:
                                url = _url(urljoin(url, response.headers["Location"]))
                            except ValueError:
                                raise _FetchError("invalid_redirect", url, status, attempts)
                            break
                        body = bytearray()
                        for chunk in response.iter_content(65536):
                            body.extend(chunk)
                            if len(body) > self.max_bytes:
                                raise _FetchError("response_too_large", url, status, attempts)
                        return status, dict(response.headers), bytes(body), url, attempts
                except requests.RequestException as exc:
                    if retry == self.retries:
                        raise _FetchError("network_" + type(exc).__name__, url, attempts=attempts) from exc
                    time.sleep(min(2 ** retry, self.max_retry_wait))
            else:
                raise _FetchError("retry_exhausted", url, attempts=attempts)
        raise _FetchError("too_many_redirects", url, attempts=attempts)

    def scrape(self, url, *, fields=None, items=None, wait_for=None, _allowed_origin=None):
        """Fetch a page and return extracted data or a structured failure result."""
        if self._closed:
            raise RuntimeError("Scraper is closed")
        url = _url(url)
        fields = normalize_fields(fields)
        items = normalize_items(items, fields)
        if wait_for is not None:
            Field(wait_for)  # Validate before network access.
        if self._credential_origin is None:
            self._credential_origin = _origin(url)
        started = time.monotonic()
        result = ScrapeResult(url)
        try:
            status, headers, body, final_url, attempts = self._fetch(url, _allowed_origin)
            result.url, result.status_code, result.attempts = final_url, status, attempts
            if not 200 <= status < 300:
                result.error = f"http_{status}"
                return result
            content_type = next((v for k, v in headers.items() if k.lower() == "content-type"), "").lower()
            if "application/json" in content_type or "+json" in content_type:
                try:
                    result.data = json.loads(body)
                except (ValueError, UnicodeError, RecursionError):
                    result.error = "invalid_json"
                if items is not None:
                    result.error = result.error or "items_require_html"
                if fields:
                    result.warnings.append("CSS fields apply to HTML, not JSON responses")
                    result.missing_fields = [name for name, spec in fields.items() if spec.required]
                return result
            if content_type and not any(kind in content_type for kind in ("text/html", "application/xhtml+xml", "text/plain")):
                result.error = "unsupported_content_type"
                return result
            if "text/plain" in content_type:
                if items is not None:
                    result.error = "items_require_html"
                message = Message()
                message["Content-Type"] = content_type
                charset = message.get_content_charset() or "utf-8"
                try:
                    result.text = body.decode(charset, errors="replace")
                except (LookupError, ValueError):
                    result.warnings.append("Unknown text charset; decoded as UTF-8")
                    result.text = body.decode("utf-8", errors="replace")
                result.missing_fields = [name for name, spec in fields.items() if spec.required]
                return result
            extracted = extract_html(body, final_url, fields, items)
            should_render = self.render == "always" or (self.render == "auto" and
                             (extracted["needs_render"] or extracted["missing_fields"]
                              or extracted["items_missing"] or extracted["item_errors"] or wait_for is not None))
            if should_render:
                if self._renderer is None:
                    from .render import Renderer
                    self._renderer = Renderer(self.profile.browser, self.timeout, self.max_bytes)
                try:
                    html, rendered_url, render_status = self._renderer.fetch(
                        final_url, self.session, wait_for=wait_for,
                        allowed_origin=_allowed_origin, allowed=self._allowed,
                    )
                    result.url = rendered_url
                    result.rendered = True
                    if render_status is not None:
                        result.status_code = render_status
                    if not 200 <= result.status_code < 300:
                        result.error = f"http_{result.status_code}"
                        return result
                    extracted = extract_html(html, rendered_url, fields, items)
                    extracted["warnings"].append("Rendered with a real browser engine using its native user agent")
                except RuntimeError as exc:
                    result.error = "render_failed"
                    extracted["warnings"].append(str(exc))
            for key, value in extracted.items():
                setattr(result, key, value)
            return result
        except _FetchError as exc:
            result.url, result.status_code = exc.url, exc.status
            result.attempts, result.error = exc.attempts, exc.code
            return result
        finally:
            result.elapsed = round(time.monotonic() - started, 3)

    def scrape_many(self, urls, *, fields=None, items=None, wait_for=None):
        """Yield results sequentially, retaining session cookies and pacing."""
        for url in urls:
            yield self.scrape(url, fields=fields, items=items, wait_for=wait_for)

    def crawl(self, start_url, *, max_pages=10, max_depth=2, fields=None, items=None,
              pagination_only=False, wait_for=None):
        """Bounded same-origin crawl, or follow only rel=next pagination links.

        max_pages counts attempted pages including failures, excluding robots and
        retries. Fragments are removed; query strings remain significant.
        """
        for name, value, minimum in (("max_pages", max_pages, 1), ("max_depth", max_depth, 0)):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if not isinstance(pagination_only, bool):
            raise ValueError("pagination_only must be a boolean")
        start_url = _url(start_url)
        origin = _origin(start_url)
        queue = deque([(start_url, 0)])
        seen = {start_url}
        visited = set()
        count = 0
        while queue and count < max_pages:
            url, depth = queue.popleft()
            if url in visited:
                continue
            result = self.scrape(url, fields=fields, items=items, wait_for=wait_for, _allowed_origin=origin)
            count += 1
            visited.update((url, result.url))
            seen.add(result.url)
            yield result
            if result.error or depth >= max_depth:
                continue
            links = [result.next_url] if pagination_only else result.links
            for link in links:
                try:
                    normalized = _url(link)
                except ValueError:
                    continue
                if _origin(normalized) == origin and normalized not in seen:
                    seen.add(normalized)
                    queue.append((normalized, depth + 1))


def scrape(url, **options):
    """One URL to structured data; use Scraper for shared sessions and crawling."""
    fields = options.pop("fields", None)
    items = options.pop("items", None)
    wait_for = options.pop("wait_for", None)
    with Scraper(**options) as scraper:
        return scraper.scrape(url, fields=fields, items=items, wait_for=wait_for)
