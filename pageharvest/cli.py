"""Command-line URLs-to-JSON/CSV workflow."""
import argparse
import json
import sys

from .export import export_csv, export_json
from .scraper import Field, Scraper


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pageharvest", description="Turn URLs into structured JSON or CSV")
    parser.add_argument("urls", nargs="+", help="HTTP(S) URLs to scrape")
    parser.add_argument("-o", "--output", help="Output file; defaults to JSON on stdout")
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    parser.add_argument("--field", action="append", default=[], metavar="NAME=CSS",
                        help="Extract text with CSS; repeat for multiple fields")
    parser.add_argument("--browser", choices=["chrome", "edge", "firefox", "safari"], default="chrome")
    parser.add_argument("--os", dest="os_type", help="Defaults to mac for Safari, windows otherwise")
    parser.add_argument("--render", choices=["never", "auto", "always"], default="never")
    parser.add_argument("--wait-for", metavar="CSS")
    parser.add_argument("--crawl", action="store_true", help="Crawl one seed on the same origin")
    parser.add_argument("--pagination-only", action="store_true", help="Follow only rel=next links")
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--ignore-robots", action="store_true", help="Explicitly disable robots.txt checks")
    args = parser.parse_args(argv)
    if args.format == "csv" and not args.output:
        parser.error("CSV requires --output")
    if args.crawl and len(args.urls) != 1:
        parser.error("--crawl requires exactly one seed URL")
    if args.pagination_only and not args.crawl:
        parser.error("--pagination-only requires --crawl")
    if args.wait_for and (args.render == "never" or args.crawl):
        parser.error("--wait-for requires direct scraping with --render auto or always")
    fields = {}
    try:
        for spec in args.field:
            name, separator, selector = spec.partition("=")
            if not separator or not name.strip() or name in fields:
                raise ValueError("--field must be a unique NAME=CSS pair")
            fields[name] = Field(selector, required=True)
        with Scraper(browser=args.browser, os_type=args.os_type, delay=args.delay,
                     timeout=args.timeout, render=args.render,
                     respect_robots=not args.ignore_robots) as scraper:
            if args.crawl:
                results = list(scraper.crawl(args.urls[0], max_pages=args.max_pages,
                                            max_depth=args.max_depth, fields=fields,
                                            pagination_only=args.pagination_only))
            else:
                results = list(scraper.scrape_many(args.urls, fields=fields, wait_for=args.wait_for))
        if args.output:
            (export_csv if args.format == "csv" else export_json)(results, args.output)
        else:
            print(json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2))
        failures = sum(not result.ok for result in results)
        print(f"{len(results)} page(s), {failures} failed", file=sys.stderr)
        return 1 if failures else 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
