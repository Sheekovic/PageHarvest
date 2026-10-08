"""Scrape URLs to JSON or CSV. Install PageHarvest before running this file."""
import argparse
from pathlib import Path

from pageharvest import Scraper, export_csv, export_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--output", default="pages.json")
    parser.add_argument("--render", choices=("never", "auto", "always"), default="never")
    parser.add_argument("--wait-for")
    args = parser.parse_args()
    suffix = Path(args.output).suffix.lower()
    if suffix not in (".json", ".csv"):
        parser.error("--output must end in .json or .csv")
    if args.wait_for and args.render == "never":
        parser.error("--wait-for needs --render auto or always")
    with Scraper(render=args.render) as scraper:
        results = list(scraper.scrape_many(args.urls, fields={"heading": "h1"},
                                           wait_for=args.wait_for))
    (export_csv if suffix == ".csv" else export_json)(results, args.output)
    for result in results:
        print(f"{result.status_code} {result.url}: {'OK' if result.ok else result.error or 'missing fields'}")
    print(f"Saved {len(results)} result(s) to {args.output}")
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
