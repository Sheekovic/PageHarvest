"""JSON and CSV exports with stable columns and no hidden filesystem writes."""

import csv
from collections.abc import Mapping
import json
from pathlib import Path


def export_json(results, path):
    """Write results as a UTF-8 JSON array (overwrites the specified file)."""
    Path(path).write_text(json.dumps([dict(r) if isinstance(r, Mapping) else r.to_dict()
                                    for r in results], ensure_ascii=False, indent=2)
                          + "\n", encoding="utf-8")


def export_csv(results, path, *, columns=None):
    """Write page results or item dictionaries; nested values become JSON cells.

    Pages use base columns plus field:<name>. Items use their keys directly.
    Explicit columns also give empty item exports a stable header.
    Spreadsheet formula prefixes in scraped values are escaped with an apostrophe.
    """
    results = list(results)
    records = bool(results) and isinstance(results[0], Mapping)
    if any(isinstance(result, Mapping) != records for result in results):
        raise ValueError("CSV export cannot mix page results and item dictionaries")
    if records:
        rows = [dict(result) for result in results]
        inferred = list(dict.fromkeys(name for row in rows for name in row))
    else:
        names = list(dict.fromkeys(name for result in results for name in result.fields))
        base = ["url", "status_code", "ok", "title", "text", "error"]
        inferred = base + ["field:" + n for n in names]
        rows = []
        for result in results:
            row = {name: getattr(result, name) for name in base}
            row.update({"field:" + name: result.fields.get(name) for name in names})
            rows.append(row)
    if columns is not None:
        if (not isinstance(columns, (list, tuple))
                or any(not isinstance(name, str) or not name for name in columns)
                or len(set(columns)) != len(columns)):
            raise ValueError("columns must be a list or tuple of unique field names")
    columns = inferred if columns is None else list(columns)
    if any(not isinstance(name, str) or not name for name in columns):
        raise ValueError("CSV column names must be non-empty strings")

    def cell(value):
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writerow({name: cell(name) for name in columns})
        for row in rows:
            writer.writerow({name: cell(row.get(name)) for name in columns})
