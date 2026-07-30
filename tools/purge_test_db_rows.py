"""Remove pytest fixture rows that leaked into the production database.

`tests/__init__.py` redirected the storage roots but left the database alone, so a
plain `pytest` run wrote fixture rows into `alert_events` and `telegram_dispatch`
through the real `DATABASE_URL`. That leak is fixed at source; this script cleans
up what the leak already wrote.

    python tools/purge_test_db_rows.py                  # dry run: report only
    python tools/purge_test_db_rows.py --apply          # back up, then delete

Deleting audit rows out of a live trading database is not reversible, so this
script refuses to guess:

* A row is only ever deleted as part of a *cluster* -- a burst of rows separated
  from its neighbours by more than CLUSTER_GAP_SECONDS, which is what one pytest
  run looks like.
* A cluster is only deleted when it contains at least one row carrying a
  definitive fixture fingerprint (see FINGERPRINTS). Signature matching alone is
  not safe: SPCX is a real traded symbol here with dozens of genuine option
  tickers, and only the exact string `SPCX250718C00050000` is a fixture. Some test
  rows also use NVDA, which is indistinguishable from a real row on its own.
* A cluster overlapping any real scan window from `scanner_runs` is never touched,
  and is reported instead. Real alert rows are written during scans.
* `--apply` writes every row it is about to delete to a JSON backup first, and
  aborts if the backup cannot be written.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)

# One pytest run's writes land within a few seconds. 60s separates runs comfortably
# while never merging two runs that are minutes apart.
CLUSTER_GAP_SECONDS = 60

TABLES = {
    "alert_events": "created_at",
    "telegram_dispatch": "timestamp",
}

# Strings that only a test fixture produces. `boom` is raised by
# tests/test_telegram_dispatcher.py; the SPCX ticker below has no `O:` prefix and a
# 2025 expiry, unlike every real SPCX contract in the table.
FINGERPRINTS = {
    "alert_events": "option_ticker = 'SPCX250718C00050000'",
    "telegram_dispatch": (
        "failure_reason = 'boom' or "
        "(symbol is null and message_type = 'UNKNOWN' and message_length = 5)"
    ),
}


def scan_windows(connection):
    """Real scan intervals. An unfinished run is treated as lasting 15 minutes."""

    windows = []

    for row in connection.execute(text(
        "select started_at, finished_at from scanner_runs order by started_at"
    )).mappings():
        started = row["started_at"]
        finished = row["finished_at"] or (started + timedelta(minutes=15))
        windows.append((started, finished))

    return windows


def overlaps_scan(lo, hi, windows):
    return any(lo <= end and start <= hi for start, end in windows)


def clusters_for(connection, table, time_column):
    rows = list(connection.execute(text(
        f'select *, "{time_column}" as _ts from "{table}" order by "{time_column}"'
    )).mappings())

    grouped = []

    for row in rows:
        if grouped and (row["_ts"] - grouped[-1][-1]["_ts"]).total_seconds() <= CLUSTER_GAP_SECONDS:
            grouped[-1].append(row)
        else:
            grouped.append([row])

    marked = {
        r["id"] for r in connection.execute(text(
            f'select id from "{table}" where {FINGERPRINTS[table]}'
        )).mappings()
    }

    return [
        {
            "rows": cluster,
            "start": cluster[0]["_ts"],
            "end": cluster[-1]["_ts"],
            "fixtures": sum(1 for r in cluster if r["id"] in marked),
        }
        for cluster in grouped
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true",
                        help="perform the deletion (default is a dry run)")
    parser.add_argument("--backup", default=str(ROOT / "logs" / "purged_test_rows.json"),
                        help="where to write the pre-delete backup")
    args = parser.parse_args()

    url = os.getenv("DATABASE_DIRECT_URL", "").strip() or os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("DATABASE_DIRECT_URL or DATABASE_URL is required")

    engine = create_engine(url, pool_pre_ping=True)
    doomed = {}
    protected = []

    with engine.connect() as connection:
        windows = scan_windows(connection)
        print(f"{len(windows)} real scan windows on record\n")

        for table, time_column in TABLES.items():
            total = connection.execute(text(f'select count(*) from "{table}"')).scalar()
            ids = []

            for cluster in clusters_for(connection, table, time_column):
                if not cluster["fixtures"]:
                    continue

                if overlaps_scan(cluster["start"], cluster["end"], windows):
                    protected.append((table, cluster))
                    continue

                ids.extend(row["id"] for row in cluster["rows"])

            doomed[table] = ids
            print(f"{table}: {total} rows, {len(ids)} identified as pytest output")

    if protected:
        print(f"\n{len(protected)} fixture cluster(s) overlap a real scan window and were "
              "left alone -- these need manual review:")
        for table, cluster in protected:
            print(f"  {table} {cluster['start']} .. {cluster['end']} "
                  f"({len(cluster['rows'])} rows, {cluster['fixtures']} fixture marker(s))")

    if not any(doomed.values()):
        print("\nNothing to delete.")
        return

    if not args.apply:
        print("\nDry run. Re-run with --apply to back up and delete.")
        return

    with engine.begin() as connection:
        backup = []

        for table, ids in doomed.items():
            if not ids:
                continue
            for row in connection.execute(text(
                f'select * from "{table}" where id = any(:ids)'
            ), {"ids": ids}).mappings():
                backup.append({"table": table, "row": {k: str(v) for k, v in row.items()}})

        path = Path(args.backup)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(backup, indent=2), encoding="utf-8")
        print(f"\nbacked up {len(backup)} rows to {path}")

        deleted = 0
        for table, ids in doomed.items():
            if not ids:
                continue
            count = connection.execute(text(
                f'delete from "{table}" where id = any(:ids)'
            ), {"ids": ids}).rowcount
            deleted += count
            print(f"  deleted {count} from {table}")

        print(f"TOTAL DELETED: {deleted}")


if __name__ == "__main__":
    main()
