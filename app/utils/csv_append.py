"""Shared safe-append helper for the daily evidence CSVs.

`write_header` only fires on an empty/missing file, so a column added after a
file already exists must not be written under a header that doesn't have it --
that misaligns every value for the rest of that day's rows. Introduced in S1.6
(app/analytics/trade_snapshot.py) and promoted here in S2.5 when the same
hazard reappeared for `paper_trade_events.csv`.
"""
from __future__ import annotations

import csv


def existing_header(path):

    """Read a CSV's own header row so an append can target it instead of a
    newer, wider column list. Returns None if the file can't be read (caller
    falls back to its own default column list -- appropriate only when the
    file doesn't exist yet, i.e. `write_header` is about to be True).
    """

    try:

        with path.open("r", newline="", encoding="utf-8") as handle:

            header = next(csv.reader(handle), None)

        return header or None

    except Exception:

        return None


def append_row(path, row, default_columns):

    """Append `row` (a dict) to the CSV at `path`, using the file's own header
    when one already exists, and `default_columns` only for a new file.
    """

    path.parent.mkdir(parents=True, exist_ok=True)

    write_header = not path.exists() or path.stat().st_size == 0
    columns = default_columns if write_header else (existing_header(path) or default_columns)

    with path.open("a", newline="", encoding="utf-8") as handle:

        writer = csv.DictWriter(handle, fieldnames=columns)

        if write_header:

            writer.writeheader()

        writer.writerow({column: row.get(column) for column in columns})

    return path
