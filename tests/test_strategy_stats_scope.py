"""Performance stats must cover strategy trades only.

`include_in_strategy_stats` is set at open -- False for review-validation entries
and manual dashboard trades, True for real auto-paper entries -- and was read by
nothing. Of 19 closed trades 10 carried it as False, and they were not a random
sample: that set won 50% against the strategy's 25%, so the blended record read
-0.37R at 38.9% wins when the strategy itself was -0.44R at 25%.
"""

import unittest

import pandas as pd

from app.analytics.performance_statistics import (
    build_performance_statistics,
    build_spread_calibration,
    strategy_trades_only,
)


class ScopeTests(unittest.TestCase):

    def _frame(self):
        return pd.DataFrame([
            {"r_multiple": -1.88, "include_in_strategy_stats": True},
            {"r_multiple": 1.35, "include_in_strategy_stats": True},
            {"r_multiple": -4.12, "include_in_strategy_stats": False},
            {"r_multiple": 1.13, "include_in_strategy_stats": False},
        ])

    def test_flagged_trades_are_dropped(self):

        kept = strategy_trades_only(self._frame())

        self.assertEqual(len(kept), 2)
        self.assertEqual(sorted(kept.r_multiple.tolist()), [-1.88, 1.35])

    def test_stats_reflect_only_strategy_trades(self):

        self.assertEqual(build_performance_statistics(self._frame())["win_rate"], 50.0)

    def test_string_false_is_honoured(self):
        """The flag survives a CSV round trip as text."""

        frame = pd.DataFrame([
            {"r_multiple": 1.0, "include_in_strategy_stats": "False"},
            {"r_multiple": 2.0, "include_in_strategy_stats": "True"},
        ])

        kept = strategy_trades_only(frame)

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept.iloc[0].r_multiple, 2.0)


class DefaultInclusionTests(unittest.TestCase):
    """Absent means include. Defaulting out would silently shrink the record."""

    def test_missing_column_keeps_every_trade(self):

        frame = pd.DataFrame([{"r_multiple": 1.0}, {"r_multiple": -1.0}])

        self.assertEqual(len(strategy_trades_only(frame)), 2)

    def test_null_flag_keeps_the_trade(self):

        frame = pd.DataFrame([
            {"r_multiple": 1.0, "include_in_strategy_stats": None},
            {"r_multiple": -1.0, "include_in_strategy_stats": False},
        ])

        kept = strategy_trades_only(frame)

        self.assertEqual(len(kept), 1)
        self.assertEqual(kept.iloc[0].r_multiple, 1.0)


class DegradedInputTests(unittest.TestCase):

    def test_empty_and_none_are_safe(self):

        self.assertEqual(len(strategy_trades_only(pd.DataFrame())), 0)
        self.assertEqual(len(strategy_trades_only(None)), 0)

    def test_all_excluded_reports_no_measurement_not_a_zero_one(self):
        """Consistent with the module's rule: absent is None, never break-even."""

        frame = pd.DataFrame([
            {"r_multiple": 1.0, "include_in_strategy_stats": False},
        ])

        self.assertIsNone(build_performance_statistics(frame)["win_rate"])


class RepositoryLiftTests(unittest.TestCase):
    """The flag lives in JSONB; it has to reach the analytics layer to matter.

    Asserted behaviourally rather than by inspecting the source of one method:
    the previous version searched `fetch_closed` for the field name and broke the
    moment the flattening was shared with `fetch_closed_between`, without
    anything actually regressing.
    """

    def _flatten(self, row):
        from app.db.paper_trade_repository import PaperTradeRepository

        return PaperTradeRepository.__new__(PaperTradeRepository)._flatten_closed([row])[0]

    def test_flag_is_lifted_out_of_the_payload(self):

        record = self._flatten({
            "symbol": "NVDA",
            "r_multiple": 1.0,
            "payload": {"include_in_strategy_stats": False},
        })

        self.assertFalse(record["include_in_strategy_stats"])

    def test_top_level_column_wins_over_the_payload(self):

        record = self._flatten({
            "symbol": "NVDA",
            "exit_reason": "TARGET",
            "payload": {"exit_reason": "STALE"},
        })

        self.assertEqual(record["exit_reason"], "TARGET")

    def test_absent_flag_is_left_absent_rather_than_defaulted(self):
        """`strategy_trades_only` treats absent as include. Defaulting it here to
        False would silently shrink the record."""

        record = self._flatten({"symbol": "NVDA", "payload": {}})

        self.assertIsNone(record["include_in_strategy_stats"])


class PremiumMeasurabilityTests(unittest.TestCase):
    """Pre-`eb56f75` trades have no knowable entry price.

    `option_pnl_pct_net` read the entry ask and the close ask from the same
    live-refreshed key, so it evaluated to minus the current spread on every
    trade regardless of outcome. All four priced trades on record match that
    signature exactly. Averaging them produced a confident 0% net win rate on a
    7.95% spread that measured nothing at all.
    """

    def test_trades_without_a_frozen_entry_ask_are_not_priced(self):
        frame = pd.DataFrame([
            {"r_multiple": -0.74, "option_pnl_pct_net": -1.94, "option_spread_cost_pct": 11.62},
        ])

        stats = build_performance_statistics(frame)

        self.assertEqual(stats["completed_trades"], 1)
        self.assertEqual(stats["priced_trades"], 0)
        self.assertIsNone(stats["net_win_rate"])
        self.assertIsNone(stats["average_spread_cost_pct"])

    def test_trades_with_a_frozen_entry_ask_are_priced(self):
        frame = pd.DataFrame([
            {
                "r_multiple": 0.6,
                "option_entry_ask": 5.15,
                "option_pnl_pct_net": 3.2,
                "option_spread_cost_pct": 2.1,
            },
        ])

        stats = build_performance_statistics(frame)

        self.assertEqual(stats["priced_trades"], 1)
        self.assertEqual(stats["net_win_rate"], 100.0)

    def test_r_figures_are_unaffected_by_premium_measurability(self):
        """R is measured on the underlying and was never touched by the bug."""

        frame = pd.DataFrame([{"r_multiple": -0.74}, {"r_multiple": 0.6}])

        stats = build_performance_statistics(frame)

        self.assertEqual(stats["completed_trades"], 2)
        self.assertEqual(stats["total_r"], -0.14)


class SpreadCalibrationTests(unittest.TestCase):

    def test_no_measurable_trades_reports_zero_rather_than_guessing(self):
        frame = pd.DataFrame([{"r_multiple": -0.74, "option_spread_cost_pct": 11.62}])

        calibration = build_spread_calibration(frame)

        self.assertEqual(calibration["measurable_trades"], 0)
        self.assertEqual(calibration["rows"], [])

    def test_flags_a_high_score_on_an_expensive_round_trip(self):
        frame = pd.DataFrame([{
            "symbol": "NVDA",
            "option_entry_ask": 5.15,
            "option_quality_score": 95,
            "option_entry_spread_pct": 2.0,
            "option_spread_cost_pct": 11.62,
        }])

        calibration = build_spread_calibration(frame)

        self.assertEqual(calibration["measurable_trades"], 1)
        self.assertTrue(calibration["rows"][0]["high_score_wide_spread"])
        self.assertEqual(calibration["high_score_wide_spread_count"], 1)
        # Realised well above what was quoted at entry: the spread widened
        # while the position was held.
        self.assertEqual(calibration["quality_vs_cost_gap"], 9.62)

    def test_cheap_round_trip_on_a_high_score_is_not_flagged(self):
        frame = pd.DataFrame([{
            "symbol": "NVDA",
            "option_entry_ask": 5.15,
            "option_quality_score": 95,
            "option_entry_spread_pct": 2.0,
            "option_spread_cost_pct": 2.2,
        }])

        calibration = build_spread_calibration(frame)

        self.assertFalse(calibration["rows"][0]["high_score_wide_spread"])
        self.assertEqual(calibration["high_score_wide_spread_count"], 0)


if __name__ == "__main__":
    unittest.main()
