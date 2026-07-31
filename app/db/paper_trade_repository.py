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

        return [
            {"trade_key": row.get("trade_key"), "payload": row.get("payload") or {}}
            for row in self._fetch(
                """
                SELECT trade_key, payload
                FROM paper_trades
                WHERE UPPER(status) IN ('OPEN', 'PAUSED')
                ORDER BY opened_at
                """
            )
        ]

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

        rows = self._fetch(
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

        flattened = []

        for row in rows:
            payload = row.get("payload") or {}
            record = {key: value for key, value in row.items() if key != "payload"}

            for key in (
                "setup", "entry_type", "regime", "decision", "exit_reason",
                "bars_in_trade", "holding_profile", "entry_source",
                "option_pnl_pct", "option_pnl_pct_net", "option_spread_cost_pct",
                "trend_capture", "left_on_table", "mfe_r",
                # Written at open, and until now never read anywhere. See
                # build_performance_statistics().
                "include_in_strategy_stats",
            ):
                if record.get(key) is None:
                    record[key] = payload.get(key)

            record.setdefault("trade_status", row.get("status"))
            flattened.append(record)

        return flattened
