"""Append-only CSV writes for files a scan adds to but never edits.

Several writers had grown the same shape: read the whole day's file, tack the
handful of new rows on the end, drop duplicates on an id, write the whole thing
back. That is quadratic in the number of scans, and the cost is paid in Python
objects rather than bytes -- a 700KB text file becomes tens of megabytes of
pandas object columns, rebuilt on every scan. Across the session it was the
largest single source of the worker's memory growth.

Every id involved is a sha256 of the fields its row is built from, so a repeated
id means a byte-identical row, and "keep the last" and "keep the first" describe
the same file. That is what makes the cheap version exact rather than
approximate: read back only the id column, append the rows whose ids are new.
"""

from __future__ import annotations

import pandas as pd


def _row_keys(frame, key_columns):

    return [
        tuple(str(value) for value in row)
        for row in frame[key_columns].itertuples(index=False, name=None)
    ]


def existing_keys(path, key_columns):
    """Keys already stored, plus the stored column order.

    The column order comes back so the caller can tell a file written by an
    older build from one it can safely append to. It is None when no file
    exists yet, which is also the signal to write a header.
    """
    if not path.exists() or not path.stat().st_size:

        return set(), None

    try:

        with open(path, "r", encoding="utf-8") as handle:
            header = handle.readline().strip()

        columns = header.split(",") if header else []

        if not columns or not all(column in columns for column in key_columns):

            return set(), columns

        stored = pd.read_csv(path, usecols=list(key_columns))

        return set(_row_keys(stored, list(key_columns))), columns

    except Exception:

        # An unreadable file is repaired by the rewrite path, not guessed at.
        return set(), []


def append_new_rows(path, frame, key_columns):
    """Add the rows of `frame` whose keys are not already in `path`.

    Returns the number of rows written. Falls back to a full rewrite when the
    stored file has different columns, so a file left by an earlier build is
    repaired once rather than appended to with its columns misaligned.
    """
    if frame is None or frame.empty:

        return 0

    key_columns = list(key_columns)

    if not all(column in frame.columns for column in key_columns):

        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    keys, columns = existing_keys(path, key_columns)

    if columns is not None and columns != list(frame.columns):

        merged = pd.concat(
            [_read_csv(path), frame], ignore_index=True, sort=False
        ).drop_duplicates(key_columns, keep="last")
        merged.to_csv(path, index=False)

        return len(merged)

    if keys:

        frame = frame[[key not in keys for key in _row_keys(frame, key_columns)]]

    if frame.empty:

        return 0

    frame.to_csv(path, mode="a", header=columns is None, index=False)

    return len(frame)


def _read_csv(path):

    try:

        return (
            pd.read_csv(path)
            if path.exists() and path.stat().st_size
            else pd.DataFrame()
        )

    except Exception:

        return pd.DataFrame()
