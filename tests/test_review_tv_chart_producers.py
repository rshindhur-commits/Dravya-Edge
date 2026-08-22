"""Only the spread producer of REVIEW_TV_CHART may be booked.

Three different things stamp a row REVIEW_TV_CHART and `ALLOW_REVIEW_TV_CHART_AUTO_PAPER`
was a single switch over all of them:

  1. `realtime_confirmation_needed` -- Polygon data is delayed. No confirmed live
     price stands behind the row.
  2. `_align_action_status_with_entry_gate` -- the scanner gate refused the row on
     RR, setup or quality and downgraded it, setting `Realtime Ready` False.
  3. A tolerated spread -- a fully qualified signal carrying a real contract with
     a live bid and ask.

Only 3 is the experiment. Turning the switch on for it would also have booked 1
and 2, and 2 is the worse of the two: the review branch is exactly the one that
stops checking `Realtime Ready`, and the paper gate's RR floor (1.8) sits below
the scanner's (2.0), so gate-downgraded rows in the 1.8-2.0 band would have been
opened -- a band that measures badly.
"""

import unittest
from unittest.mock import patch

import pandas as pd

from app.runtime.paper_automation_support import (
    _is_spread_tolerated_review,
    _paper_candidate_filter_reason,
)


def _row(**overrides):

    base = {
        "Symbol": "AMD",
        "Setup Valid": True,
        "Candidate Direction": "CALL",
        "Candidate Entry Price": 170.0,
        "Candidate Stop Price": 168.0,
        "Candidate Target Price": 174.0,
        "Candidate RR": 2.0,
        "Entry": "PULLBACK_TO_EMA9",
        "Action Status": "REVIEW_TV_CHART",
        "Next Condition": "-",
        "Live Chart Checklist": "-",
        "Realtime Ready": False,
        "Affordable": True,
        "Option Spread Tolerated": False,
    }
    base.update(overrides)

    return pd.Series(base)


class SpreadToleratedFlagTests(unittest.TestCase):

    def test_the_flag_is_read_from_the_column(self):

        self.assertTrue(_is_spread_tolerated_review(_row(**{
            "Option Spread Tolerated": True
        })))
        self.assertFalse(_is_spread_tolerated_review(_row()))

    def test_a_row_without_the_column_is_not_tolerated(self):
        """Archived frames predate the column; absent must mean no."""

        row = _row()
        self.assertFalse(_is_spread_tolerated_review(row.drop(
            "Option Spread Tolerated"
        )))


class ReviewAdmissionTests(unittest.TestCase):

    def _reason(self, row):

        with patch(
            "app.runtime.paper_automation_support._allow_review_tv_chart_auto_paper",
            return_value=True
        ):
            return _paper_candidate_filter_reason(row)

    def test_the_spread_case_is_admitted(self):

        self.assertIsNone(self._reason(_row(**{
            "Option Spread Tolerated": True
        })))

    def test_a_delayed_data_review_row_is_refused(self):

        self.assertEqual(
            self._reason(_row(**{
                "Action Reason": "Polygon data delayed; confirm live chart",
            })),
            "REVIEW_NOT_SPREAD_TOLERATED",
        )

    def test_a_gate_downgraded_review_row_is_refused(self):
        """The one that would have opened trades the scanner gate had refused."""

        self.assertEqual(
            self._reason(_row(**{
                "Candidate RR": 1.85,
                "Action Reason": "RR_BELOW_THRESHOLD",
                "Blocked By": "RR_BELOW_THRESHOLD",
            })),
            "REVIEW_NOT_SPREAD_TOLERATED",
        )

    def test_the_switch_still_governs_everything(self):

        with patch(
            "app.runtime.paper_automation_support._allow_review_tv_chart_auto_paper",
            return_value=False
        ):
            self.assertEqual(
                _paper_candidate_filter_reason(_row(**{
                    "Option Spread Tolerated": True
                })),
                "REVIEW_VALIDATION_DISABLED",
            )

    def test_ordinary_entries_are_untouched(self):

        self.assertIsNone(self._reason(_row(**{
            "Action Status": "ENTER_PAPER",
            "Realtime Ready": True,
        })))

    def test_the_realtime_bypass_belongs_only_to_the_spread_case(self):
        """`Realtime Ready` False is how a gate-downgraded row is marked."""

        import inspect

        from app.runtime import paper_automation_support

        source = inspect.getsource(
            paper_automation_support._paper_candidate_filter_reason
        )
        bypass = source[source.index("review_ready ="):source.index("Realtime Ready")]

        self.assertIn("_is_spread_tolerated_review", bypass)


class ScannerStampsTheColumnTests(unittest.TestCase):
    """A column nothing writes is a gate nothing opens."""

    def test_main_sets_it_beside_the_tolerated_contract(self):

        import inspect

        from app import main

        source = inspect.getsource(main)

        self.assertIn('"Option Spread Tolerated": option_spread_tolerated', source)
        self.assertIn("option_spread_tolerated = True", source)
        self.assertIn("option_spread_tolerated = False", source)


if __name__ == "__main__":

    unittest.main()
