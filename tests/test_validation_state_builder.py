import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.ui.cache.validation_state_builder import (
    build_validation_state_payload,
    write_validation_state,
)


class ValidationStateBuilderTests(unittest.TestCase):

    def test_builds_validation_state_payload(self):

        payload = build_validation_state_payload(
            "2026-07-18",
            scanner=pd.DataFrame([
                {
                    "Symbol": "NVDA",
                    "Action Status": "ENTER_PAPER",
                },
                {
                    "Symbol": "MSFT",
                    "Action Status": "REVIEW_TV_CHART",
                },
            ]),
            paper_events=pd.DataFrame([
                {
                    "event_type": "AUTO_EXIT",
                    "r_multiple": 1.2,
                }
            ]),
            trend_capture=pd.DataFrame([
                {
                    "Trend Capture %": 60,
                    "Maximum Favorable Excursion": 10,
                    "Maximum Adverse Excursion": 2,
                    "Left On Table": 4,
                    "Trend Health Score": 8,
                    "Exit Verdict": "NEEDS_REVIEW",
                    "Setup": "EMA_PULLBACK",
                    "Market Regime": "TRENDING_BULL",
                    "Exit Reason": "EMA",
                }
            ]),
            generated_at="2026-07-18T10:00:00Z",
            scan_id="2026-07-18_100000",
        )

        self.assertEqual(payload["kpis"]["scanner"]["enter_paper"], 1)
        self.assertEqual(payload["metadata"]["scan_id"], "2026-07-18_100000")
        self.assertEqual(payload["kpis"]["paper"]["closed_trades"], 1)
        self.assertEqual(payload["kpis"]["trend_capture"]["average_capture"], 60)
        self.assertEqual(payload["trend_capture"]["exit_verdict_distribution"][0]["Exit Verdict"], "NEEDS_REVIEW")
        self.assertEqual(payload["trade_efficiency"]["summary"]["average_capture"], 60)
        self.assertEqual(payload["trade_efficiency"]["trades"][0]["Capture %"], 60)

    def test_write_validation_state_writes_live_and_daily_json(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)
            live = root / "live_validation_state.json"
            daily = root / "daily_validation_state.json"

            with patch(
                "app.ui.cache.validation_state_builder._read_csv",
                return_value=pd.DataFrame()
            ), patch(
                "app.ui.cache.validation_state_builder.live_path",
                return_value=live
            ), patch(
                "app.ui.cache.validation_state_builder.daily_path",
                return_value=daily
            ):

                payload = write_validation_state(
                    "2026-07-18",
                    scan_id="scan"
                )

            self.assertTrue(live.exists())
            self.assertTrue(daily.exists())
            self.assertEqual(payload["scan_id"], "scan")
            self.assertEqual(payload["metadata"]["scan_id"], "scan")


if __name__ == "__main__":

    unittest.main()