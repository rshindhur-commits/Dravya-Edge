import unittest

import pandas as pd

from app.analytics.exit_waterfall import build_exit_waterfall
from app.exit.exit_engine import evaluate_exit


class ExitWaterfallTests(unittest.TestCase):

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


if __name__ == "__main__":

    unittest.main()