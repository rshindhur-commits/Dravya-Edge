"""The cost that arrives after the entry gate has already passed the trade.

Every gate in this system judges the spread once, at entry. 2026-08-03 showed
that is the wrong end of the trade:

    ORCL    entry 2.70%  ->  exit 3.16%   realised round trip 3.68%
    SMCI    entry 2.65%  ->  exit 4.55%   realised round trip 3.51%
    SMCI    entry 1.12%  ->  exit 2.81%   realised round trip 2.08%

Three of three widened and realised cost exceeded the entry spread in all three.
On the SMCI loss friction was 3.51% against a gross loss of 2.46% -- 59% of the
loss -- and the entry-time gate had cleared it at 1.69x with room to spare,
because at entry the spread genuinely was 2.65%.

The live spread is refreshed on every scan and the entry spread is frozen at
open, so this comparison was always available and nothing made it.
"""

import unittest
from unittest.mock import patch

from app.state import paper_trade_manager
from app.state.paper_trade_manager import (
    _track_spread_widening,
    spread_widening_exit_ratio,
)


class TrackingTests(unittest.TestCase):

    def _trade(self, entry_spread=2.65, live_spread=4.55, **overrides):
        trade = {
            "symbol": "SMCI",
            "option_entry_spread_pct": entry_spread,
            "option_spread_pct": live_spread,
        }
        trade.update(overrides)
        return trade

    def test_the_widening_ratio_is_recorded(self):
        trade = self._trade()

        _track_spread_widening(trade)

        # 4.55 / 2.65 -- the SMCI loss, where friction was 59% of the loss.
        self.assertAlmostEqual(trade["option_spread_widening_ratio"], 1.717, places=3)

    def test_the_peak_ratchets_and_survives_a_recovery(self):
        """A spread that widens and comes back still cost something."""

        trade = self._trade(live_spread=4.55)
        _track_spread_widening(trade)

        trade["option_spread_pct"] = 2.80
        _track_spread_widening(trade)

        self.assertEqual(trade["option_spread_pct_peak"], 4.55)
        self.assertAlmostEqual(trade["option_spread_peak_widening_ratio"], 1.717, places=3)
        # The live ratio does follow the spread back down.
        self.assertAlmostEqual(trade["option_spread_widening_ratio"], 1.057, places=3)

    def test_a_missing_entry_spread_still_tracks_the_peak(self):
        """Trades opened before option_entry_spread_pct existed."""

        trade = self._trade(entry_spread=None)

        _track_spread_widening(trade)

        self.assertEqual(trade["option_spread_pct_peak"], 4.55)
        self.assertNotIn("option_spread_widening_ratio", trade)

    def test_an_absent_live_quote_records_nothing(self):
        trade = self._trade(live_spread=None)

        _track_spread_widening(trade)

        self.assertNotIn("option_spread_pct_peak", trade)


class ObserveOnlyTests(unittest.TestCase):
    """Ships disabled. It records what it would have done and does not do it."""

    def test_the_exit_ratio_defaults_to_off(self):
        with patch("app.state.paper_trade_manager.get_float_env",
                   side_effect=lambda name, default: default):
            self.assertEqual(spread_widening_exit_ratio(), 0.0)

    def test_nothing_is_flagged_while_disabled(self):
        trade = {
            "option_entry_spread_pct": 1.0,
            "option_spread_pct": 50.0,
        }

        with patch.object(paper_trade_manager, "spread_widening_exit_ratio",
                          return_value=0.0):
            _track_spread_widening(trade)

        self.assertNotIn("spread_widening_would_exit", trade)

    def test_crossing_the_ratio_is_recorded_with_the_r_it_happened_at(self):
        trade = {
            "option_entry_spread_pct": 2.65,
            "option_spread_pct": 4.55,
            "rr_progress": -0.41,
        }

        with patch.object(paper_trade_manager, "spread_widening_exit_ratio",
                          return_value=1.5):
            _track_spread_widening(trade)

        self.assertTrue(trade["spread_widening_would_exit"])
        self.assertEqual(trade["spread_widening_would_exit_at_r"], -0.41)

    def test_the_flag_latches_rather_than_recomputing(self):
        """It crossed. A later narrowing does not un-cross it."""

        trade = {
            "option_entry_spread_pct": 2.65,
            "option_spread_pct": 4.55,
            "rr_progress": -0.41,
        }

        with patch.object(paper_trade_manager, "spread_widening_exit_ratio",
                          return_value=1.5):
            _track_spread_widening(trade)

            trade["option_spread_pct"] = 2.70
            trade["rr_progress"] = 0.90
            _track_spread_widening(trade)

        self.assertTrue(trade["spread_widening_would_exit"])
        self.assertEqual(trade["spread_widening_would_exit_at_r"], -0.41)

    def test_below_the_ratio_stays_clear(self):
        trade = {
            "option_entry_spread_pct": 2.70,
            "option_spread_pct": 3.16,
        }

        with patch.object(paper_trade_manager, "spread_widening_exit_ratio",
                          return_value=1.5):
            _track_spread_widening(trade)

        # ORCL: 1.17x, and it made +2.41R. Widening alone is not a verdict.
        self.assertNotIn("spread_widening_would_exit", trade)


if __name__ == "__main__":
    unittest.main()
