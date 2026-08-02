"""Why nothing is firing, over a trailing window rather than a whole day.

The postmortem answers "what happened on 2026-08-01". This answers "it is 11am
and nothing has fired, is that the market or is that us" -- a question with no
view anywhere in the dashboard, despite `decision_waterfall` and `gate_decisions`
holding 52k and 25k rows of exactly that.

Windowed in minutes because during the session the last hour is the question and
the day so far is noise: a rule that ate everything from 09:30 to 10:00 and has
been quiet since is a different situation from one eating candidates right now.

Every method returns None on a failed read, never []. A funnel that renders a
database outage as "no candidates today" is worse than no funnel.
"""

from app.db.repository_base import BestEffortRepository


class DecisionFunnelRepository(BestEffortRepository):

    def stage_funnel(self, minutes=60):
        """Per stage: symbols seen, symbols blocked, blocking evaluations.

        Passed is derived as seen minus blocked rather than counted, because a
        stage holds several rules per symbol -- counting rows where `passed` is
        true would count a symbol that cleared four rules and failed the fifth as
        a pass. A symbol passes a stage when nothing in it blocked.
        """

        return self._fetch_optional(
            """
            SELECT stage,
                   MIN(stage_order) AS stage_order,
                   COUNT(DISTINCT symbol) AS symbols_seen,
                   COUNT(DISTINCT symbol) FILTER (WHERE blocking) AS symbols_blocked,
                   COUNT(*) FILTER (WHERE blocking) AS blocks
            FROM decision_waterfall
            WHERE timestamp >= NOW() - make_interval(mins => :minutes)
            GROUP BY stage
            ORDER BY stage_order
            """,
            {"minutes": int(minutes)},
        )

    def blocking_rules(self, minutes=60, limit=12):
        """Which rules are eating candidates right now, worst first."""

        return self._fetch_optional(
            """
            SELECT stage, rule_name,
                   COUNT(*) AS blocks,
                   COUNT(DISTINCT symbol) AS symbols,
                   MAX(required_value) AS required_value,
                   MAX(actual_value) AS worst_actual
            FROM decision_waterfall
            WHERE timestamp >= NOW() - make_interval(mins => :minutes)
              AND blocking IS TRUE
            GROUP BY stage, rule_name
            ORDER BY blocks DESC
            LIMIT :limit
            """,
            {"minutes": int(minutes), "limit": int(limit)},
        )

    def near_misses(self, minutes=60, limit=15):
        """Symbols blocked by exactly one rule.

        The most actionable rows on the page: a candidate failing one gate is a
        threshold question, while one failing six is simply not a setup. Without
        this split the blocking table cannot tell you which it is looking at.
        """

        return self._fetch_optional(
            """
            SELECT symbol,
                   COUNT(*) FILTER (WHERE blocking) AS blocks,
                   MIN(stage) FILTER (WHERE blocking) AS stage,
                   MIN(rule_name) FILTER (WHERE blocking) AS rule_name
            FROM decision_waterfall
            WHERE timestamp >= NOW() - make_interval(mins => :minutes)
            GROUP BY symbol
            HAVING COUNT(*) FILTER (WHERE blocking) = 1
            ORDER BY symbol
            LIMIT :limit
            """,
            {"minutes": int(minutes), "limit": int(limit)},
        )

    def entry_decisions(self, minutes=60):
        """What the auto-paper layer decided, after the scanner let a name through.

        Distinct from the waterfall: a candidate can clear every scanner gate and
        still not be taken, because it was not the top candidate or the daily cap
        was already spent. Those are the reasons an operator most often reads as
        "the scanner found nothing".
        """

        return self._fetch_optional(
            """
            SELECT decision,
                   COALESCE(NULLIF(blocked_by, ''), NULLIF(reason, ''), 'UNSPECIFIED')
                       AS blocked_by,
                   COUNT(*) AS occurrences,
                   COUNT(DISTINCT symbol) AS symbols
            FROM auto_paper_decision
            WHERE scan_timestamp >= NOW() - make_interval(mins => :minutes)
            GROUP BY decision, blocked_by, reason
            ORDER BY occurrences DESC
            """,
            {"minutes": int(minutes)},
        )

    def symbol_waterfall(self, symbol, minutes=180):
        """Every rule evaluated for one symbol, in stage order.

        The per-symbol half of the question. The funnel says which rules blocked
        the most; this says why *this* name did not fire, which is what an
        operator asks after seeing a setup they expected to be taken.
        """

        return self._fetch_optional(
            """
            SELECT stage, stage_order, rule_name, passed, blocking,
                   actual_value, required_value, summary, timestamp
            FROM decision_waterfall
            WHERE UPPER(symbol) = UPPER(:symbol)
              AND timestamp >= NOW() - make_interval(mins => :minutes)
            ORDER BY timestamp DESC, stage_order, rule_name
            """,
            {"symbol": str(symbol), "minutes": int(minutes)},
        )

    def evaluated_symbols(self, minutes=180):
        """Symbols the waterfall saw in the window, for the picker."""

        rows = self._fetch_optional(
            """
            SELECT DISTINCT symbol
            FROM decision_waterfall
            WHERE timestamp >= NOW() - make_interval(mins => :minutes)
              AND symbol IS NOT NULL
            ORDER BY symbol
            """,
            {"minutes": int(minutes)},
        )

        return None if rows is None else [row["symbol"] for row in rows]

    def freshness(self):
        """The most recent scan, so the page can say how old its answer is.

        A funnel with no rows means one of two things -- nothing was evaluated,
        or nothing has scanned -- and only this separates them.
        """

        rows = self._fetch_optional(
            """
            SELECT run_id, status, started_at,
                   EXTRACT(EPOCH FROM (NOW() - started_at)) / 60 AS age_minutes
            FROM scanner_runs
            ORDER BY started_at DESC
            LIMIT 1
            """
        )

        if rows is None:
            return None

        return rows[0] if rows else {}
