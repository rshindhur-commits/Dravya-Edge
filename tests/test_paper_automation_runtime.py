import unittest
from unittest.mock import patch

import pandas as pd

from app.runtime.paper_automation import run_auto_paper_entries, run_auto_paper_exits
from app.runtime.paper_automation_support import (
    _auto_paper_entry_reason,
    _record_auto_paper_decision,
    load_auto_paper_controls,
    should_record_auto_paper_session_skip,
)


class PaperAutomationRuntimeTests(unittest.TestCase):

    def test_control_loader_uses_persisted_settings(self):

        with patch(
            "app.runtime.paper_automation_support.load_json_file",
            return_value={
                "auto_paper_enabled": True,
                "auto_paper_max_daily": 2,
                "auto_paper_min_setup": 81,
                "auto_paper_min_rr": 2.3,
                "auto_paper_direction": "Calls",
                "auto_paper_eod_close_enabled": True,
            },
        ):
            controls = load_auto_paper_controls()

        self.assertTrue(controls["auto_paper_enabled"])
        self.assertEqual(controls["max_daily"], 2)
        self.assertEqual(controls["min_setup"], 81.0)
        self.assertEqual(controls["min_rr"], 2.3)
        self.assertEqual(controls["direction"], "Calls")
        self.assertTrue(controls["eod_close_enabled"])

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

    def test_auto_paper_entry_accepts_top_rank_without_display_tag(self):

        row = pd.Series({
            "Symbol": "AAPL",
            "Action Status": "ENTER_PAPER",
            "Top Candidate": None,
            "Candidate Rank": 2,
            "Setup %": 85,
            "Candidate RR": 2.2,
            "Option Quality Score": 85,
            "Option Spread %": 4,
            "Option Bid": 3.9,
            "Option Ask": 4.1,
            "Realtime Ready": True,
            "Candidate Direction": "CALL",
            "Event Blocked": False,
            "Regime Blocked": False,
        })
        controls = {
            "auto_paper_enabled": True,
            "min_rr": 1.8,
            "min_setup": 70,
            "direction": "Both",
            "max_daily": 3,
        }
        with patch(
            "app.runtime.paper_automation_support._current_et",
            return_value=pd.Timestamp("2026-07-28 10:00:00", tz="America/New_York").to_pydatetime(),
        ), patch(
            "app.runtime.paper_automation_support.evaluate_entry_gate",
            return_value=(True, "ELIGIBLE"),
        ):
            allowed, reason = _auto_paper_entry_reason(row, controls, {})

        self.assertTrue(allowed)
        self.assertEqual(reason, "ELIGIBLE")

    def test_auto_paper_logs_one_off_window_system_skip(self):

        df = pd.DataFrame([
            {"Symbol": "NVDA"},
            {"Symbol": "AAPL"},
        ])
        controls = {"auto_paper_enabled": True}
        with patch(
            "app.runtime.paper_automation_support.auto_paper_session_block_reason",
            return_value="outside auto-entry window",
        ), patch(
            "app.runtime.paper_automation_support.should_record_auto_paper_session_skip",
            return_value=True,
        ), patch(
            "app.runtime.paper_automation_support._record_auto_paper_decision",
        ) as record_decision:
            result = run_auto_paper_entries(df, controls)

        self.assertEqual(result, [])
        record_decision.assert_called_once_with(
            "SYSTEM",
            "SKIPPED",
            "outside auto-entry window",
            controls=controls,
        )

    def test_session_skip_is_not_repeated_within_market_session(self):

        now = pd.Timestamp("2026-07-28 08:00:00", tz="America/New_York").to_pydatetime()
        existing = [{
            "trading_day": "2026-07-28",
            "market_session": "PREMARKET",
            "symbol": "SYSTEM",
            "decision": "SKIPPED",
            "reason": "outside auto-entry window",
        }]
        with patch(
            "app.runtime.paper_automation_support.load_json_file",
            return_value=existing,
        ):
            should_record = should_record_auto_paper_session_skip(
                "outside auto-entry window",
                now=now,
            )

        self.assertFalse(should_record)

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

    def test_blocked_entry_records_actual_gate_not_action_status(self):

        row = pd.Series({
            "Symbol": "AAPL",
            "Action Status": "ENTER_PAPER",
            "Blocked By": "ENTER_PAPER",
            "Action Reason": "Risk and option checks passed",
        })
        with patch(
            "app.runtime.paper_automation_support.append_daily_auto_paper_decision"
        ) as append_daily, patch(
            "app.runtime.paper_automation_support.update_recent_auto_paper_log"
        ), patch(
            "app.runtime.paper_automation_support._current_et"
        ) as current_et:
            current_et.return_value = pd.Timestamp("2026-07-28 10:00:00").to_pydatetime()
            _record_auto_paper_decision(
                "AAPL",
                "BLOCKED",
                "not top candidate",
                row,
                controls={"min_rr": 2.0, "min_setup": 70},
            )

        entry = append_daily.call_args.args[0]
        self.assertEqual(entry["decision"], "BLOCKED")
        self.assertEqual(entry["action_status"], "ENTER_PAPER")
        self.assertEqual(entry["blocked_by"], "not top candidate")
        self.assertEqual(entry["scanner_blocked_by"], "ENTER_PAPER")
        self.assertEqual(entry["action_reason"], "Risk and option checks passed")


if __name__ == "__main__":

    unittest.main()