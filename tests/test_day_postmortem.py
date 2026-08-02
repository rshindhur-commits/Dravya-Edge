"""The postmortem page exists because a failed read was rendered as a quiet day.

On 2026-08-01 a container that could not reach Postgres told subscribers "No
positions were closed this week" over a week with seven, and re-sent it on every
restart. Working out why took a day of hand-written SQL against tables that
already held the answer.

So the rule these tests hold is the one the incident broke: `None` means the
question could not be asked and `[]` means the answer was nothing, and no code
path may collapse them.
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.analytics import day_postmortem
from app.db.day_postmortem_repository import DayPostmortemRepository


def _at(hour, minute=0):
    return datetime(2026, 7, 31, hour, minute, tzinfo=timezone.utc)


class CoverageTests(unittest.TestCase):

    def test_an_unreadable_scan_table_is_none_not_zero_scans(self):

        self.assertIsNone(day_postmortem.scan_coverage(None))
        self.assertIsNone(day_postmortem.coverage_gaps(None))

    def test_a_day_with_no_scans_reports_zero(self):
        """Genuinely no scans is a real answer and must render as one."""

        coverage = day_postmortem.scan_coverage([])

        self.assertEqual(coverage["scans"], 0)
        self.assertIsNone(coverage["first_at"])

    def test_started_is_incomplete_not_failed(self):
        """A run writes STARTED then FINISHED. Counting STARTED as a failure
        reported 120 scans and 79 failures for a day that had neither."""

        coverage = day_postmortem.scan_coverage([
            {"status": "FINISHED", "started_at": _at(10), "duration_sec": 20},
            {"status": "STARTED", "started_at": _at(11)},
            {"status": "CRASHED", "started_at": _at(12)},
        ])

        self.assertEqual(coverage["incomplete"], 1)
        self.assertEqual(coverage["failures"], 1)

    def test_times_are_reported_in_et(self):

        coverage = day_postmortem.scan_coverage(
            [{"status": "FINISHED", "started_at": _at(14, 30)}])

        self.assertEqual(coverage["first_at"].strftime("%H:%M"), "10:30")


class GapTests(unittest.TestCase):

    def test_gaps_below_the_threshold_are_cadence_not_incidents(self):

        gaps = day_postmortem.coverage_gaps([
            {"started_at": _at(10, 0)},
            {"started_at": _at(10, 5)},
            {"started_at": _at(10, 10)},
        ])

        self.assertEqual(gaps, [])

    def test_a_real_gap_is_found_and_measured(self):

        gaps = day_postmortem.coverage_gaps([
            {"started_at": _at(10, 0)},
            {"started_at": _at(11, 30)},
        ])

        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["minutes"], 90.0)

    def test_gaps_are_ordered_worst_first(self):

        gaps = day_postmortem.coverage_gaps([
            {"started_at": _at(10, 0)},
            {"started_at": _at(10, 30)},
            {"started_at": _at(13, 0)},
        ])

        self.assertEqual([gap["minutes"] for gap in gaps], [150.0, 30.0])

    def test_unordered_input_is_sorted_before_measuring(self):
        """Rows arrive ordered today, but a gap computed off unsorted timestamps
        would be silently negative rather than obviously wrong."""

        gaps = day_postmortem.coverage_gaps([
            {"started_at": _at(13, 0)},
            {"started_at": _at(10, 0)},
        ])

        self.assertEqual(gaps[0]["minutes"], 180.0)


class DeliveryTests(unittest.TestCase):

    def test_unreadable_alerts_are_none(self):

        self.assertIsNone(day_postmortem.alert_delivery(None))

    def test_attempted_but_unconfirmed_is_counted_as_undelivered(self):
        """Not a failure and not a success. Counting it as either is how a stuck
        queue reads as a healthy one."""

        delivery = day_postmortem.alert_delivery([
            {"status": "ATTEMPTED", "dispatches": 5, "delivered": 0},
            {"status": "DELIVERED", "dispatches": 3, "delivered": 3},
            {"status": "FAILED", "dispatches": 2, "delivered": 0},
        ])

        self.assertEqual(delivery["delivered"], 3)
        self.assertEqual(delivery["failed"], 2)
        self.assertEqual(delivery["undelivered"], 5)


class OutcomeTests(unittest.TestCase):

    def test_unreadable_trades_are_none(self):

        self.assertIsNone(day_postmortem.trade_outcome(None))

    def test_only_closed_trades_with_an_r_multiple_are_scored(self):

        outcome = day_postmortem.trade_outcome([
            {"closed_at": _at(15), "r_multiple": 1.5},
            {"closed_at": _at(15), "r_multiple": -1.0},
            {"closed_at": None, "r_multiple": None},
        ])

        self.assertEqual(outcome["closed"], 2)
        self.assertEqual(outcome["wins"], 1)
        self.assertEqual(outcome["total_r"], 0.5)

    def test_a_scratch_counts_as_a_loss_not_a_win(self):

        outcome = day_postmortem.trade_outcome(
            [{"closed_at": _at(15), "r_multiple": 0.0}])

        self.assertEqual(outcome["losses"], 1)


class AssemblyTests(unittest.TestCase):

    class _Repository:
        def __init__(self, **sections):
            self._sections = sections

        def __getattr__(self, name):
            return lambda _day, **_kwargs: self._sections.get(name)

    def test_one_failing_section_does_not_take_the_page_down(self):

        class Exploding(self._Repository):
            def trades(self, _day):
                raise RuntimeError("connection reset")

        report = day_postmortem.build_day_postmortem(
            "2026-07-31", repository=Exploding(scans=[]))

        self.assertIsNone(report["trades"])
        self.assertEqual(report["scans"], [])

    def test_every_section_is_present_even_when_unreadable(self):

        report = day_postmortem.build_day_postmortem(
            "2026-07-31", repository=self._Repository())

        for section in ("scans", "alerts", "trades", "entry_decisions",
                        "blocking_rules", "alert_suppressions", "coverage", "gaps"):
            self.assertIn(section, report)


class RepositoryTests(unittest.TestCase):

    def test_a_failed_read_returns_none_from_every_method(self):

        with patch.object(DayPostmortemRepository, "_fetch_optional",
                          return_value=None):

            repository = DayPostmortemRepository()

            for name in ("scans", "alerts", "trades", "entry_decisions",
                         "blocking_rules", "alert_suppressions"):
                self.assertIsNone(getattr(repository, name)("2026-07-31"), name)


class TradingDayPickerTests(unittest.TestCase):

    def test_weekends_are_skipped(self):

        days = day_postmortem.previous_trading_days(
            3, reference=datetime(2026, 8, 3).date())

        self.assertEqual([day.isoformat() for day in days],
                         ["2026-08-03", "2026-07-31", "2026-07-30"])


if __name__ == "__main__":
    unittest.main()
