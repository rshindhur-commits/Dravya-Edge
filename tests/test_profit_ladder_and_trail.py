"""Holding a gain between breakeven and the target.

2026-08-19: 6.51R of favourable movement across five trades, 1.66R booked -- a
25% capture. TSLA peaked at 1.18R and PLTR at 1.24R and both booked 0.00R,
because between the breakeven stop and the 2R target there was nothing beneath
them.

Two mechanisms close that. The ladder ratchets a stop as the *peak* advances, and
the ATR trail follows price once armed. The trail already existed and could not
fire: it armed at `rr_progress >= 2`, targets sit at 2R, the target is evaluated
first and the trail is guarded by `not exit_signal` -- so the scan that first saw
2R had already exited. Across 52 closed trades a stop had been trailed past
breakeven three times.
"""

import os
from unittest import mock

import pytest

from app.exit.exit_engine import (
    ladder_locked_r,
    profit_ladder,
    trail_arm_r,
)


def _ladder(value):
    return mock.patch.dict(os.environ, {"EXIT_PROFIT_LADDER": value}, clear=False)


class TestLadder:

    def test_nothing_is_locked_below_the_first_rung(self):
        assert ladder_locked_r(0.9) is None

    def test_tsla_would_have_kept_a_quarter_r(self):
        """TSLA #340 peaked at 1.18R and booked 0.00R."""
        assert ladder_locked_r(1.18) == 0.25

    def test_pltr_would_have_kept_a_quarter_r(self):
        """PLTR #352 peaked at 1.24R and booked 0.00R."""
        assert ladder_locked_r(1.24) == 0.25

    def test_amzn_reaches_the_upper_rungs(self):
        """AMZN #343 peaked at 2.99R and booked 1.99R at its fixed target."""
        assert ladder_locked_r(2.99) == 1.75

    def test_the_ladder_climbs(self):
        seen = [ladder_locked_r(r) for r in (1.0, 1.5, 2.0, 2.5, 3.0)]
        assert seen == [0.25, 0.75, 1.25, 1.75, 2.25]
        assert seen == sorted(seen), "a higher peak must never lock less"

    def test_it_reads_the_peak_not_the_current_reading(self):
        """A retrace must not unwind a rung that was already earned. The caller
        passes mfe_r, which ratchets; this pins the contract."""
        assert ladder_locked_r(2.6) == 1.75
        assert ladder_locked_r(0.1) is None

    def test_locked_r_always_leaves_slack_under_the_peak(self):
        """A rung that locks too close to the peak is the proportional giveback
        `_giveback_floor` already measured as destructive."""
        for peak, locked in profit_ladder():
            assert peak - locked >= 0.5, f"rung {peak}:{locked} is too tight"

    def test_an_empty_ladder_disables_it(self):
        with _ladder(""):
            assert profit_ladder() == []
            assert ladder_locked_r(5.0) is None

    def test_a_custom_ladder_is_honoured(self):
        with _ladder("2.25:1.5,2.5:2.0,2.75:2.4"):
            assert ladder_locked_r(2.0) is None
            assert ladder_locked_r(2.3) == 1.5
            assert ladder_locked_r(2.8) == 2.4

    def test_a_malformed_rung_is_skipped_not_fatal(self):
        with _ladder("1.0:0.25,garbage,2.0:1.25"):
            assert profit_ladder() == [(1.0, 0.25), (2.0, 1.25)]

    def test_a_zero_lock_is_not_a_rung(self):
        """Locking 0R is the breakeven stop, which already exists."""
        with _ladder("1.0:0"):
            assert ladder_locked_r(1.5) is None


class TestTrailArming:

    def test_it_arms_below_the_target(self):
        """The whole defect: targets are at 2R, so arming at 2R never fires."""
        assert trail_arm_r() < 2.0

    def test_it_is_configurable(self):
        with mock.patch.dict(os.environ, {"EXIT_TRAIL_ARM_R": "0.75"}, clear=False):
            assert trail_arm_r() == 0.75


class TestTargetExtension:
    """Letting a runner past its target, and the column that measures it."""

    def test_it_ships_off(self):
        """The only case in the archive says on is worse.

        AMZN #343 hit its target at +1.99R and ran to +3.08R, which looks like a
        clear win for extending. But the path dips to +1.50R at 11:20, the 15m
        ATR (1.32-1.44) is wider than 1R (1.31) so the ladder governs at +1.75R,
        and that dip takes it out below the target it declined.
        """

        from app.exit.exit_engine import target_extend_enabled

        assert target_extend_enabled() is False

    def test_the_switch_turns_it_on(self):
        from app.exit.exit_engine import target_extend_enabled

        with mock.patch.dict(
            os.environ, {"EXIT_TARGET_EXTEND_ENABLED": "true"}, clear=False
        ):
            assert target_extend_enabled() is True


class TestTargetTouchIsRecorded:
    """`target_touch_r` is what makes the switch answerable from the archive.

    Recorded whether the target is taken or declined, so with the switch off it
    confirms the target was reached, and with it on `final_r - target_touch_r` is
    exactly what extending won or lost -- no replay, both numbers on the row.
    """

    def test_the_verdict_exposes_it(self):
        from app.exit.exit_engine import evaluate_exit

        import pandas as pd

        frame = pd.DataFrame([{
            "Close": 130.0, "High": 131.0, "Low": 129.0, "Open": 129.0,
            "Volume": 1_000_000, "EMA9": 129.0, "EMA20": 128.0, "VWAP": 129.0,
            "RSI": 60.0, "MACD": 0.5, "MACD_SIGNAL": 0.4, "ATR": 1.0, "ATR_PCT": 0.8,
        }])

        result = evaluate_exit(
            frame,
            {"trend_regime": "TRENDING_BULL"},
            {"entry_price": 100.0, "stop_loss": 90.0, "take_profit": 120.0,
             "initial_stop_loss": 90.0},
            entry_setup={"entry_type": "EMA_PULLBACK"},
            trade_state={"entry_type": "EMA_PULLBACK", "holding_profile": "INTRADAY",
                         "bars_in_trade": 5, "initial_stop_loss": 90.0},
        )

        # target 120 on a 10-point risk from 100 is exactly +2R
        assert result["target_touch_r"] == pytest.approx(2.0, abs=0.01)

    def test_first_touch_wins(self):
        """A later retrace must not rewrite what the target was worth."""

        from app.exit.exit_engine import evaluate_exit

        import pandas as pd

        frame = pd.DataFrame([{
            "Close": 105.0, "High": 106.0, "Low": 104.0, "Open": 104.0,
            "Volume": 1_000_000, "EMA9": 104.0, "EMA20": 103.0, "VWAP": 104.0,
            "RSI": 55.0, "MACD": 0.5, "MACD_SIGNAL": 0.4, "ATR": 1.0, "ATR_PCT": 0.8,
        }])

        result = evaluate_exit(
            frame,
            {"trend_regime": "TRENDING_BULL"},
            {"entry_price": 100.0, "stop_loss": 90.0, "take_profit": 120.0,
             "initial_stop_loss": 90.0},
            entry_setup={"entry_type": "EMA_PULLBACK"},
            trade_state={"entry_type": "EMA_PULLBACK", "holding_profile": "INTRADAY",
                         "bars_in_trade": 5, "initial_stop_loss": 90.0,
                         "target_touch_r": 2.0},
        )

        assert result["target_touch_r"] == 2.0, "the recorded touch must survive"
