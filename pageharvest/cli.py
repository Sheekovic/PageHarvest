"""Command-line URLs-to-JSON/CSV workflow."""
import argparse
import json
import sys

from .export import export_csv, export_json
from .recipes import Recipe
from .scraper import Field, Scraper


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pageharvest", description="Turn URLs into structured JSON or CSV")
    parser.add_argument("urls", nargs="*", help="HTTP(S) URLs to scrape")
    parser.add_argument("--recipe", metavar="FILE", help="Run a saved JSON recipe")
    parser.add_argument("-o", "--output", help="Output file; defaults to JSON on stdout")
    parser.add_argument("--format", choices=["json", "csv"])
    parser.add_argument("--items", metavar="CSS", help="Extract one record per matching container")
    parser.add_argument("--pages", action="store_true", default=None,
                        help="Export full page results instead of just item records")
    parser.add_argument("--field", action="append", metavar="NAME=CSS",
                        help="Extract text with CSS; repeat for multiple fields")
    parser.add_argument("--browser", choices=["chrome", "edge", "firefox", "safari"])
    parser.add_argument("--os", dest="os_type", help="Defaults to mac for Safari, windows otherwise")
    parser.add_argument("--render", choices=["never", "auto", "always"])
    parser.add_argument("--wait-for", metavar="CSS")
    parser.add_argument("--crawl", action="store_true", default=None, help="Crawl one seed on the same origin")
    parser.add_argument("--pagination-only", action="store_true", default=None, help="Follow only rel=next links")
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--max-depth", type=int)
    parser.add_argument("--delay", type=float)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--ignore-robots", action="store_true", default=None,
                        help="Explicitly disable robots.txt checks")
    args = parser.parse_args(argv)
    if args.recipe:
        if args.urls or any(value is not None for name, value in vars(args).items()
                            if name not in ("urls", "recipe", "output", "format")):
            parser.error("--recipe accepts only --output and --format overrides")
    elif not args.urls:
        parser.error("Provide at least one URL or --recipe")
    try:
        if args.recipe:
            recipe = Recipe.load(args.recipe)
            results = recipe.run(output=args.output, format=args.format)
            records = recipe.records(results)
            destination = args.output if args.output is not None else recipe.output
        else:
            format = args.format or "json"
            render = args.render or "never"
            if format == "csv" and not args.output:
                parser.error("CSV requires --output")
            if args.crawl and len(args.urls) != 1:
                parser.error("--crawl requires exactly one seed URL")
            if args.pagination_only and not args.crawl:
                parser.error("--pagination-only requires --crawl")
            if args.wait_for and render == "never":
                parser.error("--wait-for requires --render auto or always")
            fields = {}
            for spec in args.field or []:
                name, separator, selector = spec.partition("=")
                if not separator or not name.strip() or name in fields:
                    raise ValueError("--field must be a unique NAME=CSS pair")
                fields[name] = Field(selector, required=True)
            with Scraper(browser=args.browser or "chrome", os_type=args.os_type,
                         delay=0.5 if args.delay is None else args.delay,
                         timeout=20 if args.timeout is None else args.timeout,
                         render=render, respect_robots=not args.ignore_robots) as scraper:
                if args.crawl:
                    results = list(scraper.crawl(
                        args.urls[0], max_pages=10 if args.max_pages is None else args.max_pages,
                        max_depth=2 if args.max_depth is None else args.max_depth,
                        fields=fields, items=args.items, wait_for=args.wait_for,
                        pagination_only=bool(args.pagination_only),
                    ))
                else:
                    results = list(scraper.scrape_many(args.urls, fields=fields,
                                   items=args.items, wait_for=args.wait_for))
            item_mode = args.items is not None and not args.pages
            records = [item for result in results for item in result.items] if item_mode else results
            destination = args.output
            if destination:
                if format == "csv":
                    export_csv(records, destination, columns=list(fields) if item_mode else None)
                else:
                    export_json(records, destination)
        if destination is None:
            print(json.dumps([record.to_dict() if hasattr(record, "to_dict") else record
                              for record in records], ensure_ascii=False, indent=2))
        failures = sum(not result.ok for result in results)
        print(f"{len(results)} page(s), {failures} failed", file=sys.stderr)
        for result in results:
            if not result.ok:
                detail = result.error or "; ".join(result.warnings) or "Extraction failed"
                print(f"{result.url}: {detail}", file=sys.stderr)
        return 1 if failures else 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
