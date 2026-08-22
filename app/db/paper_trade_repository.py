import json

from app.db.repository_base import BestEffortRepository

class PaperTradeRepository(BestEffortRepository):
    def upsert(self, trade):
        trade = trade or {}
        return self._execute(
            """
            INSERT INTO paper_trades (
                trade_key, symbol, direction, option_ticker, status, entry_source,
                entry_price, option_entry_mid, close_price, option_close_mid,
                pnl_pct, r_multiple, payload, opened_at, closed_at,
                holding_profile, overnight_count, days_held, forced_eod_exit,
                session_id_open, session_id_close
            ) VALUES (
                :trade_key, :symbol, :direction, :option_ticker, :status, :entry_source,
                :entry_price, :option_entry_mid, :close_price, :option_close_mid,
                :pnl_pct, :r_multiple, CAST(:payload AS JSONB), :opened_at, :closed_at,
                :holding_profile, :overnight_count, :days_held, :forced_eod_exit,
                :session_id_open, :session_id_close
            ) ON CONFLICT (trade_key) DO UPDATE SET
                status = EXCLUDED.status,
                close_price = EXCLUDED.close_price,
                option_close_mid = EXCLUDED.option_close_mid,
                pnl_pct = EXCLUDED.pnl_pct,
                r_multiple = EXCLUDED.r_multiple,
                payload = EXCLUDED.payload,
                closed_at = EXCLUDED.closed_at,
                holding_profile = EXCLUDED.holding_profile,
                overnight_count = EXCLUDED.overnight_count,
                days_held = EXCLUDED.days_held,
                forced_eod_exit = EXCLUDED.forced_eod_exit,
                session_id_close = EXCLUDED.session_id_close,
                updated_at = NOW()
            """,
            {
                "trade_key": trade.get("trade_key") or trade.get("trade_id"),
                "symbol": trade.get("symbol"),
                "direction": trade.get("direction"),
                "option_ticker": trade.get("option_ticker"),
                "status": trade.get("status"),
                "entry_source": trade.get("entry_source"),
                "entry_price": trade.get("entry_price"),
                "option_entry_mid": trade.get("option_entry_mid") or trade.get("option_mid"),
                "close_price": trade.get("close_price"),
                "option_close_mid": trade.get("option_current_mid"),
                "pnl_pct": trade.get("pnl_pct"),
                "r_multiple": trade.get("r_multiple"),
                "payload": json.dumps(trade, default=str),
                "opened_at": trade.get("opened_at_et") or trade.get("opened_at"),
                "closed_at": trade.get("closed_at_et") or trade.get("closed_at"),
                "holding_profile": trade.get("holding_profile"),
                "overnight_count": trade.get("overnight_count", 0),
                "days_held": trade.get("days_held", 1),
                "forced_eod_exit": bool(trade.get("forced_eod_exit")),
                "session_id_open": trade.get("session_id_open"),
                "session_id_close": trade.get("session_id_close"),
            },
        )
    def insert(self, trade): return self.upsert(trade)
    def batch_insert(self, trades): return sum(bool(self.upsert(t)) for t in (trades or []))
    def get(self, *_args, **_kwargs): return None

    def fetch_open(self):
        """Every position the database still believes is open.

        Not filtered by trading day on purpose: a MULTIDAY position opened days ago
        is exactly the one worth recovering, and filtering by date would silently
        drop it.
        """

        rows = self._fetch_optional(
            """
            SELECT trade_key, payload
            FROM paper_trades
            WHERE UPPER(status) IN ('OPEN', 'PAUSED')
            ORDER BY opened_at
            """
        )

        # None on failure, so the caller can tell "the database says no open
        # positions" from "the database did not answer". Restoring acts on that
        # answer, and acting on a failed read means opening a position the book
        # already holds.
        if rows is None:

            return None

        return [
            {"trade_key": row.get("trade_key"), "payload": row.get("payload") or {}}
            for row in rows
        ]

    def count_opened_on(self, trading_day):
        """Positions opened on a trading day, or None when unreadable.

        Backs the daily entry cap on a host that did not open the trades. The
        file-based count is per-container, so the dashboard reported `0/5` while
        the worker had used its allowance.
        """

        rows = self._fetch_optional(
            """
            SELECT COUNT(DISTINCT trade_key) AS opened
            FROM paper_trades
            WHERE opened_at >= CAST(:day AS date)
              AND opened_at <  CAST(:day AS date) + INTERVAL '1 day'
            """,
            {"day": str(trading_day)},
        )

        if rows is None:

            return None

        return int((rows[0] or {}).get("opened") or 0)

    def fetch_closed_between(self, start_day, end_day):
        """Closed trades over an inclusive date range, flattened like `fetch_closed`.

        The weekly subscriber summary needs a week at a time, and calling
        `fetch_closed` seven times would be seven round trips to Neon for a report
        that runs once. `end_day` is inclusive: callers think in trading days, not
        half-open intervals, and an exclusive bound silently drops Friday.

        Exit quality is joined in rather than read from the payload. `_flatten_closed`
        lifted `trend_capture` and `left_on_table` out of `paper_trades.payload`,
        where **nothing has ever written them** -- the exit-analysis path writes to
        `trade_exit_analysis` instead. So Validation's efficiency panel showed a
        dash for both, indefinitely, under a caption describing what they meant.
        Adding the names to the lift list is the tempting non-fix: the source has
        no such column.

        `DISTINCT ON` because a trade re-analysed on a later day would otherwise
        fan the join out and double-count the trade in every average on the page.

        The subquery is date-bounded so it uses `trade_exit_analysis_day_idx`
        rather than scanning and sorting the whole table on every window read --
        unbounded, its cost would grow with the archive forever while the answer
        stayed the size of the window. The ±7-day margin covers a trade analysed
        a few days after it closed; the review runs post-market on the close day,
        so that is slack, not the expected case.

        Not applied to `fetch_closed`. That one serves the same trading day, and
        `trade_exit_analysis` is written by the post-market review, so intraday the
        join has nothing to match -- and its rows feed the learning engine, whose
        numbers should not start moving as a side effect of a display fix.
        """

        rows = self._fetch_optional(
            """
            SELECT p.symbol, p.direction, p.status, p.entry_price, p.close_price,
                   p.pnl_pct, p.r_multiple, p.option_entry_mid, p.option_close_mid,
                   p.holding_profile, p.opened_at, p.closed_at, p.payload,
                   exit_analysis.trend_capture_pct AS trend_capture,
                   exit_analysis.left_on_table
            FROM paper_trades AS p
            LEFT JOIN (
                SELECT DISTINCT ON (trade_key)
                       trade_key, trend_capture_pct, left_on_table
                FROM trade_exit_analysis
                WHERE trading_day >= CAST(:start_day AS date) - INTERVAL '7 days'
                  AND trading_day <  CAST(:end_day AS date) + INTERVAL '8 days'
                ORDER BY trade_key, recorded_at DESC
            ) AS exit_analysis
              ON exit_analysis.trade_key = p.payload->>'trade_key'
            WHERE p.closed_at IS NOT NULL
              AND p.closed_at >= CAST(:start_day AS date)
              AND p.closed_at < CAST(:end_day AS date) + INTERVAL '1 day'
            ORDER BY p.closed_at
            """,
            {"start_day": str(start_day), "end_day": str(end_day)},
        )

        # None survives to the caller. This feeds the subscriber summary, which
        # states a result rather than rendering a table, so "the query failed"
        # must not arrive there as "nothing happened".
        return None if rows is None else self._flatten_closed(rows)

    def _flatten_closed(self, rows):
        """Lift the fields analytics needs out of the JSONB payload."""

        flattened = []

        for row in rows or []:
            payload = row.get("payload") or {}
            record = {key: value for key, value in row.items() if key != "payload"}

            for key in (
                "setup", "entry_type", "regime", "decision", "exit_reason",
                "bars_in_trade", "holding_profile", "entry_source",
                "option_pnl_pct", "option_pnl_pct_net", "option_spread_cost_pct",
                "trend_capture", "left_on_table", "mfe_r",
                # The premium block's admission ticket. `_premium_measurable`
                # gates on `option_entry_ask` and returns an empty frame when the
                # column is absent, so leaving it in the payload made
                # `priced_trades` structurally zero: 17 measurable trades read as
                # none, the Validation page dropped its whole cash-terms row, and
                # the spread panel announced it was still waiting for the first
                # closed trade since eb56f75. The other two are what
                # `build_spread_calibration` compares -- score against what the
                # round trip actually cost.
                "option_entry_ask", "option_entry_spread_pct",
                "option_quality_score",
                # The book in cash. Percent P&L says nothing about size, and on
                # the first clean window one position (-$207) outweighed every
                # other trade combined (+$64) while the page reported a tidy
                # -6.7% average.
                "option_pl_dollars", "option_contracts",
                # Written at open, and until now never read anywhere. See
                # build_performance_statistics().
                "include_in_strategy_stats",
                # The exit engine's own confidence, refreshed on every exit
                # evaluation, so on a closed trade it holds the last score before
                # the close. `daily_engine_summary.avg_exit_confidence` reported
                # NULL every day it has ever run because it was reading this off
                # a frame that never carried it; see build_daily_learning_summary.
                "last_exit_confidence_score",
            ):
                if record.get(key) is None:
                    record[key] = payload.get(key)

            record.setdefault("trade_status", row.get("status"))
            flattened.append(record)

        return flattened

    def fetch_closed(self, trading_day):
        """Closed trades for a trading day, flattened for the analytics layer.

        The learning engine used to source completed trades only from
        `paper_trade_events.csv` under data/daily/. On Streamlit Cloud that
        directory is ephemeral and is wiped whenever the container restarts, so the
        file was routinely absent and every learning metric reported
        `completed_trades: 0`. `exit_quality_metrics` last recorded anything on
        2026-07-25 for exactly this reason -- the trades were real, the measurement
        was reading a file that no longer existed.

        Postgres survives the restart, so it is the durable source. Fields the
        analytics layer needs live in the JSONB payload; they are lifted to the top
        level here so callers can treat this like the CSV it replaces.
        """

        return self._flatten_closed(
            self._fetch(
                """
                SELECT symbol, direction, status, entry_price, close_price,
                       pnl_pct, r_multiple, option_entry_mid, option_close_mid,
                       holding_profile, opened_at, closed_at, payload
                FROM paper_trades
                WHERE closed_at IS NOT NULL
                  AND closed_at >= CAST(:trading_day AS date)
                  AND closed_at < CAST(:trading_day AS date) + INTERVAL '1 day'
                ORDER BY closed_at
                """,
                {"trading_day": str(trading_day)},
            )
        )
