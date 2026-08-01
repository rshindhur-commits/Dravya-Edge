"""Write the plain-English post-market review for a trading day.

    python tools/post_market_report.py --date 2026-08-03

Reads `trade_exit_analysis` from Postgres, falling back to the day's
`trend_capture_analysis.csv`. Distinct from `daily_validation_report.py`, which
is an engineering diagnostic; this one answers what the day did and whether the
exits were right, in sentences.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.analytics.post_market_review import write_review  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Plain-English post-market review")
    parser.add_argument("--date", required=True, help="Trading day, YYYY-MM-DD")
    args = parser.parse_args()

    path, summary = write_review(args.date)

    print(f"\nPost-market review for {args.date}")
    print(f"  trades          {summary['trades']}")
    print(f"  made money      {summary['winners']}")
    print(f"  lost money      {summary['losers']}")
    print(f"  net points      {summary['net_points']}")
    print(f"  left on table   {summary['left_on_table']}")
    print(f"  exits too early {summary['exits_too_early']}")
    print(f"\nWritten to {path}")


if __name__ == "__main__":
    main()
