"""The daily resolution pass must fire once, while idle, and never break the loop.

Resolution is the only measurement that works on a session with no trades, which
is most of them. It was a manual command until 2026-08-12; these cover the guards
that decide when it runs, because the failure mode of getting them wrong is
silent -- either it stops measuring, or it competes with a live scan.
"""

import json
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.runtime import outcome_scheduler

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 12, 17, 30, tzinfo=ET)


class DueTests(unittest.TestCase):

    def test_it_does_not_run_during_a_session(self):
        """A live scan is writing to the same tables. Idle is the only safe window."""

        with patch.object(outcome_scheduler, "last_run_day", return_value=None):
            self.assertFalse(outcome_scheduler.due(NOW, idle_reason_value=None))

    def test_it_runs_when_idle_and_not_yet_run_today(self):

        with patch.object(outcome_scheduler, "last_run_day", return_value="2026-08-11"):
            self.assertTrue(outcome_scheduler.due(NOW, idle_reason_value="IDLE"))

    def test_it_runs_at_most_once_per_et_date(self):
        """The idle branch loops every few minutes all weekend."""

        with patch.object(outcome_scheduler, "last_run_day", return_value="2026-08-12"):
            self.assertFalse(outcome_scheduler.due(NOW, idle_reason_value="IDLE"))

    def test_a_missing_marker_means_it_has_never_run(self):

        with patch.object(outcome_scheduler, "last_run_day", return_value=None):
            self.assertTrue(outcome_scheduler.due(NOW, idle_reason_value="IDLE"))


class RunTests(unittest.TestCase):

    def test_it_resolves_recent_days_and_bridges(self):

        rows = [
            {"target_hit": True, "stop_hit": False},
            {"target_hit": False, "stop_hit": True},
            {"target_hit": False, "stop_hit": False},
        ]

        with patch.object(outcome_scheduler, "due", return_value=True), \
             patch.object(outcome_scheduler, "_days_to_resolve", return_value=["2026-08-11"]), \
             patch.object(outcome_scheduler, "_record_run"), \
             patch("tools.resolve_candidate_outcomes.run_day", return_value=rows) as run_day, \
             patch("tools.resolve_candidate_outcomes.bridge_to_evidence", return_value=2):

            summary = outcome_scheduler.maybe_resolve_outcomes(NOW, "IDLE")

        run_day.assert_called_once_with("2026-08-11", write=True)
        self.assertEqual(summary["target_first"], 1)
        self.assertEqual(summary["stop_first"], 1)
        self.assertEqual(summary["bridged"], 2)
        self.assertEqual(summary["days"]["2026-08-11"]["candidates"], 3)

    def test_a_failure_returns_none_rather_than_raising(self):
        """An unresolved day is recoverable tomorrow. A stopped scanner is not."""

        with patch.object(outcome_scheduler, "due", return_value=True), \
             patch.object(outcome_scheduler, "_days_to_resolve", side_effect=RuntimeError("db down")):

            self.assertIsNone(outcome_scheduler.maybe_resolve_outcomes(NOW, "IDLE"))

    def test_skipping_writes_no_marker(self):
        """A skipped pass must not count as the day's run."""

        with patch.object(outcome_scheduler, "due", return_value=False), \
             patch.object(outcome_scheduler, "_record_run") as record:

            self.assertIsNone(outcome_scheduler.maybe_resolve_outcomes(NOW, "IDLE"))
            record.assert_not_called()


class MarkerTests(unittest.TestCase):

    def test_the_marker_round_trips(self):

        with patch.object(outcome_scheduler, "due", return_value=True), \
             patch.object(outcome_scheduler, "_days_to_resolve", return_value=[]), \
             patch("tools.resolve_candidate_outcomes.bridge_to_evidence", return_value=0):

            outcome_scheduler.maybe_resolve_outcomes(NOW, "IDLE")

        self.assertEqual(outcome_scheduler.last_run_day(), "2026-08-12")

        payload = json.loads(
            outcome_scheduler._marker_path().read_text(encoding="utf-8")
        )
        self.assertEqual(payload["last_run_day"], "2026-08-12")

    def test_an_unreadable_marker_reads_as_never_run(self):
        """Losing the marker costs one extra pass of idempotent work, not a crash."""

        with patch.object(
            outcome_scheduler, "_marker_path", side_effect=OSError("gone")
        ):
            self.assertIsNone(outcome_scheduler.last_run_day())


if __name__ == "__main__":
    unittest.main()
