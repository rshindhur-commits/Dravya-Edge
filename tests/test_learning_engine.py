import inspect
import unittest

import pandas as pd

from app.analytics import learning_engine

from app.analytics.learning_engine import (
    _blocking_rule_counts,
    _counts,
    build_daily_learning_summary,
)


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


class WaterfallAggregationTests(unittest.TestCase):
    """`_waterfalls_for` groups in Postgres, so the frame carries group sizes.

    The summary must read the same numbers off the grouped frame that it read
    off the raw one, and the CSV fallback -- which has no `row_count` -- must
    keep being counted row by row.
    """

    EXPANDED = pd.DataFrame(
        [{"stage": "Entry", "rule_name": "Setup", "blocking": True}] * 3
        + [{"stage": "Entry", "rule_name": "Setup", "blocking": False}] * 5
        + [{"stage": "Risk", "rule_name": "Stop", "blocking": True}] * 2
    )

    GROUPED = pd.DataFrame([
        {"stage": "Entry", "rule_name": "Setup", "blocking": True, "row_count": 3},
        {"stage": "Entry", "rule_name": "Setup", "blocking": False, "row_count": 5},
        {"stage": "Risk", "rule_name": "Stop", "blocking": True, "row_count": 2},
    ])

    def test_grouped_frame_counts_the_rows_behind_each_group(self):
        self.assertEqual(dict(_counts(self.GROUPED, "stage")), {"Entry": 8, "Risk": 2})

    def test_frame_without_row_count_is_counted_row_by_row(self):
        self.assertEqual(dict(_counts(self.EXPANDED, "stage")), {"Entry": 8, "Risk": 2})

    def test_missing_column_and_empty_frame_yield_no_counts(self):
        self.assertTrue(_counts(self.GROUPED, "absent").empty)
        self.assertTrue(_counts(pd.DataFrame(), "stage").empty)
        self.assertTrue(_counts(None, "stage").empty)

    def test_blocking_rules_ignore_non_blocking_group_sizes(self):
        # `Setup` blocked 3 candidates and passed 5. Weighting by `row_count`
        # without filtering first would report 8.
        self.assertEqual(
            _blocking_rule_counts(self.GROUPED), {"Setup": 3, "Stop": 2}
        )

    def test_summary_reads_the_same_numbers_from_either_frame(self):
        def summarise(waterfalls):
            summary = build_daily_learning_summary(
                "2026-08-21",
                pd.DataFrame(),
                pd.DataFrame(),
                pd.DataFrame(),
                waterfalls,
            )
            return summary["blocking_stages"], summary["blocking_rules"]

        self.assertEqual(summarise(self.GROUPED), summarise(self.EXPANDED))
        self.assertEqual(
            summarise(self.GROUPED), ({"Entry": 8, "Risk": 2}, {"Setup": 3, "Stop": 2})
        )

    def test_query_asks_the_database_to_group(self):
        # The point of the change: the day's rows are counted server-side and
        # never cross the wire. A `select ... from decision_waterfall` without a
        # `group by` is the regression this guards.
        source = inspect.getsource(learning_engine._waterfalls_for)
        self.assertIn("count(*) as row_count", source)
        self.assertIn("group by stage, rule_name, blocking", source)
        self.assertNotIn("select symbol, stage", source)


class ExitConfidenceTests(unittest.TestCase):
    """`avg_exit_confidence` read a column its frame never carried.

    It asked the waterfall frame for `v2_exit_confidence_score`. That column
    belongs to `entry_exit_v2_shadow.csv`; the argument was switched to the
    decision-waterfall source to give `blocking_stages` a `stage` column, and the
    confidence silently went NULL on every row `daily_engine_summary` has written
    since.
    """

    def test_confidence_comes_from_the_closed_trades(self):
        summary = build_daily_learning_summary(
            "2026-08-21",
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame([
                {"last_exit_confidence_score": 40},
                {"last_exit_confidence_score": 60},
            ]),
        )

        self.assertEqual(summary["avg_exit_confidence"], 50.0)

    def test_trades_missing_the_score_are_skipped_not_counted_as_zero(self):
        # 44 of 65 closed trades carry it. Averaging the gaps in as zeros would
        # halve the number and look like a real decline in exit confidence.
        summary = build_daily_learning_summary(
            "2026-08-21",
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame([
                {"last_exit_confidence_score": 40},
                {"last_exit_confidence_score": None},
                {"symbol": "NVDA"},
            ]),
        )

        self.assertEqual(summary["avg_exit_confidence"], 40.0)

    def test_no_closed_trades_reports_nothing_rather_than_crashing(self):
        for empty in (None, pd.DataFrame()):
            summary = build_daily_learning_summary(
                "2026-08-21", pd.DataFrame(), pd.DataFrame(),
                pd.DataFrame(), pd.DataFrame(), empty,
            )
            self.assertIsNone(summary["avg_exit_confidence"])

    def test_the_waterfall_can_no_longer_supply_it(self):
        """The regression: a waterfall column must not resurrect the dead read."""

        summary = build_daily_learning_summary(
            "2026-08-21",
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame([{"stage": "Risk", "v2_exit_confidence_score": 72}]),
            pd.DataFrame(),
        )

        self.assertIsNone(summary["avg_exit_confidence"])

    def test_the_repository_lifts_the_score_out_of_the_payload(self):
        """Without the lift the frame has no such column and this is NULL again."""

        from app.db.paper_trade_repository import PaperTradeRepository

        lifted = PaperTradeRepository()._flatten_closed([
            {"symbol": "TSLA", "status": "CLOSED",
             "payload": {"last_exit_confidence_score": 55}},
        ])

        self.assertEqual(lifted[0]["last_exit_confidence_score"], 55)

