"""Item alignment, recipe validation and end-to-end recipe workflows."""
import contextlib
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from pageharvest import Field, Recipe, Scraper, export_csv, export_json, run_recipe, scrape
from pageharvest.cli import main
from pageharvest.extract import extract_html
from test_pageharvest import Handler, ScrapingTests as _Fixture

# Reuse server setup without inheriting and rerunning the original test suite.
class ItemRecipeTests(unittest.TestCase):
    setUpClass = classmethod(_Fixture.setUpClass.__func__)
    tearDownClass = classmethod(_Fixture.tearDownClass.__func__)

    def setUp(self):
        Handler.hits.clear()

    def recipe_data(self):
        return {"version": 1, "urls": [self.base + "/items"], "items": ".product",
                "fields": {"name": {"selector": "h2", "required": True}, "price": ".price",
                           "href": {"selector": "a", "attr": "href"}},
                "scraper": {"delay": 0}, "output": {"path": "products.csv"}}

    def test_missing_prices_never_shift_records(self):
        page = scrape(self.base + "/items", delay=0, items=".product", fields={
            "name": Field("h2", required=True), "price": ".price", "link": Field("a", attr="href"),
            "id": Field(":scope", attr="data-id"), "tags": Field(".tag", many=True),
        })
        self.assertTrue(page.ok, page.to_dict())
        self.assertEqual(page.items, [
            {"name": "First", "price": None, "link": "/first", "id": "a", "tags": ["One", "Two"]},
            {"name": "Second", "price": "$20", "link": "/second", "id": "b", "tags": []},
        ])
        self.assertEqual(page.fields, {})
        self.assertFalse(page.items_missing)
        self.assertEqual(page.to_dict()["items"], page.items)

    def test_required_item_fields_keep_incomplete_records(self):
        page = scrape(self.base + "/items", delay=0, items=".product",
                      fields={"name": "h2", "price": Field(".price", required=True)})
        self.assertFalse(page.ok)
        self.assertEqual(len(page.items), 2)
        self.assertEqual(page.item_errors, [{"index": 0, "missing_fields": ["price"]}])
        self.assertEqual(page.missing_fields, [])

    def test_no_items_is_reported_as_extraction_failure(self):
        page = scrape(self.base, delay=0, items=".absent", fields={"name": "h2"})
        self.assertFalse(page.ok)
        self.assertTrue(page.items_missing)
        self.assertEqual(page.items, [])
        self.assertIn("No items matched", page.warnings[0])

    def test_selector_cannot_read_neighboring_item(self):
        page = scrape(self.base + "/items", delay=0, items=".product",
                      fields={"neighbor": ":scope + .product .price", "price": ".price"})
        self.assertEqual([item["neighbor"] for item in page.items], [None, None])
        self.assertIsNone(page.items[0]["price"])

    def test_table_rows_and_scope_selectors(self):
        result = extract_html('<table><tr><td>A</td><td>10</td></tr><tr><td>B</td><td>20</td></tr></table>',
                              self.base, {"name": Field(":scope > td:first-child"),
                                          "value": Field("td:nth-child(2)")}, "tr")
        self.assertEqual(result["items"], [{"name": "A", "value": "10"}, {"name": "B", "value": "20"}])

    def test_required_field_checks_returned_value(self):
        result = extract_html('<p class="name"> </p><p class="name">Later value</p>', self.base,
                              {"name": Field(".name", required=True)})
        self.assertEqual(result["fields"]["name"], "")
        self.assertEqual(result["missing_fields"], ["name"])

    def test_items_need_valid_selector_and_fields_before_network(self):
        for items, fields in (("[", {"name": "h2"}), (".product", {}), (False, {"name": "h2"})):
            with self.assertRaises(ValueError):
                scrape(self.base, items=items, fields=fields)
        self.assertFalse(Handler.hits)

    def test_items_reject_non_html(self):
        for path in ("/json", "/latin-text"):
            page = scrape(self.base + path, delay=0, items=".product", fields={"name": "h2"})
            self.assertEqual(page.error, "items_require_html")
            self.assertFalse(page.ok)

    def test_item_crawl_and_batch_preserve_records(self):
        with Scraper(delay=0) as scraper:
            pages = list(scraper.crawl(self.base + "/items", items=".product",
                         fields={"name": "h2", "price": Field(".price", required=True)}, pagination_only=True))
            self.assertEqual([len(page.items) for page in pages], [2, 1])
            self.assertFalse(pages[0].ok)
            self.assertTrue(pages[1].ok)
            pages = list(scraper.scrape_many([self.base + "/items", self.base + "/items-next"],
                                             items=".product", fields={"name": "h2"}))
            self.assertEqual([len(page.items) for page in pages], [2, 1])

    def test_item_exports_stable_columns_and_empty_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            records = [{"name": "First", "tags": ["a", "b"]}, {"name": "=unsafe", "price": "$20"}]
            export_csv(records, path / "items.csv")
            with (path / "items.csv").open(newline="") as stream:
                reader = csv.DictReader(stream)
                self.assertEqual(reader.fieldnames, ["name", "tags", "price"])
                rows = list(reader)
                self.assertEqual(rows[1]["name"], "'=unsafe")
                self.assertEqual(rows[0]["tags"], '["a", "b"]')
                self.assertEqual(rows[0]["price"], "")
            export_json(records, path / "items.json")
            self.assertEqual(json.loads((path / "items.json").read_text()), records)
            export_csv([], path / "empty.csv", columns=["name", "price"])
            self.assertEqual((path / "empty.csv").read_text().strip(), "name,price")
            with self.assertRaises(ValueError):
                export_csv(records, path / "bad.csv", columns=[["invalid"]])

    def test_recipe_run_relative_output_and_pagination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recipe.json"
            data = self.recipe_data()
            data["crawl"] = {"pagination_only": True, "max_pages": 2}
            path.write_text(json.dumps(data), encoding="utf-8")
            pages = run_recipe(path)
            self.assertEqual(len(pages), 2)
            with path.with_name("products.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row["name"] for row in rows], ["First", "Second", "Third"])
            self.assertEqual(rows[0]["price"], "")
            self.assertEqual(rows[1]["href"], "/second")

    def test_recipe_full_page_output_and_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recipe.json"
            data = self.recipe_data()
            data["output"] = {"mode": "pages", "format": "json"}
            path.write_text(json.dumps(data), encoding="utf-8")
            output = Path(directory) / "full.json"
            pages = run_recipe(path, output=output)
            self.assertEqual(json.loads(output.read_text())[0]["items"], pages[0].items)

    def test_recipe_rejects_bad_schema_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recipe.json"
            for replacement in ({"version": True}, {"version": 2}, {"url": self.base}, {"urls": []},
                                {"urls": ["file:///tmp/file"]}, {"fields": {"name": {"selector": "h2", "typo": 1}}},
                                {"fields": {"name": {}}}, {"items": "["}, {"scraper": {"timeout": -1}},
                                {"scraper": {"delay": "slow"}}, {"scraper": {"shell": "echo test"}},
                                {"crawl": {"max_pages": True}}, {"crawl": {"pagination_only": "yes"}},
                                {"crawl": {}, "urls": [self.base, self.base + "/items"]},
                                {"wait_for": ".product"}, {"output": {"format": "xml"}},
                                {"output": {"mode": "bad"}}, {"output": {"path": 7}},
                                {"items": None, "output": {"mode": "items"}}):
                data = self.recipe_data()
                data.update(replacement)
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                    run_recipe(path)
            for text in ('{"version":1,"version":2}', '{"version":NaN}'):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    Recipe.load(path)
        self.assertFalse(Handler.hits)

    def test_recipe_output_errors_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recipe.json"
            data = self.recipe_data()
            data["output"] = {"format": "csv"}
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                run_recipe(path)
            with self.assertRaises(ValueError):
                run_recipe(path, output=Path(directory) / "missing" / "output.csv")
        self.assertFalse(Handler.hits)

    def test_cli_items_and_recipe_output(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = main([self.base + "/items", "--items", ".product", "--field", "name=h2", "--delay", "0"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout.getvalue()), [{"name": "First"}, {"name": "Second"}])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recipe.json"
            data = self.recipe_data()
            data["fields"]["price"] = {"selector": ".price", "required": True}
            path.write_text(json.dumps(data), encoding="utf-8")
            output = Path(directory) / "override.json"
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--recipe", str(path), "-o", str(output), "--format", "json"]), 1)
            self.assertEqual(len(json.loads(output.read_text())), 2)
            self.assertFalse(path.with_name("products.csv").exists())
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(["--recipe", str(path), "--delay", "0"])

    @unittest.skipUnless(os.environ.get("PAGEHARVEST_TEST_RENDER") == "1", "Enable real browser integration explicitly")
    def test_rendering_can_supply_missing_items(self):
        result = scrape(self.base + "/js-items", delay=0, render="auto", wait_for=".product",
                        items=".product", fields={"name": Field("h2", required=True), "price": ".price"})
        self.assertTrue(result.ok, result.to_dict())
        self.assertTrue(result.rendered)
        self.assertEqual(result.items, [{"name": "Dynamic item", "price": "$40"}])


# Avoid unittest discovery treating the imported fixture class as another suite.
del _Fixture

if __name__ == "__main__":
    unittest.main()
