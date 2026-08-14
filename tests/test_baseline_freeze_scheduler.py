"""Freezing a regression baseline has to be automatic, because the data expires.

`scanner_snapshot` is pruned on a rolling window; a frozen baseline is not. So a
session never frozen while its snapshots are alive can never be regressed
afterwards, and nothing announces the loss -- the table simply holds fewer old
days each morning.

That is not hypothetical. `freeze_baseline` gated on a local folder while its
loader read Postgres, so nothing froze between 2026-07-31 and 2026-08-13 and
every day that expired in that fortnight went unrecoverable.

These cover the guards, matching the resolution and option-leg jobs: idle only,
once per ET date, its own marker, and never raising into the scan loop.
"""

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.runtime import outcome_scheduler

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 13, 17, 30, tzinfo=ET)


class DueTests(unittest.TestCase):

    def test_it_does_not_run_during_a_session(self):
        """A live scan is writing the very rows this reads."""

        with patch.object(outcome_scheduler, "baseline_last_run_day",
                          return_value=None):
            self.assertFalse(
                outcome_scheduler.baseline_due(NOW, idle_reason_value=None)
            )

    def test_it_runs_once_per_et_date_when_idle(self):

        with patch.object(outcome_scheduler, "baseline_last_run_day",
                          return_value="2026-08-12"):
            self.assertTrue(outcome_scheduler.baseline_due(NOW, "IDLE"))

        with patch.object(outcome_scheduler, "baseline_last_run_day",
                          return_value="2026-08-13"):
            self.assertFalse(outcome_scheduler.baseline_due(NOW, "IDLE"))

    def test_its_marker_is_independent(self):
        """A database failure here must not make resolution look unrun."""

        paths = {
            outcome_scheduler._marker_path(),
            outcome_scheduler._option_leg_marker_path(),
            outcome_scheduler._baseline_marker_path(),
        }
        self.assertEqual(len(paths), 3, "markers must not collide")

    def test_an_unreadable_marker_reads_as_never_run(self):

        with patch.object(outcome_scheduler, "_baseline_marker_path",
                          side_effect=OSError("gone")):
            self.assertIsNone(outcome_scheduler.baseline_last_run_day())


class RunTests(unittest.TestCase):

    def test_it_freezes_each_recent_session(self):

        with patch.object(outcome_scheduler, "baseline_due", return_value=True), \
             patch.object(outcome_scheduler, "_days_to_resolve",
                          return_value=["2026-08-13", "2026-08-12"]), \
             patch.object(outcome_scheduler, "_write_marker"), \
             patch("app.regression.historical_scanner.freeze_baseline",
                   return_value="/tmp/baseline_trades.csv") as freeze:

            summary = outcome_scheduler.maybe_freeze_regression_baselines(
                NOW, "IDLE"
            )

        self.assertEqual(freeze.call_count, 2)
        self.assertEqual(summary["days"], 2)

    def test_a_day_with_no_entries_is_skipped_not_failed(self):
        """freeze_baseline returns None for a day that never traded."""

        with patch.object(outcome_scheduler, "baseline_due", return_value=True), \
             patch.object(outcome_scheduler, "_days_to_resolve",
                          return_value=["2026-08-11"]), \
             patch.object(outcome_scheduler, "_write_marker"), \
             patch("app.regression.historical_scanner.freeze_baseline",
                   return_value=None):

            summary = outcome_scheduler.maybe_freeze_regression_baselines(
                NOW, "IDLE"
            )

        self.assertEqual(summary["days"], 0)
        self.assertIn("2026-08-11", summary["skipped"])

    def test_one_bad_day_does_not_stop_the_others(self):

        def flaky(day):
            if day == "2026-08-12":
                raise RuntimeError("db hiccup")
            return "/tmp/baseline_trades.csv"

        with patch.object(outcome_scheduler, "baseline_due", return_value=True), \
             patch.object(outcome_scheduler, "_days_to_resolve",
                          return_value=["2026-08-13", "2026-08-12", "2026-08-10"]), \
             patch.object(outcome_scheduler, "_write_marker"), \
             patch("app.regression.historical_scanner.freeze_baseline",
                   side_effect=flaky):

            summary = outcome_scheduler.maybe_freeze_regression_baselines(
                NOW, "IDLE"
            )

        self.assertEqual(summary["days"], 2)
        self.assertIn("2026-08-12", summary["skipped"])

    def test_a_failure_returns_none_rather_than_raising(self):
        """A stopped scanner is worse than a late baseline."""

        with patch.object(outcome_scheduler, "baseline_due", return_value=True), \
             patch.object(outcome_scheduler, "_days_to_resolve",
                          side_effect=RuntimeError("db down")):

            self.assertIsNone(
                outcome_scheduler.maybe_freeze_regression_baselines(NOW, "IDLE")
            )

    def test_skipping_writes_no_marker(self):

        with patch.object(outcome_scheduler, "baseline_due", return_value=False), \
             patch.object(outcome_scheduler, "_write_marker") as marker:

            self.assertIsNone(
                outcome_scheduler.maybe_freeze_regression_baselines(NOW, "IDLE")
            )
            marker.assert_not_called()


class RetentionTests(unittest.TestCase):
    """The window the freeze job exists to outlast."""

    def test_measurement_tables_outlive_the_firehose(self):

        from app.db.retention import RETENTION_RULES

        keep = {rule.table: rule.keep_days for rule in RETENTION_RULES}

        for table in ("scanner_snapshot", "candidate_evidence",
                      "candidate_outcome"):
            self.assertGreaterEqual(
                keep[table], 90,
                f"{table} feeds every measurement and must not be pruned at 21"
            )

        self.assertLess(
            keep["activity_trace_event"], keep["scanner_snapshot"],
            "the trace firehose is a debugging aid, not a record"
        )


if __name__ == "__main__":
    unittest.main()
