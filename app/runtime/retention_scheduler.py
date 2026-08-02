"""Run retention once per ET calendar day, while the market is idle.

Deliberately not a cron: Streamlit Cloud gives no shell and the Render worker is
the only process guaranteed to be running daily, so the scan loop is where a
daily job can actually live.

Two guards decide when it fires. It runs only while the loop is idle -- market
closed, weekend, or holiday -- because a batched DELETE against
`activity_trace_event` competes with the scans writing to it, and there is no
reason to pay that during a session. And it runs at most once per ET date, so
the idle branch looping every few minutes all weekend does not re-run it.

The marker is a file, not a table. Retention is idempotent -- deleting rows
older than N days twice removes nothing the second time -- so the worst case
from a lost marker on a container restart is one extra pass of cheap COUNT
queries, which is not worth a migration to prevent.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from app.storage.daily_paths import live_path

logger = logging.getLogger(__name__)

MARKER_FILENAME = "retention_state.json"


def _marker_path():
    return live_path(MARKER_FILENAME)


def last_run_day():
    try:
        payload = json.loads(_marker_path().read_text(encoding="utf-8"))
        return str(payload.get("last_run_day") or "") or None
    except Exception:
        return None


def _record_run(day, report):
    try:
        _marker_path().write_text(
            json.dumps(
                {
                    "last_run_day": day,
                    "ran_at": datetime.now().isoformat(),
                    "total_deleted": report.get("total_deleted", 0),
                    "tables": {
                        table: row.get("deleted", 0)
                        for table, row in (report.get("tables") or {}).items()
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        logger.warning("could not write retention marker", exc_info=True)


def due(now, idle_reason_value):
    """True when retention should run on this pass."""

    if not idle_reason_value:
        return False

    return last_run_day() != now.date().isoformat()


def maybe_run_retention(now, idle_reason_value, vacuum_after=True):
    """Run retention if due. Returns the report, or None when skipped.

    Never allowed to raise. A failure to prune must not stop the scan loop --
    a bounded database is worth less than a running scanner.
    """
    if not due(now, idle_reason_value):
        return None

    day = now.date().isoformat()

    try:
        from app.db.retention import run_retention, vacuum

        report = run_retention(dry_run=False)

        if report.get("skipped"):
            logger.info("retention skipped: %s", report["skipped"])
            return report

        # Marker is written before the vacuum so a slow or failed VACUUM does
        # not cause the deletes to be repeated on the next pass.
        _record_run(day, report)

        if vacuum_after and report.get("total_deleted"):
            vacuum()

        return report

    except Exception:
        logger.warning("retention run failed", exc_info=True)
        return None
