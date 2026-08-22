"""Fill `daily_engine_summary.avg_exit_confidence` for days already recorded.

The column has been NULL on every row it has ever written: the mean was taken
over a frame that never carried the score. Fixing the write path only helps days
that have not happened yet, and the learning page reads history -- so the days
already closed are backfilled here from the same source the fixed path now uses,
`paper_trades.payload->>'last_exit_confidence_score'`.

Recomputes nothing else. Rebuilding the whole summary would pull from data/daily
CSVs that are ephemeral on Streamlit Cloud and mostly absent, so a full rebuild
would overwrite good columns with blanks. This touches the one column and the one
key in `payload` that mirrors it.

Idempotent: re-running writes the same values. `--apply` is required; without it
this only reports.

One row is not a backfill. 2026-07-31 holds 85.0, written while the argument was
still `entry_exit_v2_shadow.csv` -- the **V2 shadow engine's** confidence, not the
live engine's. That is a different metric, not a stale value, and the live figure
for the same day is 16.75. Left alone by default: `--replace-existing` overwrites
it. Leaving it makes the column mean two things, and a chart drawn across it shows
a collapse in exit confidence that never happened; overwriting it destroys the
only surviving record of the old metric. That is the operator's call, not this
script's.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.db.connection import get_engine


READ = """
    SELECT s.trading_day,
           s.avg_exit_confidence AS recorded,
           ROUND(AVG((p.payload->>'last_exit_confidence_score')::numeric), 2) AS computed,
           COUNT(p.payload->>'last_exit_confidence_score') AS scored,
           COUNT(p.trade_key) AS closed
    FROM daily_engine_summary AS s
    LEFT JOIN paper_trades AS p
      ON p.closed_at >= s.trading_day
     AND p.closed_at <  s.trading_day + INTERVAL '1 day'
     AND p.status = 'CLOSED'
    GROUP BY s.trading_day, s.avg_exit_confidence
    ORDER BY s.trading_day
"""

# `payload` carries its own copy, and the learning page reads the JSON rather
# than the column. Updating one and not the other is how two numbers for the
# same thing start disagreeing.
WRITE = """
    UPDATE daily_engine_summary
       SET avg_exit_confidence = :value,
           payload = jsonb_set(
               COALESCE(payload, '{}'::jsonb),
               '{avg_exit_confidence}',
               to_jsonb(CAST(:value AS numeric)),
               true
           )
     WHERE trading_day = :day
"""


def _rounded(value):
    """Both sides as a plain float at the stored precision, or None."""

    return None if value is None else round(float(value), 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the values")
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="also overwrite rows already holding a value from the old metric",
    )
    args = parser.parse_args()

    engine = get_engine()

    with engine.connect() as connection:
        rows = connection.execute(text(READ)).mappings().all()

    print(f"{'day':<12}{'closed':>7}{'scored':>7}{'recorded':>10}{'computed':>10}")
    changed = []
    occupied = []

    for row in rows:
        # `AVG(...)` returns Decimal and the column is double precision, so the
        # two never compare equal as they come back -- which made every row this
        # tool had already written look like a pending change on the next run.
        computed = _rounded(row["computed"])
        recorded = _rounded(row["recorded"])
        print(
            f"{str(row['trading_day']):<12}{row['closed']:>7}{row['scored']:>7}"
            f"{str(recorded if recorded is not None else '-'):>10}"
            f"{str(computed if computed is not None else '-'):>10}"
        )

        # A day with no scored trade stays NULL rather than becoming 0. There is
        # a difference between "exit confidence averaged zero" and "nothing
        # recorded a confidence", and the second one is what those days are.
        if computed is None or computed == recorded:
            continue

        # A row that already holds a value holds the *old* metric, so replacing
        # it is a rewrite of history rather than a backfill of a gap.
        if recorded is not None:
            occupied.append((row["trading_day"], recorded, computed))
            if not args.replace_existing:
                continue

        changed.append((row["trading_day"], computed))

    for day, recorded, computed in occupied:
        print(
            f"\n  {day} already holds {recorded} from the V2-shadow source; "
            f"the live-engine figure is {computed}. "
            + (
                "REPLACING (--replace-existing)."
                if args.replace_existing
                else "Left alone; pass --replace-existing to overwrite."
            )
        )

    print(f"\n{len(changed)} of {len(rows)} rows would change.")

    if not changed:
        return 0

    if not args.apply:
        print("Dry run. Re-run with --apply to write.")
        return 0

    with engine.begin() as connection:
        for day, value in changed:
            connection.execute(text(WRITE), {"day": day, "value": value})

    print(f"Wrote {len(changed)} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
