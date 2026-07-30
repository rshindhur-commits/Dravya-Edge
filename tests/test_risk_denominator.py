"""R must be measured against the risk frozen at entry.

Regression cover for the 2026-07-29 NVDA trade: the exit engine moved the stop to
breakeven at +1R, and because rr_progress derived risk from the *current* stop,
`abs(entry - stop)` became 0. rr_progress and mfe_r collapsed to 0 permanently,
which silently disabled partial profit, the ATR trailing stop, and multiday
profit protection -- all gated on R thresholds. The trade gave back a +1.4R peak
and closed flat with r_multiple NULL and trend capture -36%.
"""

import unittest
from unittest.mock import patch

import pandas as pd

from app.exit.exit_engine import (
    _calculate_rr_progress,
    evaluate_exit,
    resolve_risk_per_share,
)
from app.state.paper_trade_manager import (
    _backfill_initial_stop,
    _initial_risk_per_share,
)


class ResolveRiskPerShareTests(unittest.TestCase):

    def test_prefers_the_initial_stop(self):

        self.assertAlmostEqual(
            resolve_risk_per_share(193.32, 191.27, 193.32), 2.05, places=2
        )

    def test_falls_back_to_current_stop_for_legacy_trades(self):

        self.assertAlmostEqual(
            resolve_risk_per_share(193.32, None, 191.27), 2.05, places=2
        )

    def test_breakeven_initial_stop_does_not_yield_zero_when_current_is_usable(self):

        self.assertAlmostEqual(
            resolve_risk_per_share(100.0, 100.0, 98.0), 2.0, places=2
        )

    def test_returns_zero_only_when_nothing_is_usable(self):

        self.assertEqual(resolve_risk_per_share(100.0, 100.0, 100.0), 0.0)
        self.assertEqual(resolve_risk_per_share(None, None, None), 0.0)


class RrProgressTests(unittest.TestCase):

    def test_breakeven_stop_no_longer_collapses_r(self):
        """The exact 2026-07-29 NVDA state: stop moved onto entry."""

        collapsed = _calculate_rr_progress(
            current_price=196.09, entry_price=193.32,
            stop_loss=193.32, is_short=False,
        )
        self.assertEqual(collapsed, 0)

        preserved = _calculate_rr_progress(
            current_price=196.09, entry_price=193.32,
            stop_loss=193.32, is_short=False,
            risk_per_share=2.05,
        )
        self.assertAlmostEqual(preserved, 1.35, places=2)

    def test_short_side_uses_frozen_risk(self):

        progress = _calculate_rr_progress(
            current_price=98.0, entry_price=100.0,
            stop_loss=100.0, is_short=True,
            risk_per_share=2.0,
        )
        self.assertAlmostEqual(progress, 1.0, places=2)


def _frame(close, high=None, low=None):
    high = high if high is not None else close
    low = low if low is not None else close
    index = pd.date_range("2026-07-29 14:00", periods=30, freq="15min", tz="America/New_York")
    return pd.DataFrame(
        {
            "Open": [close] * 30,
            "High": [high] * 30,
            "Low": [low] * 30,
            "Close": [close] * 30,
            "Volume": [1_000_000] * 30,
            "ATR": [1.0] * 30,
            "EMA9": [close] * 30,
            "EMA20": [close] * 30,
            "VWAP": [close] * 30,
            "MACD": [0.1] * 30,
            "RSI": [55.0] * 30,
            "Relative Volume": [1.2] * 30,
        },
        index=index,
    )


class EvaluateExitRiskTests(unittest.TestCase):
    """End-to-end: a trade whose stop already sits on entry still measures R."""

    def _evaluate(self, risk_setup, trade_state):
        with patch("app.exit.exit_engine.evaluate_live_trend_health",
                   return_value={"status": "HEALTHY", "score": 70}), \
             patch("app.exit.exit_engine.evaluate_exit_confidence",
                   return_value={"exit_confidence_score": 20, "health_label": "HEALTHY"}):
            return evaluate_exit(
                _frame(196.0, high=196.2, low=195.8),
                {"signal": "BULLISH", "score": 7},
                risk_setup,
                {"entry_type": "BREAKOUT"},
                trade_state=trade_state,
            )

    def test_frozen_risk_keeps_r_alive_after_breakeven_move(self):

        result = self._evaluate(
            {
                "entry_price": 193.32,
                "stop_loss": 193.32,          # already moved to breakeven
                "initial_stop_loss": 191.27,  # frozen entry risk
                "take_profit": 197.81,
            },
            {"entry_type": "BREAKOUT", "highest_price": 196.09,
             "holding_profile": "MULTIDAY", "bars_in_trade": 10},
        )

        self.assertGreater(result["rr_progress"], 1.0)
        self.assertGreater(result["mfe_r"], 1.0)

    def test_without_the_frozen_stop_r_is_dead(self):
        """Documents the old behaviour so the fix cannot silently regress."""

        result = self._evaluate(
            {
                "entry_price": 193.32,
                "stop_loss": 193.32,
                "initial_stop_loss": 193.32,
                "take_profit": 197.81,
            },
            {"entry_type": "BREAKOUT", "highest_price": 196.09,
             "holding_profile": "MULTIDAY", "bars_in_trade": 10},
        )

        self.assertEqual(result["rr_progress"], 0)
        self.assertEqual(result["mfe_r"], 0)


class PaperTradeInitialStopTests(unittest.TestCase):

    def test_initial_risk_per_share(self):

        self.assertAlmostEqual(_initial_risk_per_share(193.32, 191.27), 2.05, places=2)
        self.assertIsNone(_initial_risk_per_share(100.0, 100.0))
        self.assertIsNone(_initial_risk_per_share(None, 1.0))

    def test_backfill_adopts_current_stop_once(self):

        trade = {"entry_price": 100.0, "stop_loss": 98.0}
        _backfill_initial_stop(trade)

        self.assertEqual(trade["initial_stop_loss"], 98.0)
        self.assertAlmostEqual(trade["initial_risk_per_share"], 2.0, places=2)

    def test_backfill_never_overwrites_an_existing_initial_stop(self):

        trade = {"entry_price": 100.0, "stop_loss": 100.0, "initial_stop_loss": 98.0,
                 "initial_risk_per_share": 2.0}
        _backfill_initial_stop(trade)

        self.assertEqual(trade["initial_stop_loss"], 98.0)


if __name__ == "__main__":
    unittest.main()
