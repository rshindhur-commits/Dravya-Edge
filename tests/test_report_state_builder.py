import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.ui.cache.report_state_builder import (
    _VALIDATION_STATE_CACHE,
    _daily_validation_states,
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


class DailyValidationStateCacheTests(unittest.TestCase):
    """Re-reading finished days was the worker's largest memory cost."""

    def setUp(self):

        _VALIDATION_STATE_CACHE.clear()

    def _write_day(self, root, name, payload="x"):

        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "validation_state.json"
        path.write_text(
            json.dumps({"trading_day": name, "note": payload}),
            encoding="utf-8",
        )

        return path

    def test_a_finished_day_is_not_read_twice(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)
            self._write_day(root, "2026-08-01")
            self._write_day(root, "2026-08-02")

            with patch("app.ui.cache.report_state_builder.DAILY_DIR", root):

                first = _daily_validation_states()
                reads = []
                original = Path.read_text

                def counting_read_text(self, *args, **kwargs):

                    reads.append(str(self))

                    return original(self, *args, **kwargs)

                with patch.object(Path, "read_text", counting_read_text):

                    second = _daily_validation_states()

            self.assertEqual(len(first), 2)
            self.assertEqual(second, first)
            self.assertEqual(reads, [], "cached days should not be re-read")

    def test_a_changed_day_is_picked_up(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)
            self._write_day(root, "2026-08-01", payload="before")

            with patch("app.ui.cache.report_state_builder.DAILY_DIR", root):

                before = _daily_validation_states()
                # Today's file keeps changing all session; it must not go stale.
                self._write_day(root, "2026-08-01", payload="after-and-longer")
                after = _daily_validation_states()

            self.assertEqual(before[0]["note"], "before")
            self.assertEqual(after[0]["note"], "after-and-longer")

    def test_the_cache_does_not_grow_past_the_window(self):

        with tempfile.TemporaryDirectory() as temp_dir:

            root = Path(temp_dir)

            for day in range(1, 26):

                self._write_day(root, f"2026-08-{day:02d}")

            with patch("app.ui.cache.report_state_builder.DAILY_DIR", root):

                states = _daily_validation_states(limit=20)

            self.assertEqual(len(states), 20)
            self.assertEqual(len(_VALIDATION_STATE_CACHE), 20)
            # Newest first, so the oldest days fall out rather than accumulate.
            self.assertEqual(states[0]["trading_day"], "2026-08-25")


if __name__ == "__main__":

    unittest.main()