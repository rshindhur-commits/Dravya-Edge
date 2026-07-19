import unittest
from unittest.mock import patch

import pandas as pd

from app.runtime.paper_automation import run_auto_paper_entries, run_auto_paper_exits


class PaperAutomationRuntimeTests(unittest.TestCase):

    def test_auto_paper_entries_logs_disabled_candidates(self):

        df = pd.DataFrame([
            {
                "Symbol": "NVDA",
                "Action Status": "ENTER_PAPER",
            }
        ])
        controls = {
            "auto_paper_enabled": False,
            "max_daily": 1,
        }

        with patch(
            "app.runtime.paper_automation_support._paper_trade_candidates",
            return_value=df
        ), patch(
            "app.runtime.paper_automation_support._record_auto_paper_decision"
        ) as record_decision, patch(
            "app.state.paper_trade_manager.load_paper_trades",
            return_value={}
        ):

            result = run_auto_paper_entries(df, controls)

        self.assertEqual(result, [])
        record_decision.assert_called_once()
        self.assertEqual(record_decision.call_args.args[1], "SKIPPED")

    def test_auto_paper_exits_closes_when_exit_reason_exists(self):

        df = pd.DataFrame([
            {
                "Symbol": "NVDA",
                "Price": 105,
            }
        ])
        controls = {
            "auto_exit_enabled": True,
        }
        trades = {
            "trade": {
                "symbol": "NVDA",
                "status": "OPEN",
                "entry_price": 100,
            }
        }

        with patch(
            "app.state.paper_trade_manager.load_paper_trades",
            return_value=trades
        ), patch(
            "app.runtime.paper_automation_support._auto_exit_reason",
            return_value="Auto paper exit: target hit"
        ), patch(
            "app.runtime.paper_automation_support._scanner_context_from_row",
            return_value={"Symbol": "NVDA"}
        ), patch(
            "app.runtime.paper_automation_support._close_paper_trade"
        ) as close_trade:

            result = run_auto_paper_exits(df, controls)

        self.assertEqual(result, ["NVDA"])
        close_trade.assert_called_once()
        self.assertEqual(close_trade.call_args.args[0], "NVDA")


if __name__ == "__main__":

    unittest.main()