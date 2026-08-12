"""Resolve refused candidates once per ET day, while the market is idle.

`tools/resolve_candidate_outcomes.py` answers the question the app could never
answer -- did the candidates we refused go on to win -- but it only answers it
when somebody runs it. Left manual, the measurement that took a day to build
would last exactly as long as the habit of typing the command.

Same home as retention and for the same reason: Streamlit Cloud gives no shell,
so the Render worker's scan loop is where a daily job can actually live.

Two guards, matching `retention_scheduler`. It runs only while the loop is idle,
so the bar fetches and upserts never compete with a live scan. And it runs at
most once per ET date, so an idle branch looping every few minutes all weekend
does not re-run it.

Unlike retention this looks **back a few days rather than at today**. Resolution
needs the bars that came after the decision, so the most recent complete session
is the subject whether the loop wakes at 16:30 on the day or 04:00 the morning
after. Re-resolving a day already done is harmless -- the verdicts are derived
from settled bars and come out identical, and both upserts refuse to downgrade a
resolved row -- so a window wider than one day costs a little cached work and
buys automatic catch-up after an outage. `scanner_snapshot` keeps 21 days; a day
missed for longer than the window is gone, which is why the window is not 1.

The marker is a file only. A lost marker means one extra pass of idempotent work,
which does not justify a migration -- the reasoning `retention_scheduler` records
for its own file marker, and the case here is stronger because nothing is
deleted.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from app.storage.daily_paths import live_path

logger = logging.getLogger(__name__)

MARKER_FILENAME = "outcome_resolution_state.json"

# How many recent sessions to re-resolve on each run. Wide enough to catch up
# after a few days of outage, far short of the 21-day snapshot retention.
LOOKBACK_DAYS = 3


def _marker_path():
    return live_path(MARKER_FILENAME)


def last_run_day():

    try:
        payload = json.loads(_marker_path().read_text(encoding="utf-8"))
        return str(payload.get("last_run_day") or "") or None
    except Exception:
        return None


def _record_run(day, summary):

    try:
        _marker_path().write_text(
            json.dumps(
                {"last_run_day": day, "summary": summary},
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    except Exception:
        logger.warning("could not write outcome resolution marker", exc_info=True)


def due(now, idle_reason_value):
    """True when resolution should run on this pass."""

    if not idle_reason_value:
        return False

    return last_run_day() != now.date().isoformat()


def _days_to_resolve(now):
    """Recent sessions that carry candidates, newest first.

    Taken from the archive rather than from a calendar so holidays and outages
    need no special case: a day with no candidates simply is not in the list.
    """

    from sqlalchemy import text

    from app.db.connection import get_engine

    earliest = (now.date() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    with get_engine().begin() as connection:

        return [
            str(row[0]) for row in connection.execute(text("""
                SELECT DISTINCT trading_day FROM scanner_snapshot
                WHERE trading_day >= CAST(:earliest AS DATE)
                  AND decision_payload ->> 'Candidate Entry Price' IS NOT NULL
                ORDER BY trading_day DESC
            """), {"earliest": earliest})
        ]


def maybe_resolve_outcomes(now, idle_reason_value):
    """Resolve recent sessions if due. Returns a summary, or None when skipped.

    Never allowed to raise. A measurement that fails must not stop the scanner:
    an unresolved day can be recovered from the archive tomorrow, a missed
    session cannot.
    """

    if not due(now, idle_reason_value):
        return None

    day = now.date().isoformat()

    try:

        from tools.resolve_candidate_outcomes import (
            bridge_to_evidence,
            run_day,
        )

        summary = {"days": {}, "target_first": 0, "stop_first": 0}

        for trading_day in _days_to_resolve(now):

            rows = run_day(trading_day, write=True) or []

            target = sum(1 for row in rows if row["target_hit"])
            stop = sum(1 for row in rows if row["stop_hit"])

            summary["days"][trading_day] = {
                "candidates": len(rows),
                "target_first": target,
                "stop_first": stop,
            }
            summary["target_first"] += target
            summary["stop_first"] += stop

        summary["bridged"] = bridge_to_evidence()
        _record_run(day, summary)

        return summary

    except Exception:

        logger.warning("outcome resolution failed", exc_info=True)
        return None
