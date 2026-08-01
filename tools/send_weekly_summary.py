"""Post the weekly results to the Telegram channel.

The scanner sends this automatically once a week (Friday's close through the
weekend, see `dispatch_weekly_summary_if_due`). This tool covers the cases the
automatic path cannot: a weekend where nothing was scanning, a week that needs
re-reporting, or simply checking what the message will say before it goes out.

    python -m tools.send_weekly_summary --dry-run     # print it, send nothing
    python -m tools.send_weekly_summary               # send this week's
    python -m tools.send_weekly_summary --week 2026-07-27
    python -m tools.send_weekly_summary --force       # ignore the once-a-week dedup
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:

    sys.path.insert(0, str(ROOT_DIR))

from app.alerts.telegram_alerts import (  # noqa: E402
    build_weekly_outcome_summary_message,
    maybe_send_weekly_outcome_summary,
)
from app.analytics.weekly_summary import (  # noqa: E402
    build_weekly_summary,
    weekly_summary_window,
)


def main(argv=None):

    # The message is UTF-8 (emoji headers); a Windows console defaults to cp1252
    # and would raise rather than print it. Telegram is unaffected either way.
    try:

        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    except (AttributeError, ValueError):

        pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--week",
        help="Any date inside the target week (YYYY-MM-DD). Defaults to this week.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the message and the stats without sending.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Send even if this week's summary already went out.",
    )
    args = parser.parse_args(argv)

    reference = date.fromisoformat(args.week) if args.week else None
    start_day, end_day = weekly_summary_window(reference)
    summary = build_weekly_summary(start_day, end_day)
    stats = summary["stats"]

    print(f"[WEEKLY SUMMARY] {start_day} .. {end_day}")
    print(f"[WEEKLY SUMMARY] {len(summary['trades'])} closed trade(s) loaded")
    print(f"[WEEKLY SUMMARY] stats: {stats}")
    print("-" * 60)
    print(build_weekly_outcome_summary_message(stats, start_day, end_day))
    print("-" * 60)

    if args.dry_run:

        print("[WEEKLY SUMMARY] dry run; nothing sent.")

        return 0

    result = maybe_send_weekly_outcome_summary(
        stats,
        start_day,
        end_day,
        force=args.force,
    )
    print(f"[WEEKLY SUMMARY] result: {result}")

    return 0


if __name__ == "__main__":

    raise SystemExit(main())
