# Quick start

## 1. Install from GitHub

Use Python 3.10 or newer. Run these commands in PowerShell or your terminal:

```sh
git clone https://github.com/Sheekovic/PageHarvest.git
cd PageHarvest
python -m pip install .
```

Keep the following terminal commands in the repository directory. The package
is not published to PyPI; install from this checkout rather than a similarly
named third-party package.

## 2. Try the included local website

In a separate terminal, open the repository directory and start the demo site:

```sh
python -m http.server 8000 --bind 127.0.0.1 --directory docs/examples/site
```

Leave that terminal running. In your first terminal:

```sh
python -m pageharvest http://127.0.0.1:8000/ -o page.json
python -m pageharvest http://127.0.0.1:8000/ --field "name=h1" --field "price=.price" --format csv -o products.csv
```

Open `page.json` or `products.csv`. The demo title is `Demo book shop`, the
heading is `Practical Python`, and the price is `$19.95`.
Stop the server with Ctrl+C when you are finished.

## 3. Use Python

Save this as `my_scraper.py` and run `python my_scraper.py` while the demo server
is running:

```python
from pageharvest import Field, export_json, scrape

page = scrape("http://127.0.0.1:8000/", fields={
    "name": Field("h1", required=True),
    "price": Field(".price", required=True),
})

if page.ok:
    print(page.fields)
    print(page.structured_data)  # Product data embedded as JSON-LD
else:
    print("Fetch error:", page.error)
    print("Missing fields:", page.missing_fields)
    print("Details:", page.warnings)

export_json([page], "result.json")
```

## 4. Scrape your own URLs

Replace the local URL with the page you need. Adapt selectors to its HTML:
`h1` selects a heading, `.price` selects a class, and `#results` selects an ID.
You can omit fields to collect the title, main text, links, metadata and JSON-LD.

```sh
python -m pageharvest https://example.com -o result.json
python docs/examples/scrape_urls.py https://example.com --output pages.json
```

Output paths are relative to your current terminal directory. Existing output
files are overwritten. A successful HTTP response can still have missing
required fields; check `page.ok`, not just `page.status_code`.

## Troubleshooting

| Result | Next step |
| --- | --- |
| `ModuleNotFoundError: pageharvest` | Run `python -m pip install .` with the same Python used to run your script |
| `robots_disallowed_or_unavailable` | Inspect the site's robots.txt; disallowed or unavailable rules stop fetching |
| `http_401` / `http_403` | The server denied access; browser rendering is not a login or challenge bypass |
| `retry_after_exceeds_limit` | The server requested a longer wait than configured; retry later |
| Missing fields | Check selectors against the returned HTML; dynamic content may need rendering |
| `render_failed` | Install the render extra/browser and check `page.warnings` |

Continue with [recipes](recipes.md) for JavaScript pages, crawling, and profiles.
