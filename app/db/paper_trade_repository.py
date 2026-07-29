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
