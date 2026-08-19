"""When the stop moves to breakeven, and what it costs to move it sooner.

The breakeven move has been gated on a full 1R since it was written. Across the
21-day economics run that made it nearly inert: it fired on 38 of 291 trades and
recovered 0.2R. The trades that needed it never came close -- of the 145 that
travelled at all, 108 peaked below 1R, and the 68 that peaked between 0.1R and
0.5R gave all of it back, closing at -0.01R on average with 15 running on past
-0.25R.

`EXIT_BREAKEVEN_TRIGGER_R` makes the threshold movable so the replay can price
the trade-off, since a lower trigger also cuts positions at breakeven that would
have recovered. The default is unchanged at 1.0 and these tests pin that: the
knob must be inert until somebody sets it.
"""

import os
import unittest
from unittest.mock import patch

import pandas as pd

from app.exit.exit_engine import evaluate_exit


def _frame(close, high, low):
    """One bar, with the indicator columns the momentum exits look for."""

    return pd.DataFrame([{
        "Close": close,
        "High": high,
        "Low": low,
        "Open": close,
        "Volume": 1_000_000,
        "EMA9": close,
        "EMA20": close,
        "VWAP": close,
        "RSI": 55.0,
        "MACD": 0.5,
        "MACD_SIGNAL": 0.4,
        "ATR": 1.0,
        "ATR_PCT": 0.5,
    }])


def _evaluate(price, env=None, entry=100.0, stop=90.0, target=130.0, bars=5):
    """A long from 100 with a 10-point stop, so 1R is 10 points."""

    frame = _frame(price, price, price)
    risk_setup = {
        "entry_price": entry,
        "stop_loss": stop,
        "take_profit": target,
        "initial_stop_loss": stop,
    }
    trade_state = {
        "entry_type": "EMA_PULLBACK",
        "holding_profile": "INTRADAY",
        "bars_in_trade": bars,
        "initial_stop_loss": stop,
    }

    with patch.dict(os.environ, env or {}, clear=False):

        return evaluate_exit(
            frame,
            {"trend_regime": "TRENDING_BULL"},
            risk_setup,
            entry_setup={"entry_type": "EMA_PULLBACK"},
            trade_state=trade_state,
        )


class BreakevenTriggerTests(unittest.TestCase):

    def test_the_default_still_waits_for_a_full_1r(self):
        """Unset, the knob must reproduce the behaviour that has always run."""

        # "Unset" has to be enforced, not assumed. Production runs this at 0.5,
        # so before `.env` was synced to Render on 2026-08-19 the case passed
        # only because the local file happened to omit the variable.
        self.enterContext(patch.dict(os.environ, {}, clear=False))
        os.environ.pop("EXIT_BREAKEVEN_TRIGGER_R", None)

        for price, expected in ((103.0, False), (105.0, False), (110.0, True)):

            result = _evaluate(price)
            moved = result.get("updated_stop", 0) >= 100.0

            self.assertEqual(
                moved, expected,
                f"at {price} (={(price - 100) / 10:.1f}R) "
                f"breakeven should be {expected}",
            )

    def test_a_lower_trigger_moves_the_stop_earlier(self):
        """The point of the knob: protect the trades that never reach 1R."""

        result = _evaluate(103.0, {"EXIT_BREAKEVEN_TRIGGER_R": "0.25"})

        self.assertGreaterEqual(result["updated_stop"], 100.0)
        self.assertEqual(result["adjustment_reason"], "Moved stop to breakeven")

    def test_a_lower_trigger_does_nothing_below_itself(self):

        result = _evaluate(101.0, {"EXIT_BREAKEVEN_TRIGGER_R": "0.25"})

        self.assertLess(result["updated_stop"], 100.0)

    def test_zero_disables_the_move_entirely(self):
        """A trade at 1.5R keeps its original stop, so the flag is a real off."""

        result = _evaluate(115.0, {"EXIT_BREAKEVEN_TRIGGER_R": "0"})

        self.assertLess(result["updated_stop"], 100.0)

    def test_the_stop_never_moves_against_the_position(self):
        """Breakeven protects; it must not loosen a stop already past entry."""

        frame = _frame(112.0, 112.0, 112.0)
        result = evaluate_exit(
            frame,
            {"trend_regime": "TRENDING_BULL"},
            {
                "entry_price": 100.0,
                "stop_loss": 105.0,       # already trailed above entry
                "take_profit": 130.0,
                "initial_stop_loss": 90.0,
            },
            entry_setup={"entry_type": "EMA_PULLBACK"},
            trade_state={
                "entry_type": "EMA_PULLBACK",
                "holding_profile": "INTRADAY",
                "bars_in_trade": 5,
                "initial_stop_loss": 90.0,
            },
        )

        self.assertGreaterEqual(result["updated_stop"], 105.0)

    def test_a_short_moves_its_stop_down_not_up(self):
        """The sign error that would silently invert every short."""

        frame = _frame(97.0, 97.0, 97.0)

        with patch.dict(os.environ,
                        {"EXIT_BREAKEVEN_TRIGGER_R": "0.25"}, clear=False):

            result = evaluate_exit(
                frame,
                {"trend_regime": "TRENDING_BEAR"},
                {
                    "entry_price": 100.0,
                    "stop_loss": 110.0,
                    "take_profit": 70.0,
                    "initial_stop_loss": 110.0,
                },
                entry_setup={"entry_type": "EMA_REJECTION_SHORT"},
                trade_state={
                    "entry_type": "EMA_REJECTION_SHORT",
                    "holding_profile": "INTRADAY",
                    "bars_in_trade": 5,
                    "initial_stop_loss": 110.0,
                },
            )

        self.assertLessEqual(result["updated_stop"], 100.0)


class BreakevenOnPeakTests(unittest.TestCase):
    """A trade can touch the trigger intrabar and close back under it."""

    def test_the_default_ignores_a_peak_the_bar_gave_back(self):

        result = _evaluate(
            101.0,
            {"EXIT_BREAKEVEN_TRIGGER_R": "0.25"},
        )

        self.assertLess(result["updated_stop"], 100.0)

    def test_on_peak_uses_the_high_water_mark(self):

        frame = _frame(101.0, 101.0, 101.0)

        with patch.dict(os.environ, {
            "EXIT_BREAKEVEN_TRIGGER_R": "0.25",
            "EXIT_BREAKEVEN_ON_PEAK": "true",
        }, clear=False):

            result = evaluate_exit(
                frame,
                {"trend_regime": "TRENDING_BULL"},
                {
                    "entry_price": 100.0,
                    "stop_loss": 90.0,
                    "take_profit": 130.0,
                    "initial_stop_loss": 90.0,
                },
                entry_setup={"entry_type": "EMA_PULLBACK"},
                trade_state={
                    "entry_type": "EMA_PULLBACK",
                    "holding_profile": "INTRADAY",
                    "bars_in_trade": 5,
                    "initial_stop_loss": 90.0,
                    # The peak is carried as a price, not an R -- the engine
                    # derives mfe_r from it. 104 on a 10-point risk is 0.4R.
                    "highest_price": 104.0,
                },
            )

        self.assertGreaterEqual(result["updated_stop"], 100.0)


if __name__ == "__main__":
    unittest.main()
