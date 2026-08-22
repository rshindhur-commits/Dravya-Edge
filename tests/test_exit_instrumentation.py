"""The exit-engine write paths, which were computed and never persisted.

`adjustment_reason` was returned by `evaluate_exit` from the start and no write
path stored it, so the staged rollout's own verification steps read a field that
did not exist.
"""

import pytest

from app.state.paper_trade_manager import _record_adjustment_reason


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
    """Driving the real engine, not a resolver.

    The soft-exit *hold* was first wired ~250 lines before the soft rules are
    appended. It was a silent no-op and passed the whole suite while doing
    nothing, because every test called its resolver directly.
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

    def test_a_losing_soft_exit_closes_the_trade(self, monkeypatch):
        """The behaviour §1.6 measured on 291 trades: soft rules limit losses."""

        result = self._run({"highest_price": 100.0, "lowest_price": 98.8,
                            "bars_in_trade": 0, "v1_ema_grace_pending": True})

        assert result["exit_signal"] is True

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
