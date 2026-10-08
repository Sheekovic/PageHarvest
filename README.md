# PageHarvest

**Turn URLs into structured JSON or CSV, with one Python call or one terminal command.**

PageHarvest combines HTML/JSON extraction, browser profiles, persistent HTTP sessions,
bounded retries, polite crawling, and optional JavaScript rendering. It grew out of
ChromeMultiOSUA; the old `chrome_multi_os_ua` import still works.

**Start here:** [Documentation](https://github.com/Sheekovic/PageHarvest/tree/main/docs) ·
[Quick start](https://github.com/Sheekovic/PageHarvest/blob/main/docs/quickstart.md) ·
[Practical recipes](https://github.com/Sheekovic/PageHarvest/blob/main/docs/recipes.md) ·
[Runnable examples](https://github.com/Sheekovic/PageHarvest/tree/main/docs/examples)

## Install

Python 3.10 or newer:

```sh
python -m pip install pageharvest
```

To work with the source and included examples:

```sh
git clone https://github.com/Sheekovic/PageHarvest.git
cd PageHarvest
python -m pip install .
```

For JavaScript pages, install the optional browser support:

```sh
python -m pip install "pageharvest[render]"
python -m playwright install chromium
```

## One URL to useful data

```python
from pageharvest import scrape

page = scrape("https://example.com")
print(page.title)
print(page.text)
print(page.links)
print(page.structured_data)  # JSON-LD, when present
print(page.ok, page.error)
```

HTML extraction automatically finds the title, metadata, main/article text,
absolute links, JSON-LD blocks, and rel=next pagination. JSON endpoints return
their decoded response in `page.data`. No API key or language model is required.

## Extract exactly what you need

```python
from pageharvest import Field, scrape

page = scrape("https://example.com/products", fields={
    "heading": Field("h1", required=True),
    "prices": Field(".price", many=True),
    "product_links": Field(".product a", attr="href", many=True),
})

print(page.fields)
print(page.missing_fields)
```

A CSS string is shorthand for `Field(selector)`. Text is stripped and joined
with spaces; attribute values are returned as written in the HTML.
Missing optional fields become `None` or `[]`; missing required fields make
`page.ok` false. Invalid selectors fail before network access.

## Sessions, crawling and export

```python
from pageharvest import Scraper, export_csv, export_json

with Scraper(delay=1) as scraper:
    pages = list(scraper.scrape_many([
        "https://example.com/one",
        "https://example.com/two",
    ]))
    export_json(pages, "pages.json")
    export_csv(pages, "pages.csv")

    # Same-origin, breadth-first crawl. Failed pages count toward max_pages.
    pages = list(scraper.crawl(
        "https://example.com", max_pages=20, max_depth=2
    ))
```

Use `pagination_only=True` to follow only `rel=next` links. Crawling removes
fragments, deduplicates URLs, preserves query strings, and refuses cross-origin
redirects. Export functions overwrite the named file. CSV contains
URL/status/title/text/error plus `field:<name>` columns; use JSON for complete
metadata, links, JSON-LD and JSON response bodies. Spreadsheet formula prefixes
in CSV values are escaped.

## Terminal usage

```sh
pageharvest https://example.com
pageharvest https://example.com --field "heading=h1" -o page.json
pageharvest https://example.com --crawl --max-pages 10 -o pages.json
pageharvest https://example.com --field "title=h1" --format csv -o pages.csv
pageharvest https://example.com --render auto --wait-for "main" -o rendered.json
pageharvest https://example.com --browser firefox
```

`python -m pageharvest` works too. JSON goes to stdout by default; the summary
goes to stderr. Exit codes: 0 for success, 1 for page/extraction failures,
2 for invalid options or output errors, 130 for interruption. CLI fields are
required. See `pageharvest --help`.

## What makes it smart?

- **Adaptive retries:** retries connection failures and 408/429/500/502/503/504.
  Honors numeric/date Retry-After. If the requested wait exceeds the configured
  limit, returns a failure rather than retrying early. Does not retry 401/403.
- **Content-aware extraction:** handles HTML, JSON and plain text; reports
  unsupported responses, malformed JSON and missing required fields.
- **Rendering hints:** flags sparse pages with application/script markers.
  This heuristic is not proof that JavaScript is needed.
- **Optional rendering:** `render="auto"` invokes a browser for sparse HTML,
  missing required fields, or an explicit `wait_for`. `render="always"` renders
  successful HTML responses. Default `render="never"` keeps installation small.
- **Session continuity:** reuses connections and cookies with one fixed HTTP
  profile. Browser rendering exchanges domain-scoped cookies with that session.
- **Predictable limits:** caps page count, crawl depth, redirects, retries,
  HTTP body size and per-origin request rate; returns errors as data.

For dynamic pages, supply the selector that signals readiness:

```python
page = scrape(
    "https://example.com/app",
    render="auto",
    wait_for="#results .item",
    fields={"items": Field("#results .item", many=True, required=True)},
)
```

Rendering uses the real installed browser's native UA, not the synthetic HTTP
profile. Chrome/Edge select Chromium, Firefox selects Firefox, and Safari
selects WebKit. Install the corresponding Playwright engine. WebKit is not the
Safari application, and desktop rendering does not emulate a mobile device.
Without `wait_for`, readiness means DOM content loaded plus non-empty body text;
apps with delayed updates may need an explicit selector.

## Browser profiles

```python
from pageharvest import profile, ua

print(ua("android", offline=True))
print(ua(browser="firefox"))
print(ua(browser="safari"))  # Defaults to macOS

identity = profile("windows", browser="edge", version="154.0.4258.62")
print(identity.user_agent)
print(identity.version_source, identity.is_stale)
headers = identity.headers()  # User-Agent only
https_headers = identity.headers(client_hints=True)
```

| Browser | Supported profiles | Default version source |
| --- | --- | --- |
| Chrome | Windows, macOS, Linux, Android phone, iPhone | Live Google API, cache, then verified bundled snapshot |
| Edge | Windows, macOS, Linux | Verified official release snapshot |
| Firefox | Windows, macOS, Linux, Android phone | Verified official release snapshot |
| Safari | macOS, iPhone | Historical Safari 18.6 profile |

Unsupported combinations raise a clear error. Pin `version=` for reproducible
profiles. Chrome/Edge require four numeric components; Firefox/Safari accept
two to four. Firefox's UA reports major.0. Chrome desktop/Android UAs are reduced.

The scraper uses bundled profiles by default, so construction does not depend on
external version services. Supply `browser_profile=profile(...)` to choose live
Chrome lookup or a pinned identity.

Client Hints are optional low-entropy brand/version/platform/mobile headers for
Chrome and Edge over HTTPS. Firefox, Safari and iOS profiles omit them. They do
not model browser GREASE ordering, high-entropy hints, TLS or JavaScript
fingerprints. No browser profile guarantees compatibility with every website.

## Offline versions and persistence

```python
identity = profile("linux", cache_dir=".pageharvest-cache")
offline = profile("linux", offline=True, cache_dir=".pageharvest-cache")
```

Chrome caches successful versions for one hour and failures for 60 seconds.
Disk caching is opt-in, per-platform and endpoint-specific, uses atomic writes,
and persists only successful lookups. Offline mode reuses disk data, then falls
back to a bundled per-platform snapshot. Corrupt/unwritable caches do not prevent
generation. No directories are created until a successful lookup is saved.

Profiles record `version_source` (live/cached/bundled/pinned),
`checked_at` (Unix timestamp or None), and `is_stale` at creation time.
A pinned version is treated as intentional, not fresh-from-network.
Bundled Firefox/Edge data was checked on 2026-10-08; Safari is explicitly
historical. These versions age and are not silently described as current.
The legacy generator additionally supports an explicit custom fallback.

## Defaults and boundaries

| Setting | Default |
| --- | --- |
| `timeout` | 20 seconds per Requests connect/read timeout |
| `retries` | 2 retries after the initial attempt |
| `delay` | 0.5 seconds between requests to an origin |
| `max_retry_wait` | 30 seconds |
| `max_bytes` | 5 MB of decoded HTTP body / final rendered HTML |
| `respect_robots` | True |
| `render` | "never" |
| `client_hints` | False |

Robots rules use the PageHarvest token, including wildcard rules and Crawl-delay.
404/410 robots responses allow fetching; denied/unavailable robots stop fetching.
Rules are cached for the scraper session. `respect_robots=False` (CLI:
`--ignore-robots`) explicitly overrides this behavior.

A Scraper is synchronous; use one instance per worker. Its public `session`
is a Requests Session for explicit proxy/auth/header configuration. Raw
Authorization/Cookie headers and session auth are restricted to the first
scraped origin; cookie-jar cookies retain their own domain scope.

HTTP timeouts are not a total job deadline. Browser resources are governed by
Playwright; max_bytes caps the final DOM, not all browser network traffic.
Rendering and robots requests add network operations beyond max_pages.
The tool does not solve login/CAPTCHA challenges, invent missing field values,
or automatically discover arbitrary pagination buttons.

## Compatibility and development

Existing usage remains valid:

```python
from chrome_multi_os_ua import UserAgentGenerator
generator = UserAgentGenerator(offline=True)
print(generator.generate_user_agent("windows"))
```

The old module also has `ua()`, `profile()`, `generate_profile()`, cache
provenance and optional disk caching. Explicit FALLBACK_CHROME_VERSION overrides
remain supported. See [legacy configuration](https://github.com/Sheekovic/PageHarvest/blob/main/docs/legacy-api.md).

```sh
python -m pip install .
python -m unittest discover -v
```

Tests use mocked version feeds and a real local HTTP fixture server. Browser
integration runs separately with `PAGEHARVEST_TEST_RENDER=1` after installing
Chromium. CI covers Windows/Linux and Python 3.10, 3.13 and 3.14, plus Chromium.

Protocol references:
[Chromium UA reduction](https://www.chromium.org/updates/ua-reduction/),
[Chrome Client Hints](https://developer.chrome.com/docs/privacy-security/user-agent-client-hints),
[Edge UA guidance](https://learn.microsoft.com/en-us/microsoft-edge/web-platform/user-agent-guidance),
[Firefox UA reference](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/User-Agent/Firefox).
