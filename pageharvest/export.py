"""JSON and CSV exports with stable columns and no hidden filesystem writes."""

import csv
import json
from pathlib import Path


def export_json(results, path):
    """Write results as a UTF-8 JSON array (overwrites the specified file)."""
    Path(path).write_text(json.dumps([r.to_dict() for r in results], ensure_ascii=False, indent=2)
                          + "\n", encoding="utf-8")


def export_csv(results, path):
    """Write base columns plus field:<name>; nested values become JSON cells.

    Spreadsheet formula prefixes in scraped values are escaped with an apostrophe.
    """
    results = list(results)
    names = list(dict.fromkeys(name for result in results for name in result.fields))
    columns = ["url", "status_code", "ok", "title", "text", "error"] + ["field:" + n for n in names]

    def cell(value):
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for result in results:
            row = {name: getattr(result, name) for name in columns[:6]}
            row.update({"field:" + name: result.fields.get(name) for name in names})
            writer.writerow({name: cell(value) for name, value in row.items()})
