import unittest

import pandas as pd

from app.analytics.learning_engine import build_daily_learning_summary


class LearningEngineTests(unittest.TestCase):

    def test_daily_summary_aggregates_shadow_comparisons_and_blockers(self):
        summary = build_daily_learning_summary(
            "2026-07-23",
            pd.DataFrame([{"entry_efficiency_score": 80, "trend_capture_pct": 70}]),
            pd.DataFrame([{"final_r_v1": 0.5, "final_r_v2": 1.0, "final_r_delta": 0.5}]),
            pd.DataFrame([{"Trend Health Score": 85, "Exit Verdict": "EXIT_TOO_EARLY"}]),
            pd.DataFrame([{"stage": "Risk", "v2_exit_confidence_score": 72}, {"stage": "Risk"}]),
        )
        self.assertEqual(summary["v2_shadow_trades"], 1)
        self.assertEqual(summary["avg_r_delta"], 0.5)
        self.assertEqual(summary["premature_exits"], 1)
        self.assertEqual(summary["blocking_stages"]["Risk"], 2)

    def test_daily_summary_excludes_v1_records_from_v2_shadow_count(self):
        summary = build_daily_learning_summary(
            "2026-07-28",
            pd.DataFrame([
                {"engine_version": "v1", "entry_efficiency_score": 90},
                {"engine_version": "v2", "entry_efficiency_score": 80},
            ]),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
        )

        self.assertEqual(summary["v2_shadow_trades"], 1)
        self.assertEqual(summary["avg_entry_efficiency"], 80.0)