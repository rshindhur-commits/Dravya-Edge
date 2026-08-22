"""Soft invalidation rules must not close a trade on an unconfirmed wick.

VWAP and MACD are bare state comparisons -- `price > VWAP`, `MACD < signal` --
with no buffer, no slope condition and no bar-close requirement, evaluated
against a still-forming bar. Two protections existed and neither reached them:

  * the grace zone deferred a lone momentum exit by one bar, but was scoped to
    EMA only;
  * `_should_guard_early_exit` listed "VWAP invalidation" as guardable, but
    delegated to `trend_still_valid`, which required price to be on the right
    side of VWAP -- the exact condition a VWAP exit disproves. Unreachable.
"""

import os
import unittest
from unittest import mock

import pandas as pd

from app.exit.exit_engine import _should_guard_early_exit, trend_still_valid


def _frame(close, vwap, ema9, ema20, rsi):
    index = pd.date_range("2026-07-30 14:00", periods=6, freq="15min", tz="America/New_York")
    return pd.DataFrame(
        {
            "Close": [close] * 6, "VWAP": [vwap] * 6, "EMA9": [ema9] * 6,
            "EMA20": [ema20] * 6, "RSI": [rsi] * 6,
        },
        index=index,
    )


class TrendStillValidTests(unittest.TestCase):

    def test_vwap_exit_can_now_be_guarded(self):
        """A long dipped below VWAP; EMA stack and RSI still support the trade."""

        frame = _frame(close=99.0, vwap=100.0, ema9=101.0, ema20=100.0, rsi=60.0)

        # Including VWAP the answer can only be False -- that is the dead branch.
        self.assertFalse(trend_still_valid(frame, "CALL"))
        # Excluding the component whose rule fired asks the intended question.
        self.assertTrue(trend_still_valid(frame, "CALL", ignore="VWAP"))

    def test_ignoring_vwap_does_not_rescue_a_broken_trend(self):
        """The exclusion must not become a blanket pass."""

        frame = _frame(close=99.0, vwap=100.0, ema9=99.0, ema20=100.0, rsi=40.0)

        self.assertFalse(trend_still_valid(frame, "CALL", ignore="VWAP"))

    def test_ema_exit_excludes_only_the_ema_stack(self):

        frame = _frame(close=101.0, vwap=100.0, ema9=99.0, ema20=100.0, rsi=60.0)

        self.assertFalse(trend_still_valid(frame, "CALL"))
        self.assertTrue(trend_still_valid(frame, "CALL", ignore="EMA"))

    def test_short_side_is_symmetric(self):

        frame = _frame(close=101.0, vwap=100.0, ema9=99.0, ema20=100.0, rsi=40.0)

        self.assertFalse(trend_still_valid(frame, "PUT"))
        self.assertTrue(trend_still_valid(frame, "PUT", ignore="VWAP"))

    def test_unknown_ignore_value_excludes_nothing(self):

        frame = _frame(close=99.0, vwap=100.0, ema9=101.0, ema20=100.0, rsi=60.0)

        self.assertFalse(trend_still_valid(frame, "CALL", ignore="NOT_A_COMPONENT"))


class EarlyExitGuardTests(unittest.TestCase):

    def _healthy_long(self):
        return _frame(close=99.0, vwap=100.0, ema9=101.0, ema20=100.0, rsi=60.0)

    def test_vwap_invalidation_is_reachable_by_the_guard(self):
        """The regression: this combination could never return True before."""

        self.assertTrue(
            _should_guard_early_exit(
                self._healthy_long(),
                "VWAP invalidation (long)",
                bars_in_trade=1,
                rr_progress=0.1,
                is_short=False,
            )
        )

    def test_guard_still_declines_once_the_trade_has_moved(self):

        self.assertFalse(
            _should_guard_early_exit(
                self._healthy_long(),
                "VWAP invalidation (long)",
                bars_in_trade=1,
                rr_progress=0.4,
                is_short=False,
            )
        )

    def test_guard_still_declines_after_the_early_window(self):

        self.assertFalse(
            _should_guard_early_exit(
                self._healthy_long(),
                "VWAP invalidation (long)",
                bars_in_trade=5,
                rr_progress=0.1,
                is_short=False,
            )
        )

    def test_hard_exits_are_never_guarded(self):

        for reason in ("Hard stop hit (long)", "Profit target reached (long)"):
            self.assertFalse(
                _should_guard_early_exit(
                    self._healthy_long(), reason,
                    bars_in_trade=1, rr_progress=0.1, is_short=False,
                ),
                reason,
            )

    def test_macd_excludes_nothing_and_still_needs_a_whole_trend(self):

        broken = _frame(close=99.0, vwap=100.0, ema9=99.0, ema20=100.0, rsi=40.0)

        self.assertFalse(
            _should_guard_early_exit(
                broken, "MACD bearish crossover (long)",
                bars_in_trade=1, rr_progress=0.1, is_short=False,
            )
        )
        self.assertTrue(
            _should_guard_early_exit(
                _frame(close=101.0, vwap=100.0, ema9=101.0, ema20=100.0, rsi=60.0),
                "MACD bearish crossover (long)",
                bars_in_trade=1, rr_progress=0.1, is_short=False,
            )
        )


if __name__ == "__main__":
    unittest.main()


class EarlyExitGuardAsymmetryTests(unittest.TestCase):
    """The two directions are separately tunable, and both default to 0.25.

    They were split on 2026-08-21 while widening the adverse side to the stop,
    which was reverted the same day against TRADE_QUALITY_PLAN §1.6. The split
    is kept because it makes the question A/B-able; the defaults are the
    behaviour every archived measurement was taken under.
    """

    def _guard(self, rr, is_short=False):
        # A frame whose trend is intact apart from the rule that fired.
        frame = _frame(close=105, vwap=101, ema9=103, ema20=100, rsi=62) if not is_short \
            else _frame(close=95, vwap=99, ema9=97, ema20=100, rsi=38)
        return _should_guard_early_exit(
            frame, "VWAP invalidation", 1, rr, is_short
        )

    def test_a_trade_past_the_bail_is_not_guarded(self):
        """Both sides default to 0.25, which is what §1.6 was measured under.

        Widening the adverse side to the stop was tried on 2026-08-21 and
        reverted the same day: it holds losing trades longer, and §1.6 measured
        a dead trade running to its hard stop at -12.31% against -7.41%.
        """

        self.assertFalse(self._guard(-0.49))
        self.assertFalse(self._guard(-0.71))
        self.assertTrue(self._guard(-0.1), "inside the noise window it still guards")

    def test_the_guard_never_holds_through_the_stop(self):
        """True at any adverse setting, including the widened one."""

        for ceiling in ("0.25", "1.0"):
            with mock.patch.dict(os.environ, {"EARLY_EXIT_GUARD_MAX_ADVERSE_R": ceiling}):
                self.assertFalse(self._guard(-1.0))
                self.assertFalse(self._guard(-1.4))

    def test_above_entry_the_old_bail_is_unchanged(self):
        """A trade that genuinely moved up is not noise; the soft rule may be real."""

        self.assertFalse(self._guard(0.25))
        self.assertFalse(self._guard(0.9))
        self.assertTrue(self._guard(0.1), "barely above entry is still the noise window")

    def test_widening_the_adverse_side_is_one_env_var(self):
        """Kept tunable so the question can be A/B'd rather than argued."""

        with mock.patch.dict(os.environ, {"EARLY_EXIT_GUARD_MAX_ADVERSE_R": "1.0"}):
            self.assertTrue(self._guard(-0.49))
            self.assertTrue(self._guard(-0.71))
            self.assertFalse(self._guard(-1.0), "never past the stop")

    def test_shorts_are_symmetric(self):

        self.assertTrue(self._guard(-0.1, is_short=True))
        self.assertFalse(self._guard(-0.5, is_short=True))

    def test_the_bar_ceiling_still_wins(self):
        """Guarding is bounded to the entry's own bar however far underwater."""

        frame = _frame(close=105, vwap=101, ema9=103, ema20=100, rsi=62)
        self.assertFalse(
            _should_guard_early_exit(frame, "VWAP invalidation", 2, -0.5, False)
        )
