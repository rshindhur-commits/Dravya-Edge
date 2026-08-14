"""The target may be floored on the risk actually taken.

Every target in `calculate_risk` is an absolute distance -- 1.8 ATR for
EMA_PULLBACK, `target_atr_multiplier` elsewhere -- while the stop floats with
structure. RR therefore reduces to 1.8 / stop_in_ATR, so clearing a 2.0 bar
requires a stop under 0.9 ATR. That only happens when price sits on the EMA,
which is early in a move, before it has proven anything.

The consequence is that the strategy cannot join a trend already underway.
Measured 2026-08-13: NFLX rallied all afternoon and produced 19 LOW_RR blocks.
The one setup with geometry recorded was entry 77.29, stop 76.86, target 77.96
-- risk 1.16 ATR, reward 1.8 ATR, RR 1.55, refused. Price reached 78.64, so a
target derived from the risk taken would also have been reached.

TARGET_MIN_RR extends the target to at least that multiple of the risk. Off at
0. These cover the default (nothing moves), the extension, the direction
handling, and the rule that it may never pull a target closer.
"""

import os
import unittest
from unittest.mock import patch

import pandas as pd

from app.risk.risk_manager import calculate_risk


def _frame(**overrides):
    """A bar set whose EMA_PULLBACK geometry lands RR between 1.5 and 2.0."""

    # Calibrated to reproduce NFLX's 2026-08-13 geometry: stop 1.16 ATR from
    # entry against a target pinned at 1.8 ATR, giving RR 1.55.
    row = {
        "High": 101.25,
        "Low": 97.98,
        "Close": 100.00,
        "ATR": 2.00,
        "EMA9": 98.50,
        "VWAP": 99.40,
        "ROLLING_RESISTANCE": 102.20,
        "ROLLING_SUPPORT": 98.00,
        "PREV_HIGH": 103.00,
        "PREV_LOW": 98.50,
    }
    row.update(overrides)
    return pd.DataFrame([row] * 20)


def _risk(df, entry_type="EMA_PULLBACK", signal="BULLISH",
          regime="TRENDING_BULL"):

    return calculate_risk(
        df=df,
        analysis={"signal": signal, "market_regime": regime},
        entry_setup={
            "entry_type": entry_type,
            "entry_quality": "HIGH",
            "avoid_chasing": False,
        },
    )


class DefaultOffTests(unittest.TestCase):

    def test_the_target_is_untouched_by_default(self):
        """Absent the env var nothing about the geometry may move."""

        df = _frame()

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TARGET_MIN_RR", None)
            baseline = _risk(df)

        with patch.dict(os.environ, {"TARGET_MIN_RR": "0"}):
            explicit_off = _risk(df)

        self.assertEqual(baseline["take_profit"], explicit_off["take_profit"])
        self.assertEqual(baseline["stop_loss"], explicit_off["stop_loss"])


class ExtensionCapTests(unittest.TestCase):
    """The cap is what stops this becoming a tautology.

    Uncapped, the extension sets RR to exactly TARGET_MIN_RR for every
    candidate that reaches it -- a true RR of 0.95 had its target pushed two
    full ATR to manufacture 2.0. The gate would then be checking a number the
    target was just adjusted to satisfy, and the worst geometry would receive
    the least reachable targets.
    """

    def _rr(self, df, min_rr, cap):
        with patch.dict(os.environ, {
            "TARGET_MIN_RR": str(min_rr),
            "TARGET_MAX_REWARD_ATR": str(cap),
        }):
            return _risk(df)["risk_reward"]

    def test_geometry_far_below_the_bar_is_not_rescued(self):
        """RR 0.95 must stay refused, not be handed a two-ATR target."""

        df = _frame(Low=96.50, EMA9=97.00, ROLLING_RESISTANCE=100.50)

        off = self._rr(df, 0, 2.5)
        on = self._rr(df, 2.0, 2.5)

        self.assertLess(off, 1.5, "frame should start well below the bar")
        self.assertEqual(on, off, "a hopeless setup must not be extended")

    def test_a_modest_shortfall_is_rescued(self):
        """The NFLX case: RR 1.55 from a stop that widened off the EMA."""

        df = _frame()

        self.assertLess(self._rr(df, 0, 2.5), 2.0)
        self.assertGreaterEqual(self._rr(df, 2.0, 2.5), 2.0 - 0.01)

    def test_a_wider_cap_admits_more(self):
        """The cap is the control surface, so it must actually bind."""

        df = _frame(Low=97.50, EMA9=98.00, ROLLING_RESISTANCE=101.50)

        tight = self._rr(df, 2.0, 2.5)
        loose = self._rr(df, 2.0, 3.0)

        self.assertLess(tight, 2.0, "2.5 ATR should refuse this one")
        self.assertGreaterEqual(loose, 2.0 - 0.01, "3.0 ATR should admit it")


class ExtensionTests(unittest.TestCase):

    def test_a_short_target_is_extended_to_the_floor(self):

        df = _frame()

        with patch.dict(os.environ, {"TARGET_MIN_RR": "0"}):
            before = _risk(df)

        # 2.0 rather than 2.5: at the default 2.5-ATR cap this frame needs 5.80
        # of reward to reach RR 2.5 against a 5.00 ceiling, so the extension
        # correctly declines. That behaviour is covered in ExtensionCapTests.
        with patch.dict(os.environ, {"TARGET_MIN_RR": "2.0"}):
            after = _risk(df)

        self.assertGreaterEqual(
            after["risk_reward"], 2.0 - 0.01,
            "target was not floored on the risk taken"
        )
        self.assertGreater(
            after["take_profit"], before["take_profit"],
            "a long target must move further away, not nearer"
        )
        self.assertEqual(
            after["stop_loss"], before["stop_loss"],
            "the stop must not move; only the target is floored"
        )

    def test_it_never_pulls_a_target_closer(self):
        """A target already beyond the floor stays exactly where it was."""

        df = _frame(ROLLING_RESISTANCE=115.00)

        with patch.dict(os.environ, {"TARGET_MIN_RR": "0"}):
            before = _risk(df)

        with patch.dict(os.environ, {"TARGET_MIN_RR": "2.0"}):
            after = _risk(df)

        self.assertEqual(before["take_profit"], after["take_profit"])

    def test_a_short_setup_extends_downward(self):
        """Direction handling: a short's target is below entry, not above."""

        df = _frame(
            High=100.90, Low=98.75, Close=100.00,
            EMA9=100.80, VWAP=100.60,
            ROLLING_SUPPORT=97.80, PREV_LOW=97.50,
        )

        with patch.dict(os.environ, {"TARGET_MIN_RR": "0"}):
            before = _risk(df, entry_type="EMA_REJECTION_SHORT",
                           signal="BEARISH", regime="TRENDING_BEAR")

        with patch.dict(os.environ, {"TARGET_MIN_RR": "3.0"}):
            after = _risk(df, entry_type="EMA_REJECTION_SHORT",
                          signal="BEARISH", regime="TRENDING_BEAR")

        if before.get("take_profit") is None:
            self.skipTest("short setup produced no plan on this frame")

        self.assertLess(
            after["take_profit"], before["entry_price"],
            "a short target must sit below entry"
        )
        self.assertLessEqual(
            after["take_profit"], before["take_profit"],
            "a short target must move further down, not up"
        )


if __name__ == "__main__":
    unittest.main()
