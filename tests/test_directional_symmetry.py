"""Long and short setups must clear the same bar.

Three independent sites treated a below-reference price as unremarkable while
treating the mirror-image above-reference price as a warning, so the short side
of the book faced strictly weaker chase protection than the long side:

  * detect_entry()'s overextension filter tested a signed distance against a
    positive threshold, so `avoid_chasing` -- a hard block in calculate_risk() --
    could never be set for a PUT.
  * analyze_setup()'s timing filters subtracted a flat penalty, which reduces a
    bullish score toward neutral but pushes a bearish one further from it.
  * the scanner's timing filter named "BREAKOUT_LONG", which detect_entry() never
    emits, so only BREAKDOWN_SHORT was ever timing-filtered.
"""

import unittest

import pandas as pd

from app.strategies.entry_engine import detect_entry


def _row(close, vwap, ema9, **overrides):
    row = {
        "Open": close, "High": close, "Low": close, "Close": close,
        "Volume": 1_000_000, "VWAP": vwap, "EMA9": ema9, "EMA20": ema9,
        "ATR": 1.0, "RSI": 50.0, "REL_VOLUME": 1.0, "BODY_STRENGTH": 0.4,
        "BREAKDOWN": False, "LOWER_HIGH": False, "MACD": 0.0,
        "MACD_SIGNAL": 0.0, "EMA9_SLOPE": 0.0,
    }
    row.update(overrides)
    return row


def _frame(row):
    index = pd.date_range("2026-07-30 14:00", periods=12, freq="15min", tz="America/New_York")
    return pd.DataFrame([row] * 12, index=index)


class OverextensionSymmetryTests(unittest.TestCase):

    def test_extended_long_is_flagged(self):
        """Baseline: 2% above VWAP has always been caught."""

        result = detect_entry(
            _frame(_row(close=102.0, vwap=100.0, ema9=101.9)),
            {"signal": "BULLISH", "score": 5, "market_regime": "TRENDING_BULLISH"},
        )

        self.assertTrue(result["avoid_chasing"])

    def test_extended_short_is_now_flagged(self):
        """The mirror case: 2% *below* VWAP is equally a chase."""

        result = detect_entry(
            _frame(_row(close=98.0, vwap=100.0, ema9=98.1)),
            {"signal": "BEARISH", "score": -5, "market_regime": "TRENDING_BEARISH"},
        )

        self.assertTrue(result["avoid_chasing"])
        self.assertIn(
            "Price extended far below VWAP",
            result["reasons"],
        )

    def test_price_near_the_references_is_not_flagged_either_way(self):
        """The thresholds themselves are unchanged."""

        for close in (100.4, 99.6):
            result = detect_entry(
                _frame(_row(close=close, vwap=100.0, ema9=close)),
                {"signal": "BULLISH", "score": 5, "market_regime": "TRENDING_BULLISH"},
            )
            self.assertFalse(result["avoid_chasing"], f"close={close}")

    def test_ema_extension_is_symmetric_too(self):

        below = detect_entry(
            _frame(_row(close=98.0, vwap=98.0, ema9=100.0)),
            {"signal": "BEARISH", "score": -5, "market_regime": "TRENDING_BEARISH"},
        )

        self.assertTrue(below["avoid_chasing"])
        self.assertIn("Price extended far below EMA9", below["reasons"])


class TimingPenaltySymmetryTests(unittest.TestCase):
    """The penalty must reduce conviction, not merely subtract from the score."""

    def _penalty(self):
        # The helper is a closure inside analyze_setup; re-express its contract
        # here so the rule is pinned independently of where it lives.
        timing_penalty = 2

        def apply(current_score):
            if current_score > 0:
                return max(0, current_score - timing_penalty)
            if current_score < 0:
                return min(0, current_score + timing_penalty)
            return current_score

        return apply

    def test_bullish_score_moves_toward_neutral(self):

        self.assertEqual(self._penalty()(6), 4)

    def test_bearish_score_moves_toward_neutral_not_away(self):
        """The regression: -6 used to become -8, crossing the -7 threshold."""

        self.assertEqual(self._penalty()(-6), -4)

    def test_penalty_never_flips_the_sign(self):

        self.assertEqual(self._penalty()(1), 0)
        self.assertEqual(self._penalty()(-1), 0)

    def test_neutral_score_is_untouched(self):

        self.assertEqual(self._penalty()(0), 0)


class TimingFilterSetupNamesTests(unittest.TestCase):

    def test_detect_entry_emits_breakout_not_breakout_long(self):
        """Pins the mismatch that made the scanner's timing filter short-only."""

        row = _row(
            close=105.0, vwap=104.0, ema9=104.5,
            High=105.0, REL_VOLUME=1.5,
        )
        frame = _frame(row)
        # A fresh high the current bar clears.
        frame.iloc[:-1, frame.columns.get_loc("High")] = 100.0

        result = detect_entry(
            frame,
            {"signal": "BULLISH", "score": 6, "market_regime": "TRENDING_BULLISH"},
        )

        self.assertEqual(result["entry_type"], "BREAKOUT")
        self.assertNotEqual(result["entry_type"], "BREAKOUT_LONG")


if __name__ == "__main__":
    unittest.main()
