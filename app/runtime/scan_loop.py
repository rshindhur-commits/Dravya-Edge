"""Session-aware scan loop so the scanner schedules itself.

`python -m app.main` runs exactly one scan and exits. Nothing else in the codebase
loops, so repeated scanning depended entirely on Streamlit's auto-refresh calling
`_maybe_auto_run_scanner()` -- which means coverage depended on a browser tab being
open, and on which page it was showing.

The cost is measurable. On 2026-07-29 only 32 scans were archived between 08:01 and
17:09 ET, including an 86-minute blind hole from 11:09 to 12:35, roughly 38% of a
5-minute cadence. Setups that appeared and resolved inside a gap were never seen,
and a sparse archive also weakens the regression A/B that depends on it.

This module owns cadence only. It calls `run_scanner()` and makes no trading
decision of its own: gating, entries, exits, and alerts all stay where they are.

    python -m app.runtime.scan_loop                  # session-aware cadence
    python -m app.runtime.scan_loop --interval 300   # fixed 5-minute cadence
    python -m app.runtime.scan_loop --once           # single scan, same as app.main
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.runtime.market_calendar import idle_reason  # noqa: E402

ET = ZoneInfo("America/New_York")

# Cadence per market session, in seconds. Denser through the regular session where
# intraday setups form and resolve; sparse when nothing actionable can happen.
SESSION_INTERVALS = {
    "OPENING_RANGE": 120,
    "REGULAR": 300,
    "PREMARKET": 600,
    "AFTERHOURS": 900,
    "CLOSED": 1800,
}
DEFAULT_INTERVAL = 300

_stopping = False


def _request_stop(signum, _frame):
    global _stopping
    _stopping = True
    print(f"\n[SCAN LOOP] signal {signum} received; finishing the current scan then exiting.")


def current_session(now=None):
    from app.main import get_market_session

    return get_market_session(now or datetime.now(ET))


def interval_for_session(session, override=None):
    if override:
        return max(30, int(override))

    return SESSION_INTERVALS.get(str(session).upper(), DEFAULT_INTERVAL)


def _publish_heartbeat(status, **fields):
    """Tell Postgres this engine is alive. Never allowed to break the loop.

    Always publishes as `worker`, never as `scan_engine_owner()`. Identity is what
    this module *is*; SCAN_ENGINE_OWNER says who *should* scan. Letting the
    variable pick the identity means a misconfigured host publishes under the
    other engine's key and silently overwrites its row -- which is how the
    dashboard came to hide the real Render worker on 2026-08-01.
    """

    try:
        from app.runtime.scan_engine_heartbeat import record_heartbeat

        record_heartbeat(status, owner="worker", **fields)

    except Exception as exc:
        print(f"[SCAN LOOP WARNING] heartbeat failed: {exc}")


def _run_one_scan():
    """Run a scan, absorbing any failure so the loop survives a bad cycle."""

    from app.main import run_scanner

    started = time.perf_counter()

    try:
        run_scanner()
        return {"ok": True, "seconds": time.perf_counter() - started, "error": None}

    except KeyboardInterrupt:
        raise

    except Exception as exc:
        return {
            "ok": False,
            "seconds": time.perf_counter() - started,
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_scan_loop(interval_override=None, max_scans=None, skip_closed=True):
    """Scan on a session-aware cadence until interrupted."""

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    scans = 0
    failures = 0

    print(
        "[SCAN LOOP] started. "
        + (f"fixed interval {interval_override}s" if interval_override
           else "session-aware cadence " + ", ".join(
               f"{name}={seconds}s" for name, seconds in SESSION_INTERVALS.items()))
    )

    while not _stopping:

        session = current_session()

        # The full calendar, not just the CLOSED session. `get_market_session` is
        # clock-only: at 11:00 on a Saturday it returns REGULAR. This loop used to
        # check only for CLOSED, so once the Render worker took over scanning it
        # scanned all weekend against stale data while the dashboard supervisor --
        # which has had this guard for a while -- correctly slept. Shared with the
        # supervisor rather than reimplemented; see app/runtime/market_calendar.py.
        idle = idle_reason(session, datetime.now(ET)) if skip_closed else None

        if idle:
            wait = interval_for_session(session, interval_override)
            # Still report. A quiet engine and a dead one look identical from the
            # dashboard otherwise, and the reason is the answer to that question.
            _publish_heartbeat(
                idle, session=session, scans=scans, failures=failures,
                interval_seconds=wait,
                next_due_at=(datetime.now(ET) + timedelta(seconds=wait)).isoformat(),
            )
            print(f"[SCAN LOOP] {idle}; sleeping {wait}s without scanning.")
            _sleep(wait)
            continue

        started_at = datetime.now(ET)
        _publish_heartbeat("SCANNING", session=session, scans=scans, failures=failures)
        result = _run_one_scan()
        scans += 1

        if not result["ok"]:
            failures += 1
            print(f"[SCAN LOOP ERROR] scan {scans} failed: {result['error']}")

        interval = interval_for_session(session, interval_override)
        elapsed = result["seconds"]
        wait = max(5, interval - elapsed)

        _publish_heartbeat(
            "IDLE" if result["ok"] else "FAILED",
            session=session,
            scans=scans,
            failures=failures,
            last_scan_at=started_at.isoformat(),
            last_duration_sec=round(elapsed, 2),
            interval_seconds=interval,
            next_due_at=(datetime.now(ET) + timedelta(seconds=wait)).isoformat(),
            last_error=result["error"],
        )

        print(
            f"[SCAN LOOP] scan {scans} ({session}) "
            f"finished in {elapsed:.1f}s at {started_at.strftime('%H:%M:%S')} ET; "
            f"{failures} failure(s) so far; next in {wait:.0f}s"
        )

        if max_scans and scans >= max_scans:
            print(f"[SCAN LOOP] reached --max-scans={max_scans}; exiting.")
            break

        if _stopping:
            break

        _sleep(wait)

    # A clean shutdown is worth recording. Without it the last heartbeat says
    # IDLE and the dashboard has to infer death from staleness, which takes 15
    # minutes to conclude something the process knew at the time.
    _publish_heartbeat("STOPPED", scans=scans, failures=failures)

    print(f"[SCAN LOOP] stopped after {scans} scan(s), {failures} failure(s).")
    return {"scans": scans, "failures": failures}


def _sleep(seconds):
    """Sleep in short slices so a stop signal is honoured promptly."""

    deadline = time.monotonic() + seconds

    while not _stopping and time.monotonic() < deadline:
        time.sleep(min(1.0, deadline - time.monotonic()))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--interval", type=int,
                        help="fixed cadence in seconds; overrides the session-aware schedule")
    parser.add_argument("--max-scans", type=int,
                        help="stop after this many scans (useful for a smoke test)")
    parser.add_argument("--once", action="store_true",
                        help="run a single scan and exit")
    # `--skip-closed` is now the default and kept only so an existing start
    # command keeps working. Honouring the calendar had to be opted *into*, and
    # the Render worker's start command is a bare `python -m app.runtime.scan_loop`
    # -- so on 2026-08-01 it scanned a Saturday while the dashboard supervisor,
    # which defaults skip_closed=True, correctly slept. A guard you have to
    # remember to switch on is a guard that is off in production.
    parser.add_argument("--skip-closed", action="store_true",
                        help="deprecated; honouring the market calendar is the default")
    parser.add_argument("--scan-when-closed", action="store_true",
                        help="scan even when the market is shut (debugging only)")
    args = parser.parse_args()

    if args.once:
        result = _run_one_scan()
        if not result["ok"]:
            print(f"[SCAN LOOP ERROR] {result['error']}")
            raise SystemExit(1)
        raise SystemExit(0)

    run_scan_loop(
        interval_override=args.interval,
        max_scans=args.max_scans,
        skip_closed=not args.scan_when_closed,
    )


if __name__ == "__main__":
    main()
