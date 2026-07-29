import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.ui.cache.report_state_builder import (
    build_historical_v2_learning,
    build_historical_observational_analytics,
    build_historical_blocking_trends,
    build_historical_trade_efficiency,
    build_report_state_payload,
    write_report_state,
)


class ReportStateBuilderTests(unittest.TestCase):

    def test_builds_historical_efficiency_from_validation_caches(self):

        history = build_historical_trade_efficiency([
            {
                "trading_day": "2026-07-18",
                "kpis": {"paper": {"win_rate": 50}},
                "trade_efficiency": {
                    "summary": {"average_capture": 72, "average_tes": 86, "average_r": 0.84},
                    "charts": {
                        "capture_by_setup": [{"Setup": "EMA Pullback", "Average Trend Capture %": 72}],
                        "capture_by_regime": [{"Market Regime": "TRENDING_BULL", "Average Trend Capture %": 72}],
                        "exit_verdict": [{"Exit Verdict": "EXCELLENT_EXIT", "Count": 1}],
                    },
                },
            },
        ])

        self.assertEqual(history["daily"][0]["Capture"], 72)
        self.assertEqual(history["setup"][0]["Setup"], "EMA Pullback")
        self.assertEqual(history["exit"][0]["Count"], 1)

    def test_builds_ready_report_state_payload(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)
            report = root / "daily_validation_report.html"
            scanner = root / "scanner_output_close.csv"
            report.write_text("<html></html>", encoding="utf-8")
            scanner.write_text("Symbol\nNVDA\n", encoding="utf-8")

            payload = build_report_state_payload(
                "2026-07-18",
                daily_report_path=report,
                root_report_path=root / "missing.html",
                scanner_path=scanner,
                scan_id="scan",
            )

        self.assertIn(payload["status"], {"READY", "STALE"})
        self.assertEqual(payload["metadata"]["scan_id"], "scan")
        self.assertTrue(payload["daily_report"]["exists"])
        self.assertEqual(payload["scan_id"], "scan")

    def test_builds_historical_v2_learning(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)
            for day, score in [("2026-07-18", 80), ("2026-07-19", 90)]:
                directory = root / day
                directory.mkdir()
                pd.DataFrame([{
                    "trading_day": day,
                    "trend_age": 1,
                    "entry_efficiency_score": score,
                    "trend_capture_pct": 70,
                    "tes": 85,
                    "exit_phase": "TREND_FAILURE",
                }]).to_csv(directory / "v2_learning_dataset.csv", index=False)

            with patch("app.ui.cache.report_state_builder.DAILY_DIR", root):
                history = build_historical_v2_learning()

        self.assertEqual(len(history["daily"]), 2)
        self.assertEqual(history["daily"][0]["Entry Efficiency"], 80.0)
        self.assertEqual(history["exit_phase"][0]["Exit Phase"], "TREND_FAILURE")

    def test_historical_v2_learning_ignores_v1_rows(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)
            directory = root / "2026-07-28"
            directory.mkdir()
            pd.DataFrame([
                {"trading_day": "2026-07-28", "engine_version": "v1", "entry_efficiency_score": 90},
                {"trading_day": "2026-07-28", "engine_version": "v2", "entry_efficiency_score": 80},
            ]).to_csv(directory / "v2_learning_dataset.csv", index=False)

            with patch("app.ui.cache.report_state_builder.DAILY_DIR", root):
                history = build_historical_v2_learning()

        self.assertEqual(history["daily"][0]["Entry Efficiency"], 80.0)

    def test_builds_historical_entry_timing_and_ranking(self):

        history = build_historical_observational_analytics([
            {
                "trading_day": "2026-07-18",
                "observational_analytics": {
                    "entry_timing": {
                        "average_score": 76,
                        "late_entries": [{"Symbol": "AAPL"}],
                    },
                    "trade_ranking": [
                        {"Trade Quality Score": 90, "Candidate Rank": 1},
                        {"Trade Quality Score": 70, "Candidate Rank": 2},
                    ],
                },
            }
        ])

        row = history["daily"][0]
        self.assertEqual(row["Average Entry Timing"], 76)
        self.assertEqual(row["Late Entries"], 1)
        self.assertEqual(row["Average TQS"], 80)
        self.assertEqual(row["Average Rank"], 1.5)

    def test_builds_historical_blocking_stage_trends(self):

        history = build_historical_blocking_trends([
            {
                "trading_day": "2026-07-18",
                "observational_analytics": {
                    "blocking_stage_summary": {
                        "stages": [
                            {"stage": "Risk", "count": 4, "percentage": 50},
                            {"stage": "Option", "count": 2, "percentage": 25},
                        ]
                    }
                },
            }
        ])

        self.assertEqual(
            history["dominant_daily"][0]["Dominant Blocking Stage"],
            "Risk",
        )
        self.assertEqual(len(history["daily"]), 2)

    def test_write_report_state_writes_live_and_daily_json(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)
            live = root / "live_report_state.json"
            daily = root / "daily_report_state.json"

            with patch(
                "app.ui.cache.report_state_builder.live_path",
                return_value=live
            ), patch(
                "app.ui.cache.report_state_builder.daily_path",
                return_value=daily
            ):

                payload = write_report_state(
                    "2026-07-18",
                    scan_id="scan"
                )

            self.assertTrue(live.exists())
            self.assertTrue(daily.exists())
            self.assertEqual(payload["scan_id"], "scan")
            self.assertEqual(payload["metadata"]["scan_id"], "scan")


if __name__ == "__main__":

    unittest.main()