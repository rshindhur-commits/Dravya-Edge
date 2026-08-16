import os
import unittest
from unittest import mock

import pandas as pd

from app.analytics.exit_waterfall import build_exit_waterfall
from app.exit.exit_engine import evaluate_exit


class ExitWaterfallTests(unittest.TestCase):

    def setUp(self):
        """The EMA rule under test IS a momentum exit.

        Production runs the class off, so without pinning it here this asserts
        the behaviour of a rule the ambient environment has disabled.
        """

        patcher = mock.patch.dict(
            os.environ, {"EXIT_MOMENTUM_ENABLED": "true"}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_selected_rule_is_preserved_in_priority_order(self):

        waterfall = build_exit_waterfall(
            [
                {
                    "code": "EMA",
                    "reason": "EMA9 invalidation (long)",
                    "priority": 70,
                },
                {
                    "code": "HARD_STOP",
                    "reason": "Hard stop hit (long)",
                    "priority": 100,
                },
            ],
            selected_rule="HARD_STOP",
        )

        hard_stop = next(item for item in waterfall if item["rule"] == "HARD_STOP")
        ema = next(item for item in waterfall if item["rule"] == "EMA")
        vwap = next(item for item in waterfall if item["rule"] == "VWAP")
        self.assertEqual(hard_stop["status"], "SELECTED")
        self.assertEqual(ema["status"], "TRIGGERED")
        self.assertEqual(vwap["status"], "PASSED")

    def test_exit_engine_emits_selected_waterfall_stage(self):

        result = evaluate_exit(
            pd.DataFrame([
                {
                    "Close": 99,
                    "High": 101,
                    "Low": 97,
                    "ATR": 1,
                    "EMA9": 100,
                    "EMA9_SLOPE": -1,
                    "VWAP": 100,
                }
            ]),
            {},
            {
                "entry_price": 100,
                "stop_loss": 98,
                "take_profit": 104,
            },
            entry_setup={"entry_type": "EMA_PULLBACK"},
        )

        self.assertTrue(result["exit_signal"])
        self.assertEqual(result["exit_rule"], "HARD_STOP")
        self.assertEqual(result["exit_stage"], 1)
        self.assertEqual(
            result["exit_waterfall"][0]["status"],
            "SELECTED",
        )

    def test_profitable_healthy_first_ema_break_waits_one_bar(self):

        result = evaluate_exit(
            pd.DataFrame([
                {
                    "Close": 101,
                    "High": 103,
                    "Low": 100.5,
                    "ATR": 1,
                    "EMA9": 100,
                    "EMA9_SLOPE": -1,
                    "EMA20": 98,
                    "VWAP": 99,
                    "MACD": 1,
                    "MACD_SIGNAL": 0.5,
                    "RSI": 60,
                    "REL_VOLUME": 1.2,
                    "HIGHER_HIGH": True,
                    "HIGHER_LOW": True,
                },
                {
                    "Close": 99.5,
                    "High": 102,
                    "Low": 99,
                    "ATR": 1,
                    "EMA9": 100,
                    "EMA9_SLOPE": -1,
                    "EMA20": 98,
                    "VWAP": 99,
                    "MACD": 1,
                    "MACD_SIGNAL": 0.5,
                    "RSI": 60,
                    "REL_VOLUME": 1.2,
                    "HIGHER_HIGH": True,
                    "HIGHER_LOW": True,
                },
            ]),
            {},
            {
                "entry_price": 98,
                "stop_loss": 96,
                "take_profit": 108,
            },
            entry_setup={"entry_type": "EMA_PULLBACK"},
            trade_state={"highest_price": 103, "lowest_price": 98, "bars_in_trade": 3},
        )

        self.assertFalse(result["exit_signal"])
        self.assertTrue(result["grace_zone_active"])
        self.assertTrue(result["v1_ema_grace_pending"])

    def test_multiday_profit_protection_exits_after_peak_giveback(self):

        result = evaluate_exit(
            pd.DataFrame([{
                "Close": 104,
                "High": 104.5,
                "Low": 103.5,
                "ATR": 1,
                "EMA9": 103,
                "EMA9_SLOPE": 1,
                "EMA20": 102,
                "VWAP": 103,
                "MACD": 1,
                "MACD_SIGNAL": 0.5,
                "RSI": 60,
            }]),
            {},
            {
                "entry_price": 100,
                "stop_loss": 98,
                "take_profit": 110,
            },
            entry_setup={"entry_type": "BREAKOUT"},
            trade_state={
                "holding_profile": "MULTIDAY",
                "highest_price": 106,
                "lowest_price": 100,
                "bars_in_trade": 8,
            },
        )

        self.assertTrue(result["exit_signal"])
        self.assertEqual(result["exit_rule"], "PROFIT_PROTECTION")
        self.assertEqual(result["profit_lock_stop"], 102.0)
        self.assertEqual(result["profit_giveback_r"], 1.0)


if __name__ == "__main__":

    unittest.main()