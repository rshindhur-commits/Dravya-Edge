"""`paper_trades.opened_at` holds ET as UTC for rows written before 2026-08-03.

The write path was fixed and the existing rows were not backfilled, so the column
is mixed rather than uniformly wrong -- which is worse, because a consistent shift
can be corrected for in a query and a mixed one silently compares ET against UTC
in any filter or join spanning the changeover.

21 of 24 rows are affected, each off by exactly -4.00h, and each carries the
correct value in its own payload.
"""

import unittest

from tools.backfill_trade_timestamps import plan_corrections


class PlanCorrectionsTests(unittest.TestCase):

    def test_a_mislabelled_row_is_corrected_from_its_own_payload(self):
        """id 48 as it stands in production."""

        corrections, unfixable = plan_corrections([{
            "id": 48,
            "opened_at": "2026-07-31T11:36:33.417566+00:00",
            "closed_at": None,
            "payload": {"opened_at_utc": "2026-07-31T15:36:33.417566+00:00"},
        }])

        self.assertEqual(unfixable, [])
        self.assertEqual(len(corrections), 1)

        change = corrections[0]["changes"]["opened_at"]
        self.assertEqual(change["to"], "2026-07-31T15:36:33.417566+00:00")
        self.assertEqual(change["shift_hours"], 4.0)

    def test_an_already_correct_row_is_left_alone(self):
        """id 77. Running this twice must be a no-op."""

        corrections, unfixable = plan_corrections([{
            "id": 77,
            "opened_at": "2026-08-03T15:58:12.648074+00:00",
            "closed_at": None,
            "payload": {"opened_at_utc": "2026-08-03T15:58:12.648074+00:00"},
        }])

        self.assertEqual(corrections, [])
        self.assertEqual(unfixable, [])

    def test_a_row_with_no_payload_truth_is_reported_not_guessed(self):
        """A fixed -4h shift would corrupt any row that was already right."""

        corrections, unfixable = plan_corrections([{
            "id": 3,
            "opened_at": "2026-07-09T19:14:52+00:00",
            "closed_at": None,
            "payload": {},
        }])

        self.assertEqual(corrections, [])
        self.assertEqual(unfixable, [{"id": 3, "columns": ["opened_at"]}])

    def test_both_columns_are_corrected_independently(self):

        corrections, _ = plan_corrections([{
            "id": 44,
            "opened_at": "2026-07-31T10:58:46+00:00",
            "closed_at": "2026-07-31T14:30:37+00:00",
            "payload": {
                "opened_at_utc": "2026-07-31T14:58:46+00:00",
                "closed_at_utc": "2026-07-31T18:30:37+00:00",
            },
        }])

        self.assertEqual(set(corrections[0]["changes"]), {"opened_at", "closed_at"})

    def test_the_offset_is_never_assumed(self):
        """ET is UTC-4 in summer and UTC-5 in winter; the payload decides."""

        corrections, _ = plan_corrections([{
            "id": 1,
            "opened_at": "2026-01-15T10:00:00+00:00",
            "closed_at": None,
            "payload": {"opened_at_utc": "2026-01-15T15:00:00+00:00"},
        }])

        self.assertEqual(corrections[0]["changes"]["opened_at"]["shift_hours"], 5.0)

    def test_sub_minute_skew_is_not_treated_as_mislabelling(self):
        """Whole hours are the bug; anything smaller is a different problem."""

        corrections, _ = plan_corrections([{
            "id": 9,
            "opened_at": "2026-07-17T14:59:45+00:00",
            "closed_at": None,
            "payload": {"opened_at_utc": "2026-07-17T14:59:52+00:00"},
        }])

        self.assertEqual(corrections, [])

    def test_a_payload_arriving_as_json_text_is_parsed(self):

        corrections, _ = plan_corrections([{
            "id": 48,
            "opened_at": "2026-07-31T11:36:33+00:00",
            "closed_at": None,
            "payload": '{"opened_at_utc": "2026-07-31T15:36:33+00:00"}',
        }])

        self.assertEqual(len(corrections), 1)


if __name__ == "__main__":
    unittest.main()
