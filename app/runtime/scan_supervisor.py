"""In-process scan engine, so the dashboard scans without an operator or a terminal.

Cadence used to depend on Streamlit's auto-refresh calling
`_maybe_auto_run_scanner()` during a browser rerun. Coverage therefore tracked
whether a tab happened to be open: on 2026-07-29 only 32 scans were archived
between 08:01 and 17:09 ET, including an 86-minute hole. `app.runtime.scan_loop`
fixed the cadence but needs its own terminal and its own babysitting.

This module runs that same session-aware cadence as a daemon thread inside the
Streamlit server process. `ensure_started()` is idempotent and cheap, so the
dashboard calls it on every rerun; the loop then keeps scanning for as long as the
server lives, regardless of which page is showing or whether a browser is even
connected.

Two invariants keep this safe:

* Scans serialise through `app.runtime.scan_lock`, whose reentrancy is
  thread-local. An external `python -m app.runtime.scan_loop`, a bare
  `python -m app.main`, or a second Streamlit worker cannot overlap this thread.
* This module owns cadence only. It calls `run_scanner()` and makes no trading
  decision: gating, entries, exits, and alerts stay where they are.

No `signal` handlers are installed here -- `signal.signal()` only works on the
main thread, which is why `scan_loop.run_scan_loop()` cannot simply be reused.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.storage.daily_paths import live_path
from app.utils.json_store import save_json_file

ET = ZoneInfo("America/New_York")
STATUS_FILENAME = "scanner_run_status.json"
DEFAULT_AFTER_CLOSE_TAIL_MINUTES = 20.0

_thread = None
_stop_event = threading.Event()
_start_lock = threading.Lock()

_state_lock = threading.Lock()
_state = {
    "started": False,
    "scans": 0,
    "failures": 0,
    "session": None,
    "interval_seconds": None,
    "interval_override_seconds": None,
    "last_started_at": None,
    "last_completed_at": None,
    "last_duration_seconds": None,
    "last_error": None,
    "next_due_at": None,
    "status": "IDLE",
}


def _now():
    return datetime.now(ET)


def _iso(moment):
    return moment.isoformat(timespec="seconds")


def _update(**fields):
    """Update the shared status and mirror it to disk for the sidebar to read."""

    with _state_lock:
        _state.update(fields)
        snapshot = dict(_state)

    try:
        save_json_file(str(live_path(STATUS_FILENAME)), snapshot)
    except Exception as exc:
        print(f"[SCAN ENGINE WARNING] could not write status: {exc}")

    return snapshot


def status():
    """Current engine status, including whether the thread is actually alive."""

    with _state_lock:
        snapshot = dict(_state)

    snapshot["thread_alive"] = bool(_thread and _thread.is_alive())

    return snapshot


def set_interval_override(seconds):
    """Change cadence on a live engine. None restores the session-aware schedule.

    Read at the top of every cycle rather than captured at thread start, so the
    sidebar cadence control is not a dead knob once the engine is running.
    """

    with _state_lock:
        _state["interval_override_seconds"] = int(seconds) if seconds else None


def _after_close_tail_minutes():
    """Minutes past 16:00 ET to keep scanning. Env-tunable; large values restore
    the previous behaviour of scanning until AFTERHOURS ends at 20:00."""

    raw = os.getenv("SCAN_AFTER_CLOSE_MINUTES", "").strip()

    if not raw:
        return DEFAULT_AFTER_CLOSE_TAIL_MINUTES

    try:
        return float(raw)
    except ValueError:
        print(f"[SCAN ENGINE WARNING] bad SCAN_AFTER_CLOSE_MINUTES={raw!r}; using default.")
        return DEFAULT_AFTER_CLOSE_TAIL_MINUTES


def _minutes_past_close(now):
    """Signed minutes relative to the 16:00 ET bell. Negative before the close."""

    close = now.replace(hour=16, minute=0, second=0, microsecond=0)

    return (now - close).total_seconds() / 60.0


# NYSE/Nasdaq full closures. Half days (the 13:00 ET closes around Thanksgiving,
# Christmas Eve and 3 July) are deliberately absent: the after-close tail already
# stops scanning once `_minutes_past_close` runs out, and it measures from 16:00,
# so a half day simply scans a few idle cycles rather than doing anything wrong.
#
# A literal table rather than a calculated calendar. Good Friday moves with
# Easter and Juneteenth is recent enough that libraries disagree about it, so a
# list that is obviously auditable beats arithmetic nobody will re-derive. It
# needs extending each year; `MARKET_HOLIDAYS_THROUGH` makes running past the
# end of the table loud instead of silent.
MARKET_HOLIDAYS_THROUGH = 2027

MARKET_HOLIDAYS = frozenset({
    # 2026
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King Jr. Day
    "2026-02-16",  # Washington's Birthday
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed, 4 July falls on a Saturday)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving
    "2026-12-25",  # Christmas
    # 2027
    "2027-01-01",
    "2027-01-18",
    "2027-02-15",
    "2027-03-26",
    "2027-05-31",
    "2027-06-18",  # Juneteenth observed, 19 June falls on a Saturday
    "2027-07-05",  # Independence Day observed, 4 July falls on a Sunday
    "2027-09-06",
    "2027-11-25",
    "2027-12-24",  # Christmas observed, 25 December falls on a Saturday
})


def is_market_holiday(now):
    """Whether the exchange is shut for the day.

    `get_market_session()` is clock-only: at 10:00 on Christmas it still returns
    REGULAR, and the weekday guard alone does not catch a holiday that falls
    midweek. Without this the scanner burns a full day of Polygon option-chain
    calls and writes a day of candidate rows against a market that never opened.

    Unknown years return False -- scanning a holiday wastes calls but breaks
    nothing, whereas guessing a calendar could idle a real trading day.
    """

    if now.year > MARKET_HOLIDAYS_THROUGH:

        print(
            f"[SCAN ENGINE WARNING] no holiday calendar beyond "
            f"{MARKET_HOLIDAYS_THROUGH}; {now.year} holidays will scan. "
            f"Extend MARKET_HOLIDAYS in app/runtime/scan_supervisor.py."
        )
        return False

    return now.strftime("%Y-%m-%d") in MARKET_HOLIDAYS


def _idle_reason(session, now):
    """Why this cycle should not scan, or None to scan.

    Weekends are skipped because `get_market_session()` is a clock-only function:
    at 10:00 on a Saturday it still returns REGULAR, which would otherwise scan
    every 5 minutes all weekend. Exchange holidays are not modelled anywhere in
    the codebase, so a holiday still scans.

    Scanning also stops shortly after the close. On 2026-07-30, 9 of the day's 26
    scans ran between 16:01 and 18:01 -- 35% of the day's compute and Polygon
    option-chain calls -- and every decision in all nine was "outside auto-entry
    window". They could not open a trade by construction.

    The tail is not zero because the last scan of the day writes the closing
    archive. It defaults wider than one AFTERHOURS interval (900s) so at least one
    scan always lands after the bell: the final pre-close scan starts at 15:59 at
    the latest, putting its successor no later than ~16:14.

    Intraday force-close is unaffected -- AUTO_PAPER_EOD_CLOSE fires at 15:55,
    before the bell, so it never depended on a post-close scan.
    """

    if now.weekday() >= 5:
        return "SLEEPING_WEEKEND"

    if is_market_holiday(now):
        return "SLEEPING_HOLIDAY"

    if str(session).upper() == "CLOSED":
        return "SLEEPING_CLOSED"

    if _minutes_past_close(now) > _after_close_tail_minutes():
        return "SLEEPING_AFTER_CLOSE"

    return None


def _loop(skip_closed):
    from app.runtime.scan_loop import (
        _run_one_scan,
        current_session,
        interval_for_session,
    )

    print("[SCAN ENGINE] started in-process.")

    while not _stop_event.is_set():

        try:
            session = current_session()
        except Exception as exc:
            session = "UNKNOWN"
            print(f"[SCAN ENGINE WARNING] could not resolve market session: {exc}")

        with _state_lock:
            interval_override = _state["interval_override_seconds"]

        interval = interval_for_session(session, interval_override)
        idle_reason = _idle_reason(session, _now()) if skip_closed else None

        if idle_reason:
            _update(
                status=idle_reason,
                session=str(session),
                interval_seconds=interval,
                next_due_at=_iso(_now() + timedelta(seconds=interval)),
            )
            _stop_event.wait(interval)
            continue

        _update(
            status="RUNNING",
            session=str(session),
            interval_seconds=interval,
            last_started_at=_iso(_now()),
        )

        result = _run_one_scan()

        with _state_lock:
            scans = _state["scans"] + 1
            failures = _state["failures"] + (0 if result["ok"] else 1)

        wait = max(5.0, interval - result["seconds"])

        _update(
            status="COMPLETED" if result["ok"] else "FAILED",
            scans=scans,
            failures=failures,
            last_completed_at=_iso(_now()),
            last_duration_seconds=round(result["seconds"], 1),
            last_error=result["error"],
            next_due_at=_iso(_now() + timedelta(seconds=wait)),
        )

        if not result["ok"]:
            print(f"[SCAN ENGINE ERROR] scan {scans} failed: {result['error']}")

        _stop_event.wait(wait)

    _update(status="STOPPED", next_due_at=None)
    print("[SCAN ENGINE] stopped.")


def ensure_started(interval_override=None, skip_closed=True):
    """Start the scan engine once per process. Safe to call on every rerun."""

    global _thread

    set_interval_override(interval_override)

    with _start_lock:

        if _thread is not None and _thread.is_alive():
            return status()

        _stop_event.clear()
        _thread = threading.Thread(
            target=_loop,
            args=(skip_closed,),
            name="dravya-scan-engine",
            daemon=True,
        )
        _update(started=True, status="STARTING")
        _thread.start()

    return status()


def stop(timeout=None):
    """Ask the engine to finish the current scan and exit."""

    _stop_event.set()

    if _thread is not None and timeout:
        _thread.join(timeout)

    return status()
