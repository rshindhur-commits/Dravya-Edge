"""Run retention once per ET calendar day, while the market is idle.

Deliberately not a cron: Streamlit Cloud gives no shell and the Render worker is
the only process guaranteed to be running daily, so the scan loop is where a
daily job can actually live.

Two guards decide when it fires. It runs only while the loop is idle -- market
closed, weekend, or holiday -- because a batched DELETE against
`activity_trace_event` competes with the scans writing to it, and there is no
reason to pay that during a session. And it runs at most once per ET date, so
the idle branch looping every few minutes all weekend does not re-run it.

The marker was a file and is now both. The original reasoning was that retention
is idempotent -- deleting rows older than N days twice removes nothing the second
time -- so a marker lost to a container restart costs one extra pass of cheap
COUNT queries, and that did not justify a migration. On correctness that still
holds.

What it missed is that it left no way to answer "did retention run". On
2026-08-08 the only available check was to query every retained table for rows
past its window and infer the answer from their absence. The run is now recorded
in `retention_run` as well, so the question has an answer instead of an
inference.

The database is read first and the file kept as a fallback, which also makes the
once-a-day guard survive a restart -- while the worker was restarting hourly on
the memory leak, the marker reset with it and retention ran on most idle passes
rather than once a day.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import bindparam

from app.storage.daily_paths import live_path

logger = logging.getLogger(__name__)

MARKER_FILENAME = "retention_state.json"


def _marker_path():
    return live_path(MARKER_FILENAME)


def _last_run_day_from_db():
    """The most recent recorded run, or None when unreachable or unrecorded.

    Best effort in both directions: an unreachable database must not stop
    retention, and must not silently answer "never ran" either -- the caller
    falls through to the file rather than treating None as a fact.
    """

    try:

        from sqlalchemy import text

        from app.db.connection import get_engine

        with get_engine().begin() as connection:

            day = connection.execute(
                text("SELECT max(ran_on)::text FROM retention_run")
            ).scalar()

        return day or None

    except Exception:

        return None


def last_run_day():

    return _last_run_day_from_db() or _last_run_day_from_file()


def _last_run_day_from_file():
    try:
        payload = json.loads(_marker_path().read_text(encoding="utf-8"))
        return str(payload.get("last_run_day") or "") or None
    except Exception:
        return None


def _record_run_in_db(day, report):

    try:

        from sqlalchemy import text
        from sqlalchemy.dialects.postgresql import JSONB

        from app.db.connection import get_engine

        statement = text("""
            INSERT INTO retention_run (ran_on, total_deleted, report)
            VALUES (CAST(:ran_on AS DATE), :total_deleted, :report)
        """).bindparams(bindparam("report", type_=JSONB))

        with get_engine().begin() as connection:

            connection.execute(
                statement,
                {
                    "ran_on": day,
                    "total_deleted": int(report.get("total_deleted") or 0),
                    "report": {
                        table: row.get("deleted", 0)
                        for table, row in (report.get("tables") or {}).items()
                    },
                },
            )

    except Exception:

        # A missing table on an un-migrated deployment lands here, and retention
        # itself is unaffected. The file marker still records the run.
        logger.warning("could not record retention run", exc_info=True)


def _record_run(day, report):
    _record_run_in_db(day, report)

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
