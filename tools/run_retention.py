"""Apply diagnostic-table retention.

Dry run (default -- reports what would be deleted, touches nothing):
    python tools/run_retention.py

Apply, then let Postgres reuse the freed pages:
    python tools/run_retention.py --apply --vacuum

Per-table overrides come from the environment, e.g.
RETENTION_KEEP_DAYS_ACTIVITY_TRACE_EVENT=10.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.retention import RETENTION_RULES, run_retention, vacuum


def human(n):
    n = float(n or 0)
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually delete; without this the run is a dry run",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="VACUUM ANALYZE the pruned tables afterwards",
    )
    parser.add_argument("--batch-size", type=int, default=5000)
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"retention: {mode}\n")

    report = run_retention(dry_run=not args.apply, batch_size=args.batch_size)

    if report.get("skipped"):
        print(f"skipped: {report['skipped']}")
        return 1

    reasons = {rule.table: rule.reason for rule in RETENTION_RULES}
    label = "DELETED" if args.apply else "WOULD DELETE"
    print(f"{'TABLE':<26}{'KEEP':>6}{label:>16}")
    print("-" * 48)

    for table, row in report["tables"].items():
        if row.get("error"):
            print(f"{table:<26}{row['keep_days']:>5}d{'ERROR':>16}")
            continue

        count = row["deleted"] if args.apply else row["expired"]
        print(f"{table:<26}{row['keep_days']:>5}d{count:>16,}")

    print("-" * 48)
    total = report["total_deleted"] if args.apply else sum(
        r.get("expired", 0) for r in report["tables"].values()
    )
    print(f"{'TOTAL':<26}{'':>6}{total:>16,}\n")

    before = report.get("database_bytes_before")
    after = report.get("database_bytes_after")
    print(f"database: {human(before)} -> {human(after)}")

    if args.apply and not args.vacuum:
        print("\nnote: DELETE marks rows dead but does not return pages to Neon.")
        print("      Re-run with --vacuum, or let autovacuum catch up.")

    if args.vacuum and args.apply:
        print("\nvacuuming...")
        done = vacuum()
        print(f"vacuumed {len(done)} tables")

    if not args.apply:
        print("\nNothing was deleted. Re-run with --apply to act on this.")
        print("\nwindows in effect:")
        for rule in RETENTION_RULES:
            resolved = rule.resolved_keep_days()
            flag = "" if resolved == rule.keep_days else f"  (overridden from {rule.keep_days}d)"
            print(f"  {rule.table:<26} {resolved:>3}d{flag}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
