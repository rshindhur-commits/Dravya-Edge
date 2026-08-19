"""Is a soft exit a trend ending, or the trade wobbling inside one?

EMA9, VWAP and MACD fire on first touch, with no confirmation bar and no
reference to whether the trend broke. Every soft exit ever booked: 14 of them, 5
positive, mean -0.059R, option premium -1.15% with 4 of 14 positive. They are a
coin flip that loses slightly, and they close most trades.

`trend_health_score` is computed on every scan for every open position and, until
this rule, decided nothing at all.
"""

import os
from unittest import mock

import pytest

from app.exit.exit_engine import resolve_soft_exit_hold


@pytest.fixture(autouse=True)
def _pin_to_code_defaults(monkeypatch):
    """These cases assert the *documented defaults*, not the deployed values.

    The staged rollout of 2026-08-19 sets these off in `.env` so the bug fixes
    could ship without four behaviour changes going live at once. Without this
    pin those cases would assert whatever the rollout happens to have reached --
    the same defect that turned eight tests red when `.env` was first synced to
    Render.
    """

    for key in (
        "EXIT_PROFIT_LADDER",
        "EXIT_TRAIL_ARM_R",
        "EXIT_TRAIL_ATR_MULT",
        "EXIT_STRUCTURE_TRAIL_ENABLED",
        "EXIT_STRUCTURE_TRAIL_LOOKBACK",
        "EXIT_STRUCTURE_TRAIL_BUFFER_PCT",
        "EXIT_TARGET_EXTEND_ENABLED",
        "SOFT_EXIT_HOLD_ENABLED",
        "SOFT_EXIT_HOLD_MIN_TREND_HEALTH",
    ):
        monkeypatch.delenv(key, raising=False)


class TestTheDistinction:
    """In profit and trend intact = wobble. Losing or trend broken = wrong."""

    def test_nvda_is_held(self):
        """NVDA 2026-07-31: ran to +1.66R, closed +0.60R on an EMA9 touch with
        trend health 95. The giveback this rule exists for."""

        hold, why = resolve_soft_exit_hold("EMA", True, 0.60, 95.0)

        assert hold is True
        assert "95" in why

    def test_avgo_is_not_held(self):
        """AVGO #351 2026-08-19: VWAP touch at -0.17R with trend health 40.

        Losing and broken on both counts. The early exit saved roughly 0.83R
        against its stop, and a rule that held this trade would be strictly
        worse -- AVGO kept going against the position after.
        """

        hold, why = resolve_soft_exit_hold("VWAP", True, -0.17, 40.0)

        assert hold is False
        assert "not in profit" in why

    def test_a_losing_trade_is_never_held_however_healthy_the_trend(self):
        """The guard that stops this becoming "hold losers longer"."""

        for health in (70.0, 85.0, 100.0):
            hold, _why = resolve_soft_exit_hold("EMA", True, -0.5, health)
            assert hold is False, f"a losing trade was held at health {health}"

    def test_a_broken_trend_is_honoured_even_in_profit(self):
        hold, why = resolve_soft_exit_hold("MACD", True, 1.20, 30.0)

        assert hold is False
        assert "below" in why

    def test_exactly_flat_is_not_profit(self):
        assert resolve_soft_exit_hold("EMA", True, 0.0, 95.0)[0] is False


class TestScope:

    def test_only_soft_rules_are_held(self):
        """A stop or a target is a price level and is never second-guessed."""

        for code in ("HARD_STOP", "HARD_TARGET", "NEAR_CLOSE", "TIME_EXIT",
                     "FAILED_BREAKOUT"):
            hold, _why = resolve_soft_exit_hold(code, True, 0.60, 95.0)
            assert hold is False, f"{code} must not be held"

    def test_all_three_soft_rules_are_in_scope(self):
        for code in ("EMA", "VWAP", "MACD"):
            hold, _why = resolve_soft_exit_hold(code, True, 0.60, 95.0)
            assert hold is True, f"{code} should be eligible"

    def test_nothing_is_held_when_no_exit_fired(self):
        assert resolve_soft_exit_hold("EMA", False, 0.60, 95.0)[0] is False


class TestFailureModes:

    def test_a_missing_reading_honours_the_exit(self):
        """Absent evidence is not evidence the trend is intact."""

        hold, why = resolve_soft_exit_hold("EMA", True, 0.60, None)

        assert hold is False
        assert "no trend health" in why

    def test_the_switch_turns_it_off(self):
        with mock.patch.dict(os.environ, {"SOFT_EXIT_HOLD_ENABLED": "false"},
                             clear=False):
            hold, why = resolve_soft_exit_hold("EMA", True, 0.60, 95.0)

        assert hold is False
        assert why == "hold disabled"

    def test_the_health_floor_is_configurable(self):
        with mock.patch.dict(os.environ,
                             {"SOFT_EXIT_HOLD_MIN_TREND_HEALTH": "90"},
                             clear=False):
            assert resolve_soft_exit_hold("EMA", True, 0.60, 85.0)[0] is False
            assert resolve_soft_exit_hold("EMA", True, 0.60, 95.0)[0] is True
