# Practical recipes

Examples assume PageHarvest is installed. Local URLs refer to the demo server
from the [quick start](quickstart.md). Replace URLs and CSS selectors for your
own pages.

## Extract text, attributes, and repeated values

```python
from pageharvest import Field, scrape

page = scrape("http://127.0.0.1:8000/", fields={
    "name": "h1",  # Shorthand for Field("h1")
    "price": Field(".price", required=True),
    "tags": Field(".tag", many=True),
    "next_href": Field('a[rel="next"]', attr="href"),
})
print(page.fields)
```

Attribute values such as `next_href` remain as written in HTML. `page.links`
and `page.next_url` contain resolved absolute URLs. Use Python's
`urllib.parse.urljoin(page.url, href)` to resolve your own extracted attributes.

## Scrape several pages using one session

```python
from pageharvest import Scraper, export_csv, export_json

with Scraper(delay=1, retries=2, timeout=20) as scraper:
    pages = list(scraper.scrape_many([
        "http://127.0.0.1:8000/",
        "http://127.0.0.1:8000/page2.html",
    ], fields={"name": "h1", "price": ".price"}))

export_json(pages, "pages.json")
export_csv(pages, "pages.csv")
```

The session keeps cookies and connections. JSON retains the full result; CSV
contains basic columns and custom fields. Each result describes its own failure
without stopping the batch. Invalid URLs/options raise `ValueError` instead.

## Crawl or follow pagination

```python
from pageharvest import Scraper, export_json

with Scraper(delay=1) as scraper:
    pages = list(scraper.crawl(
        "http://127.0.0.1:8000/",
        max_pages=10,
        max_depth=2,
        fields={"name": "h1"},
        pagination_only=True,
    ))

export_json(pages, "catalog.json")
```

Set `pagination_only=False` to follow ordinary links on the same origin.
An origin includes the scheme, host, and port. Failed pages count toward
`max_pages`; retries and robots requests are additional network operations.
Pagination follows explicit `rel=next` links, not arbitrary buttons or infinite
scroll. The CLI equivalent is:

```sh
python -m pageharvest http://127.0.0.1:8000/ --crawl --pagination-only --max-pages 10 -o catalog.json
```

## Read a JSON endpoint

```python
from pageharvest import scrape

page = scrape("http://127.0.0.1:8000/products.json")
if page.ok:
    for product in page.data["products"]:
        print(product["name"], product["price"])
```

JSON responses populate `page.data`; embedded HTML JSON-LD populates
`page.structured_data`. CSS selectors apply only to HTML.

## Render JavaScript pages

Install browser support:

```sh
python -m pip install "pageharvest[render]"
python -m playwright install chromium
```

```python
from pageharvest import Field, scrape

page = scrape(
    "http://127.0.0.1:8000/dynamic.html",
    render="auto",
    wait_for="#loaded",
    fields={"heading": Field("#loaded", required=True)},
)
print(page.rendered, page.fields, page.error)
```

`auto` renders sparse HTML, missing required fields, or an explicit `wait_for`.
Choose a selector that appears when the content you need is ready. The browser
uses its own real UA; an HTTP profile does not make it a mobile emulator.
Firefox/WebKit need their corresponding Playwright browser downloads. The CLI:

```sh
python -m pageharvest http://127.0.0.1:8000/dynamic.html --render auto --wait-for "#loaded" --field "heading=#loaded"
```

## Choose a browser profile

```python
from pageharvest import Scraper, profile, ua

print(ua("android", offline=True))
print(ua(browser="firefox", offline=True))
print(ua(browser="safari", offline=True))  # Defaults to macOS

identity = profile("windows", browser="edge", version="154.0.4258.62")
with Scraper(browser_profile=identity) as scraper:
    page = scraper.scrape("http://127.0.0.1:8000/")
```

Profiles are immutable and reusable. The browser/platform matrix is in the
[README](../README.md#browser-profiles). Unsupported pairs fail instead of
inventing a UA. Firefox/Edge defaults are snapshots; Safari uses a historical
profile. Only Chrome has live version lookup. Synthetic headers do not emulate
TLS or JavaScript fingerprints.

## Use headers with another HTTP client

```python
import requests
from pageharvest import profile

identity = profile("windows", offline=True)
response = requests.get(
    "https://example.com",
    headers=identity.headers(client_hints=True),
    timeout=20,
)
response.raise_for_status()
```

Client Hints are optional and intended for HTTPS. Firefox, Safari and iOS
profiles omit them. For HTTP, use `identity.headers()` without hints. This
example uses Requests directly, so PageHarvest's robots, retries and pacing do
not apply.

## Reuse Chrome versions across runs

```python
from pageharvest import profile

online = profile("linux", cache_dir=".pageharvest-cache")
offline = profile("linux", offline=True, cache_dir=".pageharvest-cache")
print(offline.version, offline.version_source, offline.is_stale)
```

The online call may fetch Google's public version API. Successful results are
saved atomically; offline mode reads the cache or uses the bundled snapshot.
Freshness metadata describes the profile at creation time. `version=` pins a
known version without requesting an update.

## Configure a session explicitly

```python
from pageharvest import Scraper

with Scraper(delay=1) as scraper:
    scraper.session.headers["Accept-Language"] = "en-US,en;q=0.9"
    page = scraper.scrape("http://127.0.0.1:8000/")
```

The public `session` is a Requests Session. Raw auth/cookie headers and session
auth are restricted to the first scraped origin; cookie-jar cookies keep their
domain scope. Scraper instances are synchronous and should not be shared across
threads. Rendering transfers cookies, not custom Requests auth, proxies or
headers, into the browser context.

Cookie transfers preserve HttpOnly, Secure, expiry and SameSite attributes and
reconcile deletions in both directions. Requests stores SameSite metadata but
does not implement a browser's SameSite policy. Unscoped Requests cookies stay
in the HTTP jar; partitioned browser cookies are not flattened into that jar.
