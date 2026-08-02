"""Everything a postmortem needs about one trading day, from Postgres.

Answering "what happened on 2026-08-01" meant writing SQL by hand against
`scanner_runs`, `telegram_dispatch` and `alert_events` and correlating timestamps
in a terminal. That is how a Saturday incident took six hours: not because the
data was missing -- all of it was already here -- but because there was no way to
ask.

Every method returns `None` when the read fails, never `[]`. The whole reason
this module exists is an incident where a failed read was rendered as "nothing
happened", so a postmortem tool that repeats that mistake is worse than none.

Trading days are ET. `started_at` and friends are TIMESTAMPTZ stored in UTC, and
an ET session crosses a UTC date boundary, so every day filter converts before
comparing rather than truncating the raw timestamp.
"""

from app.db.repository_base import BestEffortRepository


ET = "America/New_York"


class DayPostmortemRepository(BestEffortRepository):

    def scans(self, trading_day):
        """One row per scan, in order, with duration.

        A run writes a STARTED row and later a FINISHED one, so a plain SELECT
        returns each scan twice and reports the STARTED half as a failure -- 120
        scans and 79 "failures" for a day that had neither. `DISTINCT ON` keeps
        the terminal row per `run_id`, preferring FINISHED, so a run still in
        flight or abandoned survives as STARTED rather than vanishing.
        """

        return self._fetch_optional(
            f"""
            SELECT * FROM (
                SELECT DISTINCT ON (run_id)
                       run_id, status, rows_count, started_at, finished_at,
                       EXTRACT(EPOCH FROM (finished_at - started_at)) AS duration_sec
                FROM scanner_runs
                WHERE (started_at AT TIME ZONE '{ET}')::date = CAST(:day AS date)
                ORDER BY run_id, (status = 'FINISHED') DESC, started_at DESC
            ) AS runs
            ORDER BY started_at
            """,
            {"day": str(trading_day)},
        )

    def alerts(self, trading_day):
        """Dispatches by type and status -- what subscribers actually received."""

        return self._fetch_optional(
            f"""
            SELECT message_type, status, COUNT(*) AS dispatches,
                   COUNT(*) FILTER (WHERE delivered) AS delivered,
                   MIN(timestamp) AS first_at, MAX(timestamp) AS last_at
            FROM telegram_dispatch
            WHERE (timestamp AT TIME ZONE '{ET}')::date = CAST(:day AS date)
            GROUP BY message_type, status
            ORDER BY dispatches DESC
            """,
            {"day": str(trading_day)},
        )

    def trades(self, trading_day):
        """Positions opened or closed on the day.

        Either side qualifies: a position opened yesterday and closed today
        belongs in today's postmortem, and one opened today and still open is
        exactly what an end-of-day review needs to see.
        """

        return self._fetch_optional(
            f"""
            SELECT symbol, direction, status, entry_source, holding_profile,
                   entry_price, close_price, pnl_pct, r_multiple,
                   opened_at, closed_at
            FROM paper_trades
            WHERE (opened_at AT TIME ZONE '{ET}')::date = CAST(:day AS date)
               OR (closed_at AT TIME ZONE '{ET}')::date = CAST(:day AS date)
            ORDER BY opened_at
            """,
            {"day": str(trading_day)},
        )

    def entry_decisions(self, trading_day):
        """Why entries were and were not taken, grouped by reason.

        `auto_paper_decision` carries its own `trading_day`, so no conversion is
        needed here.
        """

        return self._fetch_optional(
            """
            SELECT decision,
                   COALESCE(NULLIF(blocked_by, ''), NULLIF(reason, ''), 'UNSPECIFIED')
                       AS blocked_by,
                   COUNT(*) AS occurrences,
                   COUNT(DISTINCT symbol) AS symbols
            FROM auto_paper_decision
            WHERE trading_day = CAST(:day AS date)
            GROUP BY decision, blocked_by, reason
            ORDER BY occurrences DESC
            """,
            {"day": str(trading_day)},
        )

    def blocking_rules(self, trading_day, limit=15):
        """Which rules actually stopped candidates, most frequent first.

        The waterfall records every stage for every symbol on every scan, so it
        is the largest table here by an order of magnitude. Only blocking rows
        answer "why did nothing trade", so only those are read.
        """

        return self._fetch_optional(
            f"""
            SELECT stage, rule_name,
                   COUNT(*) AS blocks,
                   COUNT(DISTINCT symbol) AS symbols
            FROM decision_waterfall
            WHERE (timestamp AT TIME ZONE '{ET}')::date = CAST(:day AS date)
              AND blocking IS TRUE
            GROUP BY stage, rule_name
            ORDER BY blocks DESC
            LIMIT :limit
            """,
            {"day": str(trading_day), "limit": int(limit)},
        )

    def entries_intended(self, trading_day):
        """Decisions that said take it, with the key they claimed to open."""

        return self._fetch_optional(
            """
            SELECT symbol, decision, trade_key, scan_timestamp
            FROM auto_paper_decision
            WHERE trading_day = CAST(:day AS date)
              AND UPPER(decision) IN ('ENTER', 'ENTER_PAPER', 'TAKEN', 'OPENED')
            ORDER BY scan_timestamp
            """,
            {"day": str(trading_day)},
        )

    def entries_recorded(self, trading_day):
        """Positions the book actually holds for the day."""

        return self._fetch_optional(
            f"""
            SELECT trade_key, symbol, status, entry_source, opened_at
            FROM paper_trades
            WHERE (opened_at AT TIME ZONE '{ET}')::date = CAST(:day AS date)
            ORDER BY opened_at
            """,
            {"day": str(trading_day)},
        )

    def alert_suppressions(self, trading_day, limit=15):
        """Alerts considered and not sent, by reason.

        Distinct from `alerts()`: that is what went out, this is what did not.
        A day with no alerts is either a quiet market or a suppression rule doing
        more than intended, and only this separates the two.
        """

        return self._fetch_optional(
            f"""
            SELECT alert_type, status,
                   COALESCE(NULLIF(reason, ''), 'UNSPECIFIED') AS reason,
                   COUNT(*) AS occurrences
            FROM alert_events
            WHERE (created_at AT TIME ZONE '{ET}')::date = CAST(:day AS date)
            GROUP BY alert_type, status, reason
            ORDER BY occurrences DESC
            LIMIT :limit
            """,
            {"day": str(trading_day), "limit": int(limit)},
        )
