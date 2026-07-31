"""One definition of what a setup is, and a regime block that actually runs.

Two coupled defects.

Setup names had drifted across five independent lists. `detect_entry` emits five
types; the lists carried up to nine, the extras being detectors commented out in
entry_engine.py. That was not cosmetic: the scanner's entry-timing filter named
"BREAKOUT_LONG", which detect_entry never emits, so only shorts were ever timing
filtered. dashboard.py and paper_automation_support.py also held *different*
copies of REVIEW_VALIDATION_ENTRY_TYPES, one long-only, so the dashboard called a
SPY BREAKDOWN_SHORT ineligible while the scanner would open it.

And the regime block returned a blanket pass for HIGH_VOLATILITY, which the
1,633-row archive shows is 74% of rows -- the 0.45 ATR% cutoff sits at the 25th
percentile of the observed distribution. Nine watchlist symbols were labelled
HIGH_VOLATILITY on 100% of scans, so they had no directional discipline ever.
"""

import unittest

from app.main import _evaluate_regime_setup_block, _trend_regime_from_row
from app.options.option_direction import resolve_option_direction
from app.risk.risk_manager import _is_short_entry
from app.strategies import setup_registry as reg
from app.strategies.entry_engine import detect_entry


class RegistryTests(unittest.TestCase):

    def test_active_setups_are_exactly_what_detect_entry_can_emit(self):

        self.assertEqual(
            reg.ACTIVE_SETUPS,
            {"BREAKOUT", "EMA_PULLBACK", "BREAKDOWN_SHORT",
             "EMA_REJECTION_SHORT", "VWAP_REJECTION"},
        )

    def test_removed_detectors_are_not_claimed_to_exist(self):

        for dead in ("VWAP_RECLAIM", "COILED_BREAKOUT", "COILED_BREAKDOWN",
                     "HIGHER_LOW_CONTINUATION", "BREAKOUT_CONTINUATION"):
            self.assertNotIn(dead, reg.KNOWN_SETUPS, dead)
            self.assertIsNone(reg.setup_direction(dead), dead)

    def test_breakout_long_stays_resolvable(self):
        """Synthesised by the scanner for held positions; must keep working."""

        self.assertIn("BREAKOUT_LONG", reg.KNOWN_SETUPS)
        self.assertEqual(reg.canonical_setup("BREAKOUT_LONG"), "BREAKOUT")
        self.assertEqual(reg.setup_direction("BREAKOUT_LONG"), "CALL")

    def test_every_setup_has_exactly_one_direction(self):

        self.assertEqual(reg.LONG_SETUPS | reg.SHORT_SETUPS, reg.ACTIVE_SETUPS)
        self.assertEqual(reg.LONG_SETUPS & reg.SHORT_SETUPS, frozenset())

    def test_markers_are_not_setups(self):

        for marker in reg.NON_SETUP_MARKERS:
            self.assertFalse(reg.is_tradeable_setup(marker), marker)

    def test_lookup_is_case_and_whitespace_tolerant(self):

        self.assertEqual(reg.setup_direction("  ema_pullback "), "CALL")


class ConsumersAgreeTests(unittest.TestCase):
    """Every consumer must resolve direction the same way."""

    def test_option_direction_matches_the_registry(self):

        for setup, direction in reg.SETUP_DIRECTIONS.items():
            self.assertEqual(
                resolve_option_direction("NEUTRAL", setup), direction, setup
            )

    def test_risk_manager_matches_the_registry(self):

        for setup in reg.SHORT_SETUPS:
            self.assertTrue(_is_short_entry(setup), setup)

        for setup in reg.LONG_SETUPS:
            self.assertFalse(_is_short_entry(setup), setup)

    def test_review_validation_lists_no_longer_disagree(self):
        from app.dashboard import REVIEW_VALIDATION_ENTRY_TYPES as dash
        from app.runtime.paper_automation_support import (
            REVIEW_VALIDATION_ENTRY_TYPES as scanner,
        )

        self.assertEqual(dash, scanner)
        self.assertIn("BREAKDOWN_SHORT", dash)


class RegimeBlockTests(unittest.TestCase):

    def _block(self, entry_type, trend, market="HIGH_VOLATILITY", signal="BULLISH"):
        return _evaluate_regime_setup_block(entry_type, signal, market, trend)

    def test_high_volatility_no_longer_waives_directional_discipline(self):
        """The regression: this returned "not blocked" on 74% of rows."""

        result = self._block("EMA_PULLBACK", trend="TRENDING_BEAR")

        self.assertTrue(result["blocked"])

    def test_bearish_setup_still_blocked_in_a_bull_trend(self):

        self.assertTrue(
            self._block("EMA_REJECTION_SHORT", trend="TRENDING_BULL")["blocked"]
        )

    def test_breakout_needs_the_trend_behind_it(self):

        self.assertTrue(self._block("BREAKOUT", trend="RANGE_BOUND")["blocked"])
        self.assertFalse(self._block("BREAKOUT", trend="TRENDING_BULL")["blocked"])

    def test_continuation_setups_may_work_inside_a_range(self):

        self.assertFalse(self._block("EMA_PULLBACK", trend="RANGE_BOUND")["blocked"])

    def test_low_volatility_still_blocks_both_directions(self):

        for signal in ("BULLISH", "BEARISH"):
            self.assertTrue(
                _evaluate_regime_setup_block(
                    "EMA_PULLBACK", signal, "LOW_VOLATILITY", "TRENDING_BULL"
                )["blocked"],
                signal,
            )

    def test_unknown_trend_does_not_block(self):
        """A regime that could not be computed is not evidence of anything."""

        self.assertFalse(self._block("BREAKOUT", trend="UNKNOWN")["blocked"])


class TrendRegimeTests(unittest.TestCase):
    """Trend classification must survive a wide ATR."""

    def _row(self, close, vwap, ema9, ema20, rsi, atr_pct):
        return {
            "Close": close, "VWAP": vwap, "EMA9": ema9, "EMA20": ema20,
            "RSI": rsi, "ATR_PCT": atr_pct, "MACD": 1.0, "MACD_SIGNAL": 0.5,
        }

    def test_a_trending_name_is_still_trending_at_high_atr(self):
        """SMCI-class names were 100% HIGH_VOLATILITY, so never trend-classified."""

        row = self._row(101, 100, 101, 100, 60, atr_pct=3.5)

        self.assertEqual(_trend_regime_from_row(row), "TRENDING_BULL")

    def test_bear_trend_survives_high_atr(self):
        row = self._row(99, 100, 99, 100, 40, atr_pct=3.5)
        row.update({"MACD": 0.5, "MACD_SIGNAL": 1.0})

        self.assertEqual(_trend_regime_from_row(row), "TRENDING_BEAR")

    def test_no_clear_trend_is_range_bound(self):

        self.assertEqual(
            _trend_regime_from_row(self._row(100, 100, 100, 100, 50, 0.3)),
            "RANGE_BOUND",
        )


class DetectEntryEmissionTests(unittest.TestCase):

    def test_neutral_signal_emits_a_non_setup_marker(self):
        import pandas as pd

        frame = pd.DataFrame(
            [{"Close": 100.0, "VWAP": 100.0, "EMA9": 100.0, "EMA20": 100.0,
              "High": 100.0, "Low": 100.0, "Open": 100.0, "ATR": 1.0,
              "REL_VOLUME": 1.0, "RSI": 50.0}] * 5
        )
        result = detect_entry(frame, {"signal": "NEUTRAL", "score": 0})

        self.assertIn(result["entry_type"], reg.NON_SETUP_MARKERS)


if __name__ == "__main__":
    unittest.main()
