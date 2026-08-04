"""Durable store for the auto-paper decision ledger.

Every OPENED, BLOCKED and SKIPPED decision, with the reason, used to exist only as
a CSV under data/daily/ and a 500-row rolling JSON. Streamlit Cloud wipes both on
container recycle, so the record of which gate rejected which candidate -- the one
thing you need to tune a gate -- survived only by luck.

Writes stay best-effort through BestEffortRepository: a DB outage degrades to the
existing file-backed flow and must never delay or block an entry.
"""

from __future__ import annotations

import json

from app.db.persistence import _json_safe
from app.db.repository_base import BestEffortRepository


_INSERT = """
    INSERT INTO auto_paper_decision (
        trading_day, session_id, scan_id, scan_timestamp, market_session,
        is_auto_entry_window, is_after_close, minutes_from_open, minutes_to_close,
        symbol, decision, reason, trade_key, top_candidate, setup_percent,
        candidate_rr, min_rr_used, min_setup_used, setup_valid, realtime_ready,
        action_status, blocked_by, scanner_blocked_by, payload, created_at
    ) VALUES (
        :trading_day, :session_id, :scan_id, :scan_timestamp, :market_session,
        :is_auto_entry_window, :is_after_close, :minutes_from_open, :minutes_to_close,
        :symbol, :decision, :reason, :trade_key, :top_candidate, :setup_percent,
        :candidate_rr, :min_rr_used, :min_setup_used, :setup_valid, :realtime_ready,
        :action_status, :blocked_by, :scanner_blocked_by, CAST(:payload AS JSONB), now()
    )
"""


def _boolean(value):
    """Tri-state: real booleans survive, blanks stay NULL.

    A missing flag must not be recorded as False. "We did not evaluate the
    auto-entry window" and "it was outside the window" are different facts, and
    collapsing them is how a reporting query invents a reason that never applied.
    """

    if value is None or value == "":
        return None

    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()

    if text in {"true", "1", "yes", "y"}:
        return True

    if text in {"false", "0", "no", "n"}:
        return False

    return None


def _number(value):
    if value is None or value == "":
        return None

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    # NaN and inf round-trip through JSON as invalid literals and compare false
    # against every threshold; NULL is the honest representation.
    if result != result or result in (float("inf"), float("-inf")):
        return None

    return result


class AutoPaperDecisionRepository(BestEffortRepository):

    def batch_insert(self, decisions):
        rows = [self._row(entry) for entry in (decisions or []) if entry]
        rows = [row for row in rows if row["symbol"] and row["decision"]]

        return self._batch_execute(_INSERT, rows)

    def insert(self, decision):
        return self.batch_insert([decision])

    def fetch_day(self, trading_day):
        """Full decision ledger for one day, oldest first."""

        return self._fetch("""
            SELECT * FROM auto_paper_decision
            WHERE trading_day = CAST(:trading_day AS date)
            ORDER BY scan_timestamp, symbol
        """, {"trading_day": str(trading_day)})

    def fetch_block_reasons(self, trading_day):
        """Which gate cost the most candidates, for one day."""

        return self._fetch("""
            SELECT decision,
                   COALESCE(blocked_by, reason) AS blocked_by,
                   COUNT(*) AS decisions,
                   COUNT(DISTINCT symbol) AS symbols,
                   AVG(setup_percent) AS avg_setup,
                   AVG(candidate_rr) AS avg_rr
            FROM auto_paper_decision
            WHERE trading_day = CAST(:trading_day AS date)
              AND decision <> 'OPENED'
            GROUP BY 1, 2
            ORDER BY decisions DESC
        """, {"trading_day": str(trading_day)})

    def _row(self, entry):
        entry = entry or {}

        return {
            "trading_day": entry.get("trading_day"),
            "session_id": entry.get("session_id"),
            "scan_id": entry.get("scan_id"),
            # UTC first. `scan_timestamp` is ET wall-clock with no offset, so
            # writing it to a timestamptz column stamps it +00 and puts every row
            # four hours early -- which is what the whole ledger held until this
            # was added. The naive values remain as fallbacks for callers that
            # predate `scan_timestamp_utc`, so those rows stay merely wrong rather
            # than becoming NULL.
            "scan_timestamp": (
                entry.get("scan_timestamp_utc")
                or entry.get("scan_timestamp")
                or entry.get("timestamp")
            ),
            "market_session": entry.get("market_session"),
            "is_auto_entry_window": _boolean(entry.get("is_auto_entry_window")),
            "is_after_close": _boolean(entry.get("is_after_close")),
            "minutes_from_open": _number(entry.get("minutes_from_open")),
            "minutes_to_close": _number(entry.get("minutes_to_close")),
            "symbol": entry.get("symbol"),
            "decision": entry.get("decision"),
            "reason": entry.get("reason"),
            "trade_key": entry.get("trade_key"),
            "top_candidate": entry.get("top_candidate"),
            "setup_percent": _number(entry.get("setup_percent")),
            "candidate_rr": _number(entry.get("rr")),
            "min_rr_used": _number(entry.get("min_rr_used")),
            "min_setup_used": _number(entry.get("min_setup_used")),
            "setup_valid": _boolean(entry.get("setup_valid")),
            "realtime_ready": _boolean(entry.get("realtime_ready")),
            "action_status": entry.get("action_status"),
            "blocked_by": entry.get("blocked_by"),
            "scanner_blocked_by": entry.get("scanner_blocked_by"),
            # Everything the columns do not name, so a later question does not
            # need a migration to answer.
            #
            # _json_safe before json.dumps, not instead of it. psycopg2 cannot adapt
            # a bare dict to jsonb, and json.dumps alone would emit NaN for a float
            # NaN -- which is not valid JSON and which Postgres rejects, failing the
            # whole insert. 2026-07-30's suggested_trade_state carried a literal
            # `top_candidate: NaN`, so this is a live case, not a hypothetical.
            "payload": json.dumps(_json_safe(entry), default=str),
        }
