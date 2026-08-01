"""Publish "a scan engine is alive here" to Postgres.

The dashboard reads `data/live/scanner_run_status.json` to answer whether
scanning is running. That file is written by the scanner into its own container,
so it only works while the scanner and the dashboard are one process. The
always-on worker migration breaks exactly that assumption: once the scanner runs
on Render, the dashboard on Streamlit Cloud cannot see the file, and the System
panel would show a confident blank rather than an error.

The file writes stay as they are. This is a parallel, best-effort mirror through
the one thing both hosts can see.

**Keyed on owner, not on host.** A Render redeploy changes the hostname, so a
host-keyed table would accumulate a new row per deploy and show phantom duplicate
engines. Owner-keyed means one row per *kind* of engine, which is what the
cutover risk actually is: the worker and the in-Streamlit supervisor both
scanning, double-opening positions that the file-based `scan_lock` cannot
serialise across two filesystems. The cost is that two copies of the same owner
would overwrite each other rather than both showing -- acceptable while the
Render worker runs a single instance.
"""

from __future__ import annotations

import os
import socket


DEFAULT_OWNER = "dashboard"


def scan_engine_owner():
    """Which engine this process is. `worker` once Render owns scanning.

    Also the switch the cutover turns: `_should_start_supervisor` reads it so
    ownership moves by environment variable rather than by deploy, which matters
    because pushes are barred during market hours.
    """

    return str(
        os.getenv("SCAN_ENGINE_OWNER", DEFAULT_OWNER)
    ).strip().lower() or DEFAULT_OWNER


def _hostname():

    for name in ("RENDER_INSTANCE_ID", "RENDER_SERVICE_NAME", "HOSTNAME"):

        value = os.getenv(name)

        if value:

            return value

    try:

        return socket.gethostname()

    except Exception:

        return "unknown"


def build_heartbeat(status, owner=None, **fields):

    owner = owner or scan_engine_owner()
    heartbeat = {
        "instance_id": owner,
        "owner": owner,
        "hostname": _hostname(),
        "status": status,
    }
    heartbeat.update({key: value for key, value in fields.items() if key != "payload"})
    heartbeat["payload"] = fields.get("payload") or {}

    return heartbeat


def record_heartbeat(status, owner=None, **fields):
    """Best effort by design: a failed heartbeat must never stop a scan.

    Returns the heartbeat that was attempted so callers can log it, whether or
    not the write landed.
    """

    heartbeat = build_heartbeat(status, owner=owner, **fields)

    try:

        from app.db.scan_engine_heartbeat_repository import ScanEngineHeartbeatRepository

        ScanEngineHeartbeatRepository().upsert(heartbeat)

    except Exception as exc:

        print(f"[SCAN ENGINE HEARTBEAT WARNING] {exc}")

    return heartbeat


# Reporting, but not scanning. An engine parked on the calendar is not competing
# for the scan lock, so it cannot double-open anything.
# STANDBY: alive and reporting, but SCAN_ENGINE_OWNER names someone else.
IDLE_STATUSES = frozenset({"STOPPED", "STANDBY"})
IDLE_STATUS_PREFIX = "SLEEPING"


def _is_scanning(row):

    status = str((row or {}).get("status") or "").upper()

    return not (status in IDLE_STATUSES or status.startswith(IDLE_STATUS_PREFIX))


def heartbeat_to_engine_status(row):
    """Shape a heartbeat row like `scan_supervisor.status()`.

    One definition, because the UI reads engine health in more than one place and
    two shapes meant two answers: on 2026-08-01 the sidebar correctly reported the
    Render worker while the Operator Console, reading only the local thread, sat
    on ENGINE DOWN and told the operator to restart Streamlit.

    `thread_alive` stays False -- there genuinely is no thread in this process.
    `running` is the question the UI actually wants answered: is *an* engine
    scanning for this deployment.
    """

    row = row or {}

    return {
        "status": row.get("status"),
        "owner": row.get("owner"),
        "session": row.get("session"),
        "interval_seconds": row.get("interval_seconds"),
        "scans": row.get("scans"),
        "failures": row.get("failures"),
        "last_error": row.get("last_error"),
        "last_completed_at": row.get("last_scan_at"),
        "last_duration_seconds": row.get("last_duration_sec"),
        "next_due_at": row.get("next_due_at"),
        "thread_alive": False,
        "running": True,
        "remote": True,
    }


def _stale_after(row, floor_seconds):
    """How long silence from *this* engine is allowed before it means anything.

    A fixed threshold cannot work: the heartbeat lands once per cycle, and the
    cycle is 300s in the regular session but 900s after hours and 1800s when the
    market is shut. Against a flat 900s a perfectly healthy weekend worker looks
    dead, which is precisely the "is it actually running?" ambiguity the
    heartbeat was added to remove.

    Two cycles plus a minute, so one missed beat is tolerated and two are not.
    """

    interval = row.get("interval_seconds")

    try:
        interval = float(interval)
    except (TypeError, ValueError):
        interval = 0.0

    return max(float(floor_seconds), interval * 2 + 60)


def summarize_engines(rows, stale_after_seconds=900):
    """Turn heartbeat rows into what the System panel needs to say.

    Distinguishes states a raw row does not: reporting, gone quiet, and more than
    one engine *actually scanning*. That last one is the cutover failure worth
    shouting about.

    Conflict counts scanning engines, not reporting ones. On the first weekend
    both engines were up and the banner fired, but the dashboard was
    SLEEPING_WEEKEND with zero scans -- parked on the calendar, not competing for
    the scan lock. A banner that cries wolf every weekend is one nobody reads on
    the Monday it matters.
    """

    rows = list(rows or [])
    live, stale = [], []

    for row in rows:

        age = float(row.get("age_seconds") or 0)
        (live if age <= _stale_after(row, stale_after_seconds) else stale).append(row)

    scanning = [row for row in live if _is_scanning(row)]

    return {
        "engines": rows,
        "live": live,
        "stale": stale,
        "scanning": scanning,
        "live_count": len(live),
        "scanning_count": len(scanning),
        "conflict": len(scanning) > 1,
        "owners": sorted({str(row.get("owner") or "unknown") for row in scanning}),
    }
