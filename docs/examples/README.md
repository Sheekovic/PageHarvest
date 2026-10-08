# Runnable examples

Run from the repository root after `python -m pip install .`.

## Offline browser profiles

```sh
python docs/examples/browser_profiles.py
```

Prints Chrome, Edge, Firefox and Safari profiles without network access.

## Scrape URLs and export

```sh
python docs/examples/scrape_urls.py https://example.com --output pages.json
python docs/examples/scrape_urls.py https://example.com --output pages.csv
```

Add `--render auto --wait-for "main"` when browser rendering is installed and
the page needs JavaScript. Use a selector appropriate to that page.

## Local demo site

```sh
python -m http.server 8000 --bind 127.0.0.1 --directory docs/examples/site
```

In another terminal:

```sh
python docs/examples/scrape_urls.py http://127.0.0.1:8000/ http://127.0.0.1:8000/page2.html --output pages.json
python -m pageharvest http://127.0.0.1:8000/ --crawl --max-pages 2 -o crawl.json
```

The demo includes HTML products, JSON-LD, repeated tags, next-page links, a JSON
endpoint, and a page with delayed JavaScript content. See [recipes](../recipes.md)
for extraction/rendering commands. Stop the server with Ctrl+C. Generated files
are written to your current directory and overwrite existing files of that name.
