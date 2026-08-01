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


def summarize_engines(rows, stale_after_seconds=900):
    """Turn heartbeat rows into what the System panel needs to say.

    Distinguishes three states that a raw row does not: reporting, gone quiet,
    and more than one engine claiming to scan. The third is the cutover failure
    worth shouting about.
    """

    rows = list(rows or [])
    live = [row for row in rows if (row.get("age_seconds") or 0) <= stale_after_seconds]
    stale = [row for row in rows if (row.get("age_seconds") or 0) > stale_after_seconds]

    return {
        "engines": rows,
        "live": live,
        "stale": stale,
        "live_count": len(live),
        "conflict": len(live) > 1,
        "owners": sorted({str(row.get("owner") or "unknown") for row in live}),
    }
