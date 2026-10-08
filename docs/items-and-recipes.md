# Items and saved recipes

PageHarvest 1.1 adds one record per matching HTML container and reusable JSON
jobs. No API key or AI service is needed.

## Try the included catalog

From the repository root, start the local example server:

```sh
python -m http.server 8000 --bind 127.0.0.1 --directory docs/examples/site
```

In another terminal, run:

```sh
pageharvest --recipe docs/examples/products.recipe.json
```

This writes `docs/examples/products.csv`, with one row per product. The second
product has no price: its cell stays empty and the third product keeps its own
price. Stop the server with Ctrl+C.

To choose another destination and format:

```sh
pageharvest --recipe docs/examples/products.recipe.json --output products.json --format json
```

## Extract repeated items

```python
from pageharvest import Field, scrape, export_csv

page = scrape("http://127.0.0.1:8000/catalog.html", items=".product", fields={
    "name": Field("h2", required=True),
    "price": Field(".price"),
    "id": Field(":scope", attr="data-id"),
    "link": Field("a", attr="href"),
})
export_csv(page.items, "products.csv", columns=["name", "price", "id", "link"])
print(page.ok, page.item_errors)
```

Selectors run independently inside each item. `:scope` selects the container
itself. Attribute values stay as written in HTML, including relative links.
Use `many=True` for a list within each record. Table rows work the same way:
`items="tbody tr"` with fields such as `Field("td:nth-child(2)")`.

Missing optional values become `None` (or `[]` with `many=True`). Missing required
values set `page.ok` to false and appear in `page.item_errors`, with a zero-based
item index and missing field names. Incomplete records remain in `page.items`.
No matching containers sets `page.items_missing` and makes the page unsuccessful.
Item extraction requires HTML; JSON and plain text return `items_require_html`.

`Scraper.scrape_many` and `Scraper.crawl` also accept `items` and `fields`.
With optional browser support installed, `render="auto"` can retry extraction
in a browser when items or required fields are missing. Use `wait_for` to wait
for a particular rendered selector.

## A saved job

```json
{
  "version": 1,
  "urls": ["http://127.0.0.1:8000/catalog.html"],
  "items": ".product",
  "fields": {
    "name": {"selector": "h2", "required": true},
    "price": ".price",
    "id": {"selector": ":scope", "attr": "data-id"}
  },
  "scraper": {"delay": 0.5},
  "output": {"path": "products.csv", "format": "csv", "mode": "items"}
}
```

`version` and a nonempty `urls` list are required. `items` requires at least one
field. Fields accept CSS strings or objects with `selector`, `attr`, `many`,
and `required`. Unknown options and duplicate JSON keys are rejected.

Optional settings:

| Setting | Meaning |
| --- | --- |
| `scraper` | `browser`, `os_type`, `timeout`, `retries`, `delay`, `max_bytes`, `max_retry_wait`, `respect_robots`, `render`, `client_hints` |
| `crawl` | Object with `max_pages`, `max_depth`, `pagination_only`; requires exactly one seed URL |
| `wait_for` | CSS selector; requires `scraper.render` set to `auto` or `always` |
| `output.path` | File to overwrite; parent directory must already exist |
| `output.format` | `json` or `csv`; defaults to CSV for a configured `.csv` path, otherwise JSON |
| `output.mode` | `items` for flat records or `pages` for full results; defaults to items when an item selector is supplied |

Omit `items` for an ordinary page extraction recipe. Omit `output.path` for JSON
on CLI stdout. CSV requires a path. Recipe paths resolve relative to the recipe
file; a CLI `--output` override resolves relative to your current directory.
An override does not change the format automatically: use `--format` as well.
Only `--output` and `--format` may accompany `--recipe`.

Python jobs retain complete results for diagnostics:

```python
from pageharvest import run_recipe

pages = run_recipe("docs/examples/products.recipe.json", output="products.csv")
for page in pages:
    print(page.url, page.ok, page.item_errors)
```

## Direct terminal extraction

```sh
pageharvest http://127.0.0.1:8000/catalog.html --items .product --field name=h2 --format csv -o names.csv
```

CLI `--field` values are required text fields. Use recipes or Python for optional
fields and attributes. Add `--pages` for complete JSON results, including source
URLs and item diagnostics. Flat item exports contain only the requested fields.
Page-mode CSV retains the existing page columns; use JSON for nested items.

Exit status is 0 for success, 1 for fetch/extraction failures, and 2 for invalid
configuration or runtime setup errors. Partial data is still exported; check
the exit status or `page.ok` before treating a job as complete.
