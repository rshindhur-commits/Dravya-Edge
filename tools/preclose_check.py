"""Is anything still open, and is the engine alive to close it?

    python tools/preclose_check.py

Run it late in the session, before 16:00 ET. It answers one question the
dashboard cannot be trusted to answer: **is a position about to be carried
overnight without anyone deciding to carry it?**

## Why this is worth a tool

On 2026-08-05 an INTRADAY put on SMCI ran nine days unmanaged. It was booked at
-1.92% when the contract had actually lost -99.5%. Two trades of that kind are
roughly 90% of every loss this book has recorded, which is also why raw live
P&L comparisons from that period are contaminated. The failure was not a bad
entry and not a bad exit rule -- it was that nobody looked, and the position
held a profile (`INTRADAY`) that says on its face it should never have seen a
second session.

So the check that matters is not "how are we doing today". It is the much
narrower: anything open, what profile does it claim, and is the process that
would close it still breathing.

## Why it reads Postgres and not the dashboard

`data/live/*` is gitignored, so on Streamlit Cloud the Trading page falls back
to the `scanner_output.xlsx` committed to the repo -- last written 2026-08-08 as
of this writing. It renders, it refreshes, and it cannot show you today. The
database is the only surface both the worker and this laptop agree on.

## Exit status

Non-zero when something wants a human: an open position, a stale heartbeat, or
consecutive scan failures. That makes it usable from a scheduler without having
to parse the text.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

from app.db.connection import get_engine

ET = ZoneInfo("America/New_York")

# The worker publishes a heartbeat every cycle, and the longest legitimate cycle
# is 3600s (CLOSED). 2400s is inside that on purpose: this tool is meant to run
# in the session, where the interval is 300s, and a gap that long during the
# session means the loop is not running rather than that it is between scans.
HEARTBEAT_STALE_SECONDS = 2400

# Matches FAILURES_BEFORE_ALERT in app/runtime/scan_loop.py. One failed cycle is
# a Polygon hiccup the loop is built to survive; three is a missed session.
FAILURES_BEFORE_ALERT = 3


def _fetch_open_positions(conn):
    return conn.execute(
        text(
            """
            select id, symbol, direction, status, opened_at, option_ticker,
                   holding_profile, overnight_count, days_held, entry_price
            from paper_trades
            where status not in ('CLOSED', 'EXPIRED')
            order by opened_at
            """
        )
    ).fetchall()


def _fetch_worker(conn):
    return conn.execute(
        text(
            """
            select session, status, scans, failures, last_error, updated_at,
                   round(extract(epoch from (now() - updated_at))) as age_sec
            from scan_engine_heartbeat
            where owner = 'worker'
            order by updated_at desc
            limit 1
            """
        )
    ).first()


def _fetch_today(conn):
    return conn.execute(
        text(
            """
            select count(*) as opened,
                   count(*) filter (where status = 'CLOSED') as closed
            from paper_trades
            where (opened_at at time zone 'America/New_York')::date
                  = (now() at time zone 'America/New_York')::date
            """
        )
    ).first()


def main():
    now = datetime.now(ET)
    problems = []

    with get_engine().connect() as conn:
        positions = _fetch_open_positions(conn)
        worker = _fetch_worker(conn)
        today = _fetch_today(conn)

    print(f"Pre-close check  {now:%Y-%m-%d %H:%M:%S} ET")
    print(f"Minutes to 16:00 ET: "
          f"{int((now.replace(hour=16, minute=0, second=0) - now).total_seconds() // 60)}")

    print("\n-- OPEN POSITIONS --")

    if not positions:
        print("None. Nothing can be carried overnight.")

    for row in positions:
        held = now - row.opened_at.astimezone(ET)
        # An INTRADAY position open near the bell is the SMCI shape exactly, so
        # it is called out separately rather than left for the reader to spot in
        # a column.
        flag = ""
        if (row.holding_profile or "").upper() == "INTRADAY":
            flag = "  <-- INTRADAY, must not carry overnight"
        print(
            f"#{row.id} {row.symbol} {row.direction} {row.status} "
            f"{row.option_ticker or '(no contract)'} "
            f"profile={row.holding_profile} overnights={row.overnight_count} "
            f"open for {held.days}d {held.seconds // 3600}h{flag}"
        )
        problems.append(f"open position #{row.id} {row.symbol}")

    print("\n-- WORKER --")

    if worker is None:
        print("No worker heartbeat has ever been published.")
        problems.append("no worker heartbeat")
    else:
        age = int(worker.age_sec)
        print(
            f"{worker.status} in {worker.session}; {worker.scans} scans, "
            f"{worker.failures} failures; heartbeat {age}s old"
        )

        if worker.last_error:
            print(f"last error: {worker.last_error}")

        if age > HEARTBEAT_STALE_SECONDS:
            print(f"STALE: no heartbeat for {age}s.")
            problems.append(f"heartbeat {age}s old")

        if worker.failures >= FAILURES_BEFORE_ALERT:
            problems.append(f"{worker.failures} scan failures")

    print("\n-- TODAY --")
    print(f"opened {today.opened}, closed {today.closed}")

    print()

    if problems:
        print("NEEDS A LOOK: " + "; ".join(problems))
        return 1

    print("Clear. Nothing open, worker healthy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
