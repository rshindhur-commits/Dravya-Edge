"""Correct `paper_trades.opened_at` / `closed_at` rows that hold ET as UTC.

The write path stored ET wall-clock in a `timestamptz` column, then started
storing true UTC, and the existing rows were never backfilled. So the column is
**mixed**: rows before 2026-08-03 are four hours early, rows after are correct.

    id 48  2026-07-31  opened_at 11:36:33+00   opened_at_utc 15:36:33+00
    id 77  2026-08-03  opened_at 15:58:12+00   opened_at_utc 15:58:12+00

A column that is wrong the same way everywhere produces a consistent shift you
can correct for in a query. This one silently compares ET against UTC in any
range filter, join or duration that spans the changeover, so it yields
plausible-looking wrong answers rather than obvious ones -- and it makes trades
before the boundary look as though they fired outside the 09:45-15:30 ET
auto-paper entry window when they did not.

    python tools/backfill_trade_timestamps.py            # dry run: report only
    python tools/backfill_trade_timestamps.py --apply    # back up, then update

Every correction comes from the row's own `payload.opened_at_utc` /
`payload.closed_at_utc`, which have been written correctly throughout and are the
authoritative record. Nothing is inferred:

* A row with no `*_utc` in its payload is reported and never touched -- there is
  nothing to correct it from, and guessing a fixed four-hour shift would corrupt
  any row that was already right.
* The offset is not assumed. ET is UTC-4 in summer and UTC-5 in winter, so rows
  are selected by *disagreeing with their own payload* rather than by a hardcoded
  delta, which also makes this a no-op once every row is correct.
* `--apply` writes every row it is about to change to a JSON backup first,
  including the old and new value of each column, and aborts if it cannot.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=False)

# Clock skew between the app and Postgres is sub-second; a real mislabelling is
# whole hours. Anything between the two is a bug this script must not paper over.
TOLERANCE_SECONDS = 60

COLUMNS = {
    "opened_at": "opened_at_utc",
    "closed_at": "closed_at_utc",
}


def _as_utc(value):
    if value is None:
        return None

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)


def plan_corrections(rows):
    """Rows to fix, rows that cannot be fixed, and why. Pure, so it is testable."""

    corrections = []
    unfixable = []

    for row in rows:
        payload = row.get("payload") or {}

        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {}

        changes = {}
        missing = []

        for column, payload_key in COLUMNS.items():
            stored = _as_utc(row.get(column))

            if stored is None:
                continue

            truth = _as_utc(payload.get(payload_key))

            if truth is None:
                missing.append(column)
                continue

            if abs((stored - truth).total_seconds()) > TOLERANCE_SECONDS:
                changes[column] = {
                    "from": stored.isoformat(),
                    "to": truth.isoformat(),
                    "shift_hours": round((truth - stored).total_seconds() / 3600, 2),
                }

        if changes:
            corrections.append({"id": row.get("id"), "changes": changes})

        elif missing:
            # Only interesting when the row also looks wrong; a row with no
            # payload truth and no visible skew is simply unverifiable.
            unfixable.append({"id": row.get("id"), "columns": missing})

    return corrections, unfixable


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true",
                        help="perform the update (default is a dry run)")
    parser.add_argument("--backup", default=str(ROOT / "logs" / "backfilled_timestamps.json"),
                        help="where to write the pre-update backup")
    args = parser.parse_args()

    url = os.getenv("DATABASE_DIRECT_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()

    if not url:
        raise SystemExit("DATABASE_DIRECT_URL or DATABASE_URL is required")

    engine = create_engine(url, pool_pre_ping=True)

    with engine.connect() as connection:
        rows = [
            dict(row) for row in connection.execute(text(
                "SELECT id, opened_at, closed_at, payload FROM paper_trades ORDER BY id"
            )).mappings()
        ]

    corrections, unfixable = plan_corrections(rows)

    print(f"{len(rows)} paper trades on record")
    print(f"{len(corrections)} row(s) disagree with their own payload\n")

    for correction in corrections:
        parts = ", ".join(
            f"{column} {change['from']} -> {change['to']} ({change['shift_hours']:+}h)"
            for column, change in correction["changes"].items()
        )
        print(f"  id {correction['id']}: {parts}")

    if unfixable:
        print(f"\n{len(unfixable)} row(s) carry no payload UTC to correct from and were "
              "left alone:")
        for row in unfixable:
            print(f"  id {row['id']}: {', '.join(row['columns'])}")

    if not corrections:
        print("\nNothing to correct.")
        return

    if not args.apply:
        print("\nDry run. Re-run with --apply to back up and update.")
        return

    path = Path(args.backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(corrections, indent=2), encoding="utf-8")
    print(f"\nbacked up {len(corrections)} row(s) to {path}")

    updated = 0

    with engine.begin() as connection:
        for correction in corrections:
            for column, change in correction["changes"].items():
                connection.execute(
                    text(
                        f"UPDATE paper_trades SET {column} = CAST(:value AS timestamptz) "
                        "WHERE id = :id"
                    ),
                    {"value": change["to"], "id": correction["id"]},
                )
            updated += 1

    print(f"TOTAL ROWS UPDATED: {updated}")


if __name__ == "__main__":
    main()
