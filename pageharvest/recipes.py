"""Validated JSON recipes: repeatable jobs without executable configuration."""

from dataclasses import dataclass
import json
from pathlib import Path

from .export import export_csv, export_json
from .extract import Field, normalize_fields, normalize_items
from .scraper import Scraper, _url


def _keys(value, allowed, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    unknown = value.keys() - allowed
    if unknown:
        raise ValueError(f"Unknown {label} option(s): {', '.join(sorted(unknown))}")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate recipe key: {key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"Invalid JSON number: {value}")


@dataclass
class Recipe:
    """A loaded recipe; run() returns page results and writes configured output."""

    urls: list
    fields: dict
    items: str | None
    scraper: dict
    crawl: dict | None
    wait_for: str | None
    output: Path | None
    format: str
    mode: str

    @classmethod
    def load(cls, path):
        """Load strict schema version 1; output paths are recipe-relative."""
        path = Path(path)
        with path.open(encoding="utf-8-sig") as stream:
            data = json.load(stream, object_pairs_hook=_unique_pairs, parse_constant=_invalid_constant)
        _keys(data, {"version", "urls", "items", "fields", "scraper", "crawl", "wait_for", "output"}, "recipe")
        if type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("Recipe version must be 1")
        urls = data.get("urls")
        if not isinstance(urls, list) or not urls:
            raise ValueError("Recipe urls must be a non-empty list")
        urls = [_url(url) for url in urls]
        fields = data.get("fields", {})
        if not isinstance(fields, dict):
            raise ValueError("Recipe fields must be a JSON object")
        parsed_fields = {}
        for name, value in fields.items():
            if isinstance(value, dict):
                _keys(value, {"selector", "attr", "many", "required"}, f"field {name}")
                if "selector" not in value:
                    raise ValueError(f"Field {name} needs a selector")
                value = Field(**value)
            parsed_fields[name] = value
        fields = normalize_fields(parsed_fields)
        items = normalize_items(data.get("items"), fields)
        options = data.get("scraper", {})
        _keys(options, {"browser", "os_type", "timeout", "retries", "delay", "max_bytes",
                       "max_retry_wait", "respect_robots", "render", "client_hints"}, "scraper")
        # Constructor validation is offline and creates no browser or cache files.
        with Scraper(**options):
            pass
        wait_for = data.get("wait_for")
        if wait_for is not None:
            Field(wait_for)
            if options.get("render", "never") == "never":
                raise ValueError("Recipe wait_for requires render auto or always")
        crawl = data.get("crawl")
        if crawl is not None:
            _keys(crawl, {"max_pages", "max_depth", "pagination_only"}, "crawl")
            if len(urls) != 1:
                raise ValueError("A crawl recipe requires exactly one seed URL")
            for key, minimum in (("max_pages", 1), ("max_depth", 0)):
                if key in crawl and (type(crawl[key]) is not int or crawl[key] < minimum):
                    raise ValueError(f"{key} must be an integer >= {minimum}")
            if "pagination_only" in crawl and not isinstance(crawl["pagination_only"], bool):
                raise ValueError("pagination_only must be a boolean")
        output = data.get("output", {})
        _keys(output, {"path", "format", "mode"}, "output")
        destination = output.get("path")
        if destination is not None:
            if not isinstance(destination, str) or not destination.strip():
                raise ValueError("output.path must be a non-empty string")
            destination = path.resolve().parent / destination
        format = output.get("format", "csv" if destination and destination.suffix.lower() == ".csv" else "json")
        mode = output.get("mode", "items" if items is not None else "pages")
        if format not in ("csv", "json") or mode not in ("items", "pages"):
            raise ValueError("output requires format json/csv and mode items/pages")
        if mode == "items" and items is None:
            raise ValueError("output.mode items requires an items selector")
        return cls(urls, fields, items, dict(options), crawl, wait_for, destination, format, mode)

    def records(self, results):
        """Select records to export; incomplete items are retained."""
        return [item for page in results for item in page.items] if self.mode == "items" else results

    def run(self, *, output=None, format=None):
        """Run, export when configured, and return full page results for diagnostics.

        Explicit output overrides are relative to the caller's working directory.
        An output override's suffix does not change the recipe's format.
        """
        destination = Path(output) if output is not None else self.output
        format = self.format if format is None else format
        if format not in ("csv", "json"):
            raise ValueError("format must be json or csv")
        if format == "csv" and destination is None:
            raise ValueError("CSV recipes require an output path")
        if destination is not None and (not destination.parent.is_dir() or destination.is_dir()):
            raise ValueError("Output must be a file in an existing directory")
        with Scraper(**self.scraper) as scraper:
            if self.crawl is not None:
                results = list(scraper.crawl(self.urls[0], fields=self.fields, items=self.items,
                                            wait_for=self.wait_for, **self.crawl))
            else:
                results = list(scraper.scrape_many(self.urls, fields=self.fields,
                                                 items=self.items, wait_for=self.wait_for))
        if destination is not None:
            records = self.records(results)
            if format == "csv":
                export_csv(records, destination, columns=list(self.fields) if self.mode == "items" else None)
            else:
                export_json(records, destination)
        return results


def run_recipe(path, *, output=None, format=None):
    """Run a saved JSON recipe and return its full page results."""
    return Recipe.load(path).run(output=output, format=format)
