import unittest

import pandas as pd

from app.strategies.entry_engine import _entry_score, detect_entry


class EntryEngineTests(unittest.TestCase):

    def test_ema_pullback_accepts_moderate_low_distance_from_ema9(self):

        df = pd.DataFrame([
            {
                "Open": 100.5,
                "High": 103.0,
                "Low": 99.1,
                "Close": 101.0,
                "VWAP": 100.0,
                "EMA9": 99.8,
                "EMA20": 98.5,
                "ATR": 2.0,
                "REL_VOLUME": 1.0,
                "BODY_STRENGTH": 0.4,
                "BREAKDOWN": False,
                "LOWER_HIGH": False,
            }
        ] * 20)

        entry = detect_entry(
            df,
            {
                "signal": "BULLISH",
                "score": 7,
                "market_regime": "TRENDING_BULL",
            },
            symbol="ORCL"
        )

        self.assertEqual(entry["entry_type"], "EMA_PULLBACK")

    def test_ema_rejection_short_uses_recent_touch_window(self):

        df = pd.DataFrame([
            {
                "Open": 100,
                "High": 100.5,
                "Low": 98,
                "Close": 98.5,
                "VWAP": 96,
                "EMA9": 99,
                "EMA20": 101,
                "ATR": 2,
                "REL_VOLUME": 1.0,
                "BODY_STRENGTH": 0.4,
                "BREAKDOWN": False,
                "LOWER_HIGH": False,
            },
            {
                "Open": 98.5,
                "High": 99.4,
                "Low": 97,
                "Close": 97.5,
                "VWAP": 96,
                "EMA9": 99,
                "EMA20": 100.5,
                "ATR": 2,
                "REL_VOLUME": 1.0,
                "BODY_STRENGTH": 0.4,
                "BREAKDOWN": False,
                "LOWER_HIGH": False,
            },
            {
                "Open": 97.5,
                "High": 98.1,
                "Low": 96,
                "Close": 96.5,
                "VWAP": 96,
                "EMA9": 98.8,
                "EMA20": 100.2,
                "ATR": 2,
                "REL_VOLUME": 1.0,
                "BODY_STRENGTH": 0.4,
                "BREAKDOWN": False,
                "LOWER_HIGH": False,
            },
        ])

        entry = detect_entry(
            df,
            {
                "signal": "BEARISH",
                "score": -8,
                "market_regime": "TRENDING_BEAR",
            },
        )

        self.assertEqual(entry["entry_type"], "EMA_REJECTION_SHORT")

    def test_entry_score_accepts_short_regime_aliases(self):

        latest = pd.Series({"REL_VOLUME": 1.0})

        score_with_short_alias = _entry_score(
            "EMA_REJECTION_SHORT",
            {"score": -8, "market_regime": "TRENDING_BEAR"},
            latest,
            False,
            "PUT",
        )
        score_with_long_alias = _entry_score(
            "EMA_REJECTION_SHORT",
            {"score": -8, "market_regime": "TRENDING_BEARISH"},
            latest,
            False,
            "PUT",
        )

        self.assertEqual(score_with_short_alias, score_with_long_alias)


if __name__ == "__main__":

    unittest.main()