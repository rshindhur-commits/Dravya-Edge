"""Parsing for the timestamp shapes this app actually writes.

`format_timestamp` in `app/main.py` renders with `%Z`, so the scanner's
`Current ET` column -- and everything derived from it, including
`activity_trace.csv` -- carries values like ``2026-07-31 00:38:19 EDT``. Pandas
parses those with a ``FutureWarning`` that says the unrecognized timezone is
dropped and *will raise* in a future version. Dropping it is already wrong: it
silently turns an Eastern instant into a naive one.

The writer is deliberately left alone. `Current ET` is consumed by candidate
evidence, quote attribution, recommendation outcomes and the candidate snapshot
writer, and years of archived CSVs already hold the abbreviation form, so the
reader has to understand it regardless. Changing the written format is a
separate, wider change than making reads correct.

Naive values are read as Eastern rather than UTC. That is what the column means
-- it is named `Current ET` -- and reading them as UTC would shift every one by
four hours.
"""

from __future__ import annotations

import re

import pandas as pd

ET_TZ = "America/New_York"

# Only the two the formatter can emit for a US/Eastern datetime.
ZONE_OFFSETS = {"EDT": "-04:00", "EST": "-05:00"}

_ABBREVIATION = re.compile(r"\s+(EDT|EST)\s*$", re.IGNORECASE)
_HAS_ZONE = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})\s*$")


def _normalise(text):
    """Swap a trailing zone abbreviation for the numeric offset it stands for."""
    return _ABBREVIATION.sub(
        lambda match: ZONE_OFFSETS[match.group(1).upper()], str(text).strip()
    )


def to_utc(value):
    """One timestamp as a UTC-aware pd.Timestamp, or NaT."""
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return pd.NaT

    if isinstance(value, pd.Timestamp):
        return value.tz_localize(ET_TZ).tz_convert("UTC") if value.tz is None \
            else value.tz_convert("UTC")

    text = _normalise(value)
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return pd.NaT

    if parsed.tz is None:
        return parsed.tz_localize(ET_TZ, ambiguous=True, nonexistent="shift_forward") \
            .tz_convert("UTC")
    return parsed.tz_convert("UTC")


def to_utc_series(values):
    """A column of mixed timestamp shapes as UTC-aware datetimes.

    Zone-aware and naive values are parsed separately: passing ``utc=True`` over
    the whole column would read the naive ones as UTC and move them four hours.
    """
    text = pd.Series(values).astype(str).str.strip()
    normalised = text.map(_normalise)
    zoned = normalised.str.contains(_HAS_ZONE, na=False)

    parsed = pd.Series(pd.NaT, index=normalised.index, dtype="datetime64[ns, UTC]")

    if zoned.any():
        parsed.loc[zoned] = pd.to_datetime(
            normalised[zoned], errors="coerce", utc=True, format="mixed"
        )

    if (~zoned).any():
        naive = pd.to_datetime(normalised[~zoned], errors="coerce", format="mixed")
        localised = naive.dt.tz_localize(
            ET_TZ, ambiguous=True, nonexistent="shift_forward"
        ).dt.tz_convert("UTC")
        parsed.loc[~zoned] = localised

    return parsed


def minutes_since(value, now=None):
    """Age of a timestamp in minutes, or None when it cannot be read."""
    stamp = to_utc(value)
    if pd.isna(stamp):
        return None
    now = now or pd.Timestamp.now(tz="UTC")
    return (now - stamp).total_seconds() / 60.0
