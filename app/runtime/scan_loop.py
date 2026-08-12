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
import os
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
#
# These also decide the database bill, which is not obvious from here. Neon
# suspends compute after 300s idle and bills CU-hours while awake, and a scan
# writes its rows in a burst of about 5 seconds. So an interval of 300 leaves a
# ~295s gap and the compute never once suspends; an interval above ~310 lets it
# sleep every cycle. REGULAR sits on the wrong side of that line by seconds.
#
# PREMARKET was 600. Measured 2026-08-11: its 28 scans priced **zero** contracts
# and carried chain evidence on 4 of 728 rows, against 231 of 2,210 in session --
# options do not trade before 09:30, and the entry window does not open until
# 09:45, so nothing before it can produce an entry either. It was holding the
# database awake roughly two hours a day to learn 1.7% of the day's evidence.
# 1800 keeps ~11 premarket scans for context and gap detection.
#
# REGULAR is deliberately left at 300. Moving it to 360 would suspend compute
# every cycle and cut the session's DB cost by about 15%, but the scan cadence
# through the session is a trading decision, not a hosting one, and it is not
# being made here as a side effect of saving a few dollars.
SESSION_INTERVALS = {
    "OPENING_RANGE": 120,
    "REGULAR": 300,
    "PREMARKET": 1800,
    "AFTERHOURS": 900,
    "CLOSED": 1800,
}
DEFAULT_INTERVAL = 300

# Consecutive failures before the operator is told. One bad cycle is a
# Polygon hiccup and the loop is built to survive it; three in a row means
# the session is being missed.
FAILURES_BEFORE_ALERT = 3

_stopping = False

# Which signal ended the loop, so the shutdown heartbeat can say why. A STOPPED
# row on its own records that the process died and nothing about what killed it,
# which is the first question asked every time it happens.
_stop_signal = None


def _request_stop(signum, _frame):
    global _stopping, _stop_signal
    _stopping = True
    _stop_signal = signum
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


def _maybe_prune():
    """Daily retention pass. Never allowed to break the loop."""

    try:
        from app.runtime.retention_scheduler import maybe_run_retention

        report = maybe_run_retention(datetime.now(ET), idle_reason_value="IDLE")

        if report and report.get("total_deleted"):
            print(
                f"[SCAN LOOP] retention removed {report['total_deleted']:,} "
                "expired diagnostic rows."
            )

    except Exception as exc:
        print(f"[SCAN LOOP WARNING] retention failed: {exc}")


def _report_database_state():
    """Say once, at startup, whether this container can reach Postgres.

    Every repository here is best-effort: reads return empty and writes are
    dropped, both without raising. That is right for keeping a scan alive through
    a blip and wrong as a permanent condition, because a container that never had
    a database is indistinguishable from a quiet market -- it reports no open
    trades, no dedup keys, and no history, all confidently.

    Recorded in the heartbeat as well as the log, so the state is visible from
    the dashboard rather than only to whoever is watching Render's console at the
    moment the process starts.
    """

    try:
        from app.db.persistence import database_status

        status = database_status()

    except Exception as exc:
        status = "UNREACHABLE"
        print(f"[SCAN LOOP] database status check failed: {exc}")

    if status == "ON":
        print("[SCAN LOOP] database reachable; persistence and dedup are live.")
        return status

    warning = (
        "[SCAN LOOP WARNING] database is "
        + ("switched off (DB_WRITE_ENABLED)" if status == "OFF" else "UNREACHABLE")
        + ". Alert dedup cannot be verified and trade history will read as empty."
    )
    print(warning)
    _publish_heartbeat("STARTING", last_error=warning)

    # Pushed, not left for someone to find. This is the exact condition that ran
    # for hours on 2026-08-01 while the dashboard showed "DB writes ACTIVE", and
    # Telegram does not depend on the database, so the alert still gets out when
    # nothing else can be recorded.
    if status == "UNREACHABLE":

        _notify_operator(
            "database",
            "The scan worker cannot reach Postgres. Alert dedup cannot be "
            "verified, trade history reads as empty, and nothing this container "
            "does will be recorded.",
        )

    return status


# Statuses a process only writes on its way out. Anything else in the predecessor's
# row means it was still claiming to be alive when it died.
_CLEAN_EXIT_STATUSES = frozenset({"STOPPED"})


def _report_restart():
    """Say, once at startup, that this process is replacing a live predecessor.

    The heartbeat is keyed on instance_id, so starting up overwrites the previous
    worker's row and resets `scans` to zero. A clean SIGTERM shutdown writes
    STOPPED first and is therefore self-explanatory; an OOM kill, a crash or a
    hardware pull writes nothing, and the only evidence left after the new process
    publishes is a counter that quietly went backwards. On 2026-08-03 the worker
    restarted mid-session and the only trace was `scans = 0` at 21:42 against a
    last scan at 20:19.

    Reads the predecessor before the first heartbeat overwrites it. Best-effort
    throughout: an unreadable row means no claim is made, because "could not ask"
    must not be reported as "restarted".
    """

    try:
        from app.db.scan_engine_heartbeat_repository import ScanEngineHeartbeatRepository

        previous = ScanEngineHeartbeatRepository().fetch_instance("worker")

    except Exception as exc:
        print(f"[SCAN LOOP WARNING] restart check failed: {exc}")
        return None

    if not previous:
        return None

    status = str(previous.get("status") or "").upper()

    if status in _CLEAN_EXIT_STATUSES:
        print(f"[SCAN LOOP] previous worker exited cleanly ({previous.get('last_error')}).")
        return None

    age = previous.get("age_seconds")
    age_text = f"{float(age) / 60:.0f} minutes ago" if age is not None else "at an unknown time"
    detail = (
        f"Scan worker restarted. The previous process was last seen {age_text} "
        f"in status {status or 'UNKNOWN'} after {previous.get('scans') or 0} scan(s) "
        f"on host {previous.get('hostname') or 'unknown'}, and never recorded a "
        f"shutdown -- so it was killed rather than stopped. Scan counters restart "
        f"from zero."
    )

    print(f"[SCAN LOOP] {detail}")

    # Carried into this process's own heartbeat as well as pushed, so the restart
    # survives in the row even if the alert does not get out.
    _publish_heartbeat(
        "STARTING",
        last_error=detail,
        payload={
            "restarted_from": {
                "status": previous.get("status"),
                "hostname": previous.get("hostname"),
                "scans": previous.get("scans"),
                "failures": previous.get("failures"),
                "last_scan_at": str(previous.get("last_scan_at")),
                "last_error": previous.get("last_error"),
                "age_seconds": age,
            }
        },
    )
    _notify_operator("scan_worker_restart", detail)

    return previous


def _notify_operator(key, message, healthy=False):
    """Monitoring must never be able to stop a scan."""

    try:
        from app.alerts.operator_alerts import notify_operator

        return notify_operator(key, message, healthy=healthy)

    except Exception as exc:
        print(f"[SCAN LOOP WARNING] operator alert failed: {exc}")

        return {"sent": False}


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
    consecutive_failures = 0

    print(
        "[SCAN LOOP] started. "
        + (f"fixed interval {interval_override}s" if interval_override
           else "session-aware cadence " + ", ".join(
               f"{name}={seconds}s" for name, seconds in SESSION_INTERVALS.items()))
    )
    _report_database_state()
    # After the database check so an unreachable database is reported as itself
    # rather than as a failed restart lookup, and before the loop's first
    # heartbeat, which is what overwrites the evidence.
    _report_restart()

    while not _stopping:

        # Ownership is checked here too, so the switch works in both directions.
        # Until now only the dashboard read it: flipping SCAN_ENGINE_OWNER back to
        # `dashboard` -- the obvious move if this worker is down and you want
        # Streamlit scanning for the open -- would have started the supervisor
        # while this process carried on, giving two scanners and the double-open
        # the file-based scan_lock cannot prevent across hosts.
        #
        # Parks rather than exits, so flipping back resumes with no redeploy, and
        # keeps heartbeating so "deliberately standing by" is distinguishable from
        # "dead". An unset variable means no opinion and scans, which is what
        # keeps `python -m app.runtime.scan_loop` working locally.
        declared_owner = str(os.getenv("SCAN_ENGINE_OWNER", "")).strip().lower()

        if declared_owner and declared_owner != "worker":

            wait = interval_for_session(current_session(), interval_override)
            _publish_heartbeat(
                "STANDBY", scans=scans, failures=failures, interval_seconds=wait,
                next_due_at=(datetime.now(ET) + timedelta(seconds=wait)).isoformat(),
            )
            print(
                f"[SCAN LOOP] STANDBY; SCAN_ENGINE_OWNER={declared_owner!r} "
                f"so this worker is not scanning. Sleeping {wait}s."
            )
            _sleep(wait)
            continue

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
            # Idle is the only safe window for a batched DELETE: it competes with
            # the scans writing to the same tables. Self-gated to once per ET
            # date, so this branch looping all weekend runs it once a day.
            _maybe_prune()
            print(f"[SCAN LOOP] {idle}; sleeping {wait}s without scanning.")
            _sleep(wait)
            continue

        started_at = datetime.now(ET)
        _publish_heartbeat("SCANNING", session=session, scans=scans, failures=failures)
        result = _run_one_scan()
        scans += 1

        if not result["ok"]:
            failures += 1
            consecutive_failures += 1
            print(f"[SCAN LOOP ERROR] scan {scans} failed: {result['error']}")

            # Consecutive, not cumulative. One bad cycle is a Polygon hiccup and
            # the loop is built to survive it; three in a row means the session
            # is being missed, and that is worth waking someone for.
            if consecutive_failures >= FAILURES_BEFORE_ALERT:

                _notify_operator(
                    "scan_failures",
                    f"{consecutive_failures} consecutive scans have failed. "
                    f"Latest: {result['error']}",
                )

        else:

            if consecutive_failures >= FAILURES_BEFORE_ALERT:

                _notify_operator(
                    "scan_failures",
                    f"Scanning recovered after {consecutive_failures} failures.",
                    healthy=True,
                )

            consecutive_failures = 0

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
    reason = (f"terminated by signal {_stop_signal}" if _stop_signal
              else f"reached --max-scans={max_scans}" if max_scans
              else "loop ended")
    _publish_heartbeat(
        "STOPPED", scans=scans, failures=failures, last_error=f"stopped: {reason}")

    _announce_shutdown(reason, scans)

    print(f"[SCAN LOOP] stopped after {scans} scan(s), {failures} failure(s) ({reason}).")
    return {"scans": scans, "failures": failures}


def _announce_shutdown(reason, scans):
    """Tell the operator only when the worker dies at a time it should be working.

    A deploy sends SIGTERM every time, so alerting on every shutdown would make
    this channel noise -- and the redeploys are mostly evenings and weekends,
    when nothing is missed. During a live session the same event means scanning
    has stopped and no one would otherwise know until they looked.
    """

    try:
        session = current_session()
        idle = idle_reason(session, datetime.now(ET))

    except Exception:
        return

    if idle:
        return

    _notify_operator(
        "worker_stopped",
        f"The scan worker exited during {session} ({reason}) after {scans} scan(s). "
        "Nothing is scanning until it comes back.",
    )


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
