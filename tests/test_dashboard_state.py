import tempfile
import unittest
from pathlib import Path

import pandas as pd

from app.ui.dashboard_state import (
    build_dashboard_state,
    build_today_performance_summary,
    write_dashboard_state,
)


class DashboardStateTests(unittest.TestCase):

    def test_builds_today_performance_from_cached_analytics_frames(self):

        summary = build_today_performance_summary(
            pd.DataFrame([
                {
                    "event_type": "AUTO_EXIT",
                    "r_multiple": 1.2,
                    "closed_at": "2026-07-18T10:00:00-04:00",
                },
                {
                    "event_type": "AUTO_EXIT",
                    "r_multiple": -0.4,
                    "closed_at": "2026-07-18T10:15:00-04:00",
                },
            ]),
            pd.DataFrame([
                {
                    "Trend Capture %": 72.4,
                    "Trade Efficiency Score": 86,
                    "Left On Table": 18.5,
                    "Exit Verdict": "EXCELLENT_EXIT",
                },
            ]),
        )

        self.assertEqual(summary["completed_trades"], 2)
        self.assertEqual(summary["win_rate"], 50)
        self.assertEqual(summary["average_r"], 0.4)
        self.assertEqual(summary["average_trend_capture"], 72.4)
        self.assertEqual(summary["excellent_exits"], 1)
        self.assertEqual(summary["last_completed_trade"], "2026-07-18T14:15:00+00:00")

    def test_today_performance_has_no_completion_timestamp_before_first_exit(self):

        summary = build_today_performance_summary()

        self.assertEqual(summary["completed_trades"], 0)
        self.assertIsNone(summary["last_completed_trade"])

    def test_builds_command_center_state_from_scanner_rows(self):

        state = build_dashboard_state(
            pd.DataFrame(
                [
                    {
                        "Symbol": "ARM",
                        "Scan ID": "2026-07-13_175750",
                        "Final Signal": "HIGH CONVICTION BEARISH",
                        "Candidate Direction": "PUT",
                        "ENTRY_SETUP_CANDIDATE": "BREAKDOWN_SHORT",
                        "ENTRY_READINESS": 82,
                        "Candidate RR": 1.8,
                        "Option Quality Score": 80,
                        "ENTRY_GATE_FAILURE_STAGE": "Risk",
                        "Action Status": "WAIT",
                        "Next Condition": "Below 148.32",
                    },
                    {
                        "Symbol": "MSFT",
                        "Final Signal": "NEUTRAL",
                        "Action Status": "WAIT",
                        "ENTRY_GATE_FAILURE_STAGE": "Momentum",
                    },
                ]
            ),
            generated_at="2026-07-14T09:42:17",
        )

        self.assertEqual(state["market_bias"], "BEARISH")
        self.assertEqual(state["scan_id"], "2026-07-13_175750")
        self.assertEqual(state["data_version"], "2026-07-13_175750")
        self.assertEqual(state["best_put"]["symbol"], "ARM")
        self.assertEqual(state["summary"]["scanned"], 2)
        self.assertEqual(state["summary"]["trades"], 0)
        self.assertEqual(state["top_candidates"][0]["blocked"], "Risk")

    def test_writes_dashboard_state_with_runtime_metadata(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            output = Path(temp_dir) / "dashboard_state.json"
            state = write_dashboard_state(
                pd.DataFrame([
                    {
                        "Symbol": "NVDA",
                        "Scan ID": "2026-07-18_100000",
                        "Final Signal": "BULLISH",
                        "Action Status": "ENTER_PAPER",
                    }
                ]),
                [output],
                generated_at="2026-07-18T10:00:00",
                scanner_health={"health_score": 95},
                telegram_summary={"sent_count": 1}
            )

            self.assertTrue(output.exists())
            self.assertEqual(state["scanner_health"]["health_score"], 95)
            self.assertEqual(state["telegram_summary"]["sent_count"], 1)


if __name__ == "__main__":

    unittest.main()