import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.ui.cache.validation_state_builder import (
    _strategy_confidence,
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
                    "Entry Timing Score": 86,
                    "Entry Timing Grade": "EXCELLENT",
                    "Trade Quality Score": 91,
                    "Candidate Rank": 1,
                },
                {
                    "Symbol": "MSFT",
                    "Action Status": "REVIEW_TV_CHART",
                    "Entry Timing Score": 42,
                    "Entry Timing Grade": "LATE_ENTRY",
                    "Trade Quality Score": 58,
                    "Candidate Rank": 2,
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
                    "Exit Verdict Reason": "Trend continued after exit.",
                    "Exit Trigger": "EMA9_INVALIDATION",
                    "Engineering Recommendation": "Review the exit delay.",
                    "Entry Grade": "B",
                    "Exit Grade": "B",
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
        self.assertEqual(payload["trade_efficiency"]["trades"][0]["Exit Trigger"], "EMA9_INVALIDATION")
        self.assertEqual(payload["trade_efficiency"]["trades"][0]["Engineering Recommendation"], "Review the exit delay.")
        entry_finding = payload["diagnosis"]["entry"]["findings"][0]
        self.assertEqual(entry_finding["reason"], "EMA_PULLBACK capture was observed today.")
        self.assertEqual(entry_finding["evidence"], "1 trades | average capture 60.0%")
        self.assertEqual(entry_finding["action"], "Observe; DO NOT CHANGE RULE.")
        self.assertEqual(payload["diagnosis"]["exit"]["status"], "NO_EXIT_LOSS_EVIDENCE")
        self.assertEqual(payload["strategy_confidence"]["evidence_days"], 1)
        self.assertEqual(payload["strategy_confidence"]["confidence_pct"], 18)
        self.assertEqual(payload["entry_exit_v2"]["summary"]["Trades compared"], 0)
        self.assertEqual(payload["entry_exit_v2"]["trend_outcomes"], [])
        self.assertEqual(payload["v2_learning"]["summary"]["Completed learning records"], 0)
        self.assertEqual(payload["decision_analysis"]["summary"]["Scanned"], 2)
        self.assertEqual(payload["decision_analysis"]["summary"]["Review"], 1)
        self.assertIn("candidate_intelligence", payload)
        self.assertEqual(
            payload["observational_analytics"]["entry_timing"]["average_score"],
            64,
        )
        self.assertEqual(
            len(payload["observational_analytics"]["entry_timing"]["late_entries"]),
            1,
        )
        self.assertEqual(
            len(payload["observational_analytics"]["decision_waterfalls"]),
            2,
        )

    def test_strategy_confidence_requires_sample_and_time_evidence(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            for day in range(1, 21):

                directory = root / f"2026-07-{day:02d}"
                directory.mkdir()
                pd.DataFrame([
                    {"Trend Capture %": 60}
                    for _ in range(4)
                ]).to_csv(directory / "trend_capture_analysis.csv", index=False)

            with patch("app.ui.cache.validation_state_builder.DAILY_DIR", root):

                confidence = _strategy_confidence(
                    "2026-07-20",
                    pd.DataFrame(),
                )

        self.assertEqual(confidence["evidence_days"], 20)
        self.assertEqual(confidence["completed_trades"], 80)
        self.assertEqual(confidence["confidence_pct"], 92)
        self.assertTrue(confidence["rule_change_allowed"])

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