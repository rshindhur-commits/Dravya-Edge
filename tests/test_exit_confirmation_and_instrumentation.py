"""The soft-exit confirmation layer, and the write paths that make it visible.

Every one of these pins something that was already shipped broken once: a rule
computed and never persisted, or a streak that resets before it can be reached.
"""

import pytest

from app.exit.exit_engine import resolve_soft_exit_confirmation
from app.state.paper_trade_manager import _record_adjustment_reason


def _confirm(code, streak_state, bars, monkeypatch):
    monkeypatch.setenv("SOFT_EXIT_CONFIRM_BARS", str(bars))
    return resolve_soft_exit_confirmation(code, True, streak_state)


def test_off_by_default_suppresses_nothing(monkeypatch):
    """Shipping default. The streak is still counted so it can be measured."""

    monkeypatch.delenv("SOFT_EXIT_CONFIRM_BARS", raising=False)
    suppress, streak, code, _why = resolve_soft_exit_confirmation("EMA", True, {})

    assert suppress is False
    assert (streak, code) == (1, "EMA"), "the streak must accrue while the switch is off"


def test_first_sighting_is_suppressed_then_the_second_fires(monkeypatch):
    """TSLA #373, NVDA #375 and PLTR #429 each died on a single sighting."""

    suppress, streak, code, why = _confirm("VWAP", {}, 1, monkeypatch)
    assert suppress is True and streak == 1
    assert "needs 2" in why

    state = {"soft_exit_streak": streak, "soft_exit_streak_code": code}
    suppress, streak, _code, _why = _confirm("VWAP", state, 1, monkeypatch)
    assert suppress is False and streak == 2


def test_a_different_soft_rule_restarts_the_count(monkeypatch):
    """EMA once and VWAP once is not the same evidence as EMA twice."""

    state = {"soft_exit_streak": 1, "soft_exit_streak_code": "EMA"}
    suppress, streak, code, _why = _confirm("VWAP", state, 1, monkeypatch)

    assert suppress is True
    assert (streak, code) == (1, "VWAP")


def test_it_never_defers_a_stop_or_a_target(monkeypatch):
    """Confirmation is scoped to soft rules. A hard exit is not a soft one."""

    for code in ("HARD_STOP", "HARD_TARGET", "TIME", None):
        suppress, streak, streak_code, _why = _confirm(code, {}, 1, monkeypatch)
        assert suppress is False, f"{code} must never be deferred"
        assert (streak, streak_code) == (0, None)


def test_it_does_not_require_profit_or_a_young_trade(monkeypatch):
    """The gap the existing guards leave.

    `_should_guard_early_exit` returns False once `abs(rr_progress) >= 0.25` and
    `resolve_soft_exit_hold` requires profit, so a trade half an R underwater on
    its first evaluation falls through both. This layer takes no P&L argument at
    all, which is the point -- the assertion is on the signature.
    """

    import inspect

    params = inspect.signature(resolve_soft_exit_confirmation).parameters
    assert "rr_progress" not in params
    assert "bars_in_trade" not in params


class TestAdjustmentReasonIsActuallyWritten:
    """`evaluate_exit` returned this from the start and nothing stored it."""

    def test_latest_and_history_both_recorded(self):

        trade = {}
        _record_adjustment_reason(trade, "Profit ladder: 0.25R locked")
        _record_adjustment_reason(trade, "Profit ladder: 0.75R locked")

        assert trade["adjustment_reason"] == "Profit ladder: 0.75R locked"
        assert [x["reason"] for x in trade["adjustment_reasons"]] == [
            "Profit ladder: 0.25R locked",
            "Profit ladder: 0.75R locked",
        ], "the rungs walked are the ladder's measurement"

    def test_consecutive_repeats_collapse(self):

        trade = {}
        for _ in range(20):
            _record_adjustment_reason(trade, "Trend intact")

        assert len(trade["adjustment_reasons"]) == 1

    def test_history_is_bounded(self):

        trade = {}
        for i in range(120):
            _record_adjustment_reason(trade, f"reason {i}")

        assert len(trade["adjustment_reasons"]) == 40

    def test_an_empty_reason_clears_latest_without_touching_history(self):

        trade = {}
        _record_adjustment_reason(trade, "Structure trailing stop active")
        _record_adjustment_reason(trade, None)

        assert trade["adjustment_reason"] is None
        assert len(trade["adjustment_reasons"]) == 1


class TestItActuallyFiresThroughEvaluateExit:
    """Driving the real engine, not the helper.

    The soft-exit *hold* was first wired at a point ~250 lines before the soft
    rules are appended. It was a silent no-op and passed the whole suite while
    doing nothing, because every test called its resolver directly. These drive
    `evaluate_exit` end to end so the same mistake cannot repeat here.
    """

    @staticmethod
    def _losing_ema_break():
        """A long, underwater, breaking EMA9 on its first evaluation.

        This is PLTR #429's shape: `bars_in_trade` 0 and rr_progress well past
        -0.25, so `_should_guard_early_exit` bails and `resolve_soft_exit_hold`
        declines for want of profit.
        """

        import pandas as pd

        bar = {
            "Close": 99.0, "High": 99.4, "Low": 98.8, "ATR": 1,
            "EMA9": 100.0, "EMA9_SLOPE": -1, "EMA20": 101.0, "VWAP": 101.5,
            "MACD": 0.2, "MACD_SIGNAL": 0.5, "RSI": 45, "REL_VOLUME": 1.1,
            "HIGHER_HIGH": False, "HIGHER_LOW": False,
        }
        return pd.DataFrame([bar, bar])

    def _run(self, trade_state):

        from app.exit.exit_engine import evaluate_exit

        return evaluate_exit(
            self._losing_ema_break(),
            {},
            {"entry_price": 100.0, "stop_loss": 98.0, "take_profit": 106.0},
            entry_setup={"entry_type": "EMA_PULLBACK"},
            trade_state=trade_state,
        )

    def test_off_the_soft_exit_still_closes_the_trade(self, monkeypatch):
        """The behaviour that has always run must be unchanged at the default."""

        monkeypatch.setenv("SOFT_EXIT_CONFIRM_BARS", "0")
        result = self._run({"highest_price": 100.0, "lowest_price": 98.8,
                            "bars_in_trade": 0, "v1_ema_grace_pending": True})

        assert result["exit_signal"] is True

    def test_on_the_first_sighting_is_held_and_the_streak_is_returned(self, monkeypatch):

        monkeypatch.setenv("SOFT_EXIT_CONFIRM_BARS", "1")
        result = self._run({"highest_price": 100.0, "lowest_price": 98.8,
                            "bars_in_trade": 0, "v1_ema_grace_pending": True})

        assert result["exit_signal"] is False, "an unconfirmed soft exit must not close"
        assert result["soft_exit_streak"] == 1
        assert result["soft_exit_streak_code"] == "EMA"
        assert "unconfirmed" in result["adjustment_reason"].lower()

    def test_feeding_the_streak_back_lets_the_second_sighting_close_it(self, monkeypatch):
        """The round trip. If this fails the switch can never fire in production."""

        monkeypatch.setenv("SOFT_EXIT_CONFIRM_BARS", "1")
        first = self._run({"highest_price": 100.0, "lowest_price": 98.8,
                           "bars_in_trade": 0, "v1_ema_grace_pending": True})

        second = self._run({
            "highest_price": 100.0, "lowest_price": 98.8, "bars_in_trade": 1,
            "v1_ema_grace_pending": True,
            "soft_exit_streak": first["soft_exit_streak"],
            "soft_exit_streak_code": first["soft_exit_streak_code"],
        })

        assert second["exit_signal"] is True
        assert second["soft_exit_streak"] == 2

    def test_a_hard_stop_is_never_deferred(self, monkeypatch):
        """Price through the stop closes regardless of the confirmation switch."""

        import pandas as pd
        from app.exit.exit_engine import evaluate_exit

        bar = {
            "Close": 97.0, "High": 99.0, "Low": 96.5, "ATR": 1,
            "EMA9": 100.0, "EMA9_SLOPE": -1, "EMA20": 101.0, "VWAP": 101.5,
            "MACD": 0.2, "MACD_SIGNAL": 0.5, "RSI": 40, "REL_VOLUME": 1.1,
            "HIGHER_HIGH": False, "HIGHER_LOW": False,
        }

        monkeypatch.setenv("SOFT_EXIT_CONFIRM_BARS", "3")
        result = evaluate_exit(
            pd.DataFrame([bar, bar]),
            {},
            {"entry_price": 100.0, "stop_loss": 98.0, "take_profit": 106.0},
            entry_setup={"entry_type": "EMA_PULLBACK"},
            trade_state={"highest_price": 100.0, "lowest_price": 96.5,
                         "bars_in_trade": 0, "v1_ema_grace_pending": True},
        )

        assert result["exit_signal"] is True
        assert "stop" in str(result["exit_reason"]).lower()
