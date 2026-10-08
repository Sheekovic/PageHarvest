"""Optional Playwright adapter; imported only when rendering is requested."""

from urllib.parse import urlsplit


class Renderer:
    def __init__(self, browser, timeout, max_bytes):
        self.engine_name = {"chrome": "chromium", "edge": "chromium",
                            "firefox": "firefox", "safari": "webkit"}[browser]
        self.timeout, self.max_bytes = timeout, max_bytes
        self._playwright = self._browser = self._context = None

    def _start(self):
        if self._context is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError('Install rendering: pip install "pageharvest[render]"; '
                               'then python -m playwright install chromium') from exc
        try:
            self._playwright = sync_playwright().start()
            self._browser = getattr(self._playwright, self.engine_name).launch(headless=True)
            self._context = self._browser.new_context()
        except Exception as exc:
            self.close()
            raise RuntimeError(f"Could not start {self.engine_name}; run python -m playwright install "
                               f"{self.engine_name} ({type(exc).__name__})") from exc

    def fetch(self, url, session, *, wait_for=None, allowed_origin=None, allowed=None):
        self._start()
        page = None
        try:
            cookies = []
            for cookie in session.cookies:
                if cookie.domain:
                    cookies.append({"name": cookie.name, "value": cookie.value,
                                    "domain": cookie.domain, "path": cookie.path or "/",
                                    "secure": cookie.secure})
            if cookies:
                self._context.add_cookies(cookies)
            page = self._context.new_page()

            def route_request(route):
                request = route.request
                if request.is_navigation_request() and request.frame == page.main_frame:
                    parts = urlsplit(request.url)
                    origin = parts.scheme + "://" + parts.netloc
                    if (parts.scheme not in ("http", "https") or
                            (allowed_origin and origin != allowed_origin) or
                            (allowed is not None and not allowed(request.url))):
                        route.abort()
                        return
                route.continue_()

            page.route("**/*", route_request)
            response = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            if wait_for:
                page.wait_for_selector(wait_for, state="attached", timeout=self.timeout * 1000)
            else:
                # A finite readiness heuristic; explicit wait_for is more reliable.
                page.wait_for_function("document.body && document.body.innerText.trim().length > 0",
                                       timeout=self.timeout * 1000)
            html = page.content()
            if len(html.encode("utf-8")) > self.max_bytes:
                raise RuntimeError("Rendered HTML exceeds max_bytes")
            for cookie in self._context.cookies():
                session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"],
                                    path=cookie["path"], secure=cookie["secure"])
            return html, page.url, response.status if response else None
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Browser rendering failed ({type(exc).__name__}); "
                               "check browser installation, navigation rules, and wait_for") from exc
        finally:
            if page is not None:
                page.close()

    def close(self):
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
            self._context = self._browser = self._playwright = None
