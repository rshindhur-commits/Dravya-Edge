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
    """The flag lives in JSONB; it has to reach the analytics layer to matter."""

    def test_flag_is_in_the_lifted_field_list(self):
        import inspect

        from app.db import paper_trade_repository

        source = inspect.getsource(paper_trade_repository.PaperTradeRepository.fetch_closed)

        self.assertIn("include_in_strategy_stats", source)


if __name__ == "__main__":
    unittest.main()
