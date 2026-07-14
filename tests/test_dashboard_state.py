import unittest

import pandas as pd

from app.ui.dashboard_state import build_dashboard_state


class DashboardStateTests(unittest.TestCase):

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


if __name__ == "__main__":

    unittest.main()