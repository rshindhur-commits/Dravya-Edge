"""The per-trade review has to run itself, and be safe to re-run.

Reviewing a trade needs the bars that came *after* its exit, so it can only run
post-market -- the same constraint outcome resolution has, which is why both live
in the scan loop's idle branch.

Two derivation bugs were caught on the day this shipped, and both are the reason
the diagnostics are computed once and stored rather than re-derived per question:
a placement percentage that ran past 100 and averaged 287%, and a counterfactual
that scored the best price seen after the exit and so valued the book at +33.65R
against a booked +0.76R.

These cover the guards, matching the resolution, option-leg and baseline jobs:
idle only, once per ET date, its own marker, and never raising into the loop.
"""

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.runtime import outcome_scheduler

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 15, 17, 30, tzinfo=ET)


class DueTests(unittest.TestCase):

    def test_it_does_not_run_during_a_session(self):
        """The bars after an exit do not exist until the session ends."""

        with patch.object(outcome_scheduler, "trade_review_last_run_day",
                          return_value=None):
            self.assertFalse(
                outcome_scheduler.trade_review_due(NOW, idle_reason_value=None)
            )

    def test_it_runs_once_per_et_date_when_idle(self):

        with patch.object(outcome_scheduler, "trade_review_last_run_day",
                          return_value="2026-08-14"):
            self.assertTrue(outcome_scheduler.trade_review_due(NOW, "IDLE"))

        with patch.object(outcome_scheduler, "trade_review_last_run_day",
                          return_value="2026-08-15"):
            self.assertFalse(outcome_scheduler.trade_review_due(NOW, "IDLE"))

    def test_its_marker_is_independent_of_the_other_jobs(self):
        """This fails on the bar cache; the others fail on quota or the database."""

        paths = {
            outcome_scheduler._marker_path(),
            outcome_scheduler._option_leg_marker_path(),
            outcome_scheduler._baseline_marker_path(),
            outcome_scheduler._trade_review_marker_path(),
        }
        self.assertEqual(len(paths), 4, "markers must not collide")

    def test_an_unreadable_marker_reads_as_never_run(self):

        with patch.object(outcome_scheduler, "_trade_review_marker_path",
                          side_effect=OSError("gone")):
            self.assertIsNone(outcome_scheduler.trade_review_last_run_day())


class RunTests(unittest.TestCase):

    def test_it_reviews_and_reports_what_it_wrote(self):

        rows = [
            {"trading_day": "2026-08-14", "symbol": "NFLX"},
            {"trading_day": "2026-08-14", "symbol": "CRWD"},
            {"trading_day": "2026-08-13", "symbol": "PLTR"},
        ]

        with patch.object(outcome_scheduler, "trade_review_due", return_value=True), \
             patch.object(outcome_scheduler, "_write_marker"), \
             patch("tools.daily_trade_review.review_days", return_value=rows) as review:

            summary = outcome_scheduler.maybe_review_trades(NOW, "IDLE")

        self.assertEqual(summary["reviewed"], 3)
        self.assertEqual(summary["days"], ["2026-08-13", "2026-08-14"])
        self.assertTrue(review.call_args.kwargs["write"])

    def test_a_day_with_no_trades_is_not_an_error(self):

        with patch.object(outcome_scheduler, "trade_review_due", return_value=True), \
             patch.object(outcome_scheduler, "_write_marker"), \
             patch("tools.daily_trade_review.review_days", return_value=[]):

            summary = outcome_scheduler.maybe_review_trades(NOW, "IDLE")

        self.assertEqual(summary["reviewed"], 0)

    def test_a_failure_returns_none_rather_than_raising(self):
        """A stopped scanner is worse than a missing diagnostic."""

        with patch.object(outcome_scheduler, "trade_review_due", return_value=True), \
             patch("tools.daily_trade_review.review_days",
                   side_effect=RuntimeError("no bars")):

            self.assertIsNone(outcome_scheduler.maybe_review_trades(NOW, "IDLE"))

    def test_skipping_writes_no_marker(self):

        with patch.object(outcome_scheduler, "trade_review_due", return_value=False), \
             patch.object(outcome_scheduler, "_write_marker") as marker:

            self.assertIsNone(outcome_scheduler.maybe_review_trades(NOW, "IDLE"))
            marker.assert_not_called()

    def test_it_looks_back_far_enough_to_catch_up_after_an_outage(self):

        self.assertGreaterEqual(outcome_scheduler.TRADE_REVIEW_LOOKBACK_DAYS, 2)


class DerivationTests(unittest.TestCase):
    """The two bugs that motivated storing this instead of re-deriving it."""

    def test_placement_is_clamped_to_a_percentage(self):
        """An entry outside the prior range scored past 100 and averaged 287%."""

        import inspect

        from tools import daily_trade_review

        source = inspect.getsource(daily_trade_review.review)
        self.assertIn("min(100.0", source)
        self.assertIn("max(0.0", source)

    def test_the_counterfactual_walks_bars_rather_than_taking_extremes(self):
        """Whichever level is reached first ends the trade."""

        import inspect

        from tools import daily_trade_review

        source = inspect.getsource(daily_trade_review.review)

        self.assertIn("for _ts, bar in after.iterrows()", source)
        # The stop is tested before the target inside the loop, so a bar that
        # touches both scores the stop.
        self.assertLess(source.index("touched_stop:"), source.index("touched_target:"))


if __name__ == "__main__":
    unittest.main()
