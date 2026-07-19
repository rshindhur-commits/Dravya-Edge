import unittest

from app.analytics.trade_snapshot import build_trade_snapshot
from app.analytics.trend_health import evaluate_trend_health


class TradeLifecycleSnapshotTests(unittest.TestCase):

    def test_trend_health_scores_strong_long_snapshot(self):

        health = evaluate_trend_health({
            "ema_alignment": True,
            "price_above_ema9": True,
            "price_above_vwap": True,
            "higher_high": True,
            "higher_low": True,
            "macd_bullish": True,
            "rsi": 61,
            "relative_volume": 1.4,
        })

        self.assertEqual(health["score"], 12)
        self.assertEqual(health["state"], "STRONG")

    def test_trend_health_scores_broken_snapshot(self):

        health = evaluate_trend_health({
            "ema_alignment": False,
            "price_above_ema9": False,
            "price_above_vwap": False,
            "higher_high": False,
            "higher_low": False,
            "macd_bullish": False,
            "rsi": 42,
            "relative_volume": 0.8,
        })

        self.assertEqual(health["score"], 0)
        self.assertEqual(health["state"], "BROKEN")

    def test_build_trade_snapshot_includes_exit_indicators(self):

        snapshot = build_trade_snapshot(
            {
                "trade_key": "NVDA|CALL|2026-07-17 10:00:00",
                "symbol": "NVDA",
                "direction": "CALL",
                "entry_type": "EMA_PULLBACK",
                "opened_at": "2026-07-17 10:00:00",
                "closed_at": "2026-07-17 10:30:00",
                "entry_price": 100,
                "close_price": 106,
                "exit_reason": "EMA",
                "bars_held": 6,
            },
            {
                "Close": 106,
                "EMA9": 104,
                "EMA20": 101,
                "VWAP": 103,
                "MACD": 1.2,
                "MACD_SIGNAL": 1.0,
                "RSI": 62,
                "ATR": 2,
                "Volume": 100000,
                "REL_VOLUME": 1.5,
                "HIGHER_HIGH": True,
                "HIGHER_LOW": True,
            },
            {},
            {
                "score": 12,
                "state": "STRONG",
            }
        )

        self.assertEqual(snapshot["trade_key"], "NVDA|CALL|2026-07-17 10:00:00")
        self.assertEqual(snapshot["setup"], "EMA_PULLBACK")
        self.assertEqual(snapshot["ema9"], 104)
        self.assertTrue(snapshot["price_above_ema9"])
        self.assertEqual(snapshot["trend_health_state"], "STRONG")


if __name__ == "__main__":

    unittest.main()