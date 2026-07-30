"""Candidate artifacts must record holding profile and suggestion lifecycle.

Both gaps blocked the 2026-07-29 intraday audit:

* `candidate_evidence.holding_profile` was NULL on all 295 rows, because the profile
  was only derived when a paper trade opened. "How many intraday candidates were
  generated, and where did they die?" was unanswerable.
* `suggested_trade_state.json` held zero entries for the day, because the suggestion
  sync only ran from a dashboard render. Nothing recorded a candidate expiring
  unentered, so trigger-window expiry could not be measured.
"""

import unittest
from unittest.mock import patch

import pandas as pd

from app.analytics.candidate_evidence import EVIDENCE_COLUMNS
from app.main import _add_holding_profiles
from app.runtime.paper_position_lifecycle import (
    _suggestion_candidate_rows,
    sync_scan_suggestions,
)


def _candidate(symbol, **extra):
    row = {
        "Symbol": symbol,
        "Candidate Direction": "PUT",
        "Setup Valid": True,
        "Action Status": "ENTER_PAPER",
        "Entry": "VWAP_REJECTION",
        "Candidate Entry Price": 100.0,
        "Candidate Stop Price": 102.0,
        "Candidate Target Price": 96.0,
    }
    row.update(extra)
    return row


class HoldingProfileStampTests(unittest.TestCase):

    def test_every_row_gets_a_profile(self):

        df = _add_holding_profiles(pd.DataFrame([
            _candidate("NVDA"),
            _candidate("NFLX", **{"Action Status": "WAIT"}),
        ]))

        self.assertIn("Holding Profile", df.columns)
        self.assertEqual(len(df["Holding Profile"].dropna()), 2)
        for value in df["Holding Profile"]:
            self.assertIn(value, {"INTRADAY", "MULTIDAY"})

    def test_multiday_setups_are_labelled_multiday(self):

        df = _add_holding_profiles(pd.DataFrame([
            _candidate("NVDA", **{
                "Expiration Bucket": "PREFERRED_14_30",
                "Setup %": 85,
                "Candidate RR": 2.0,
                "Option Quality Score": 80,
            }),
        ]))

        self.assertEqual(df["Holding Profile"].iloc[0], "MULTIDAY")

    def test_plain_candidates_default_to_intraday(self):

        df = _add_holding_profiles(pd.DataFrame([_candidate("NVDA")]))
        self.assertEqual(df["Holding Profile"].iloc[0], "INTRADAY")

    def test_empty_frame_is_tolerated(self):

        self.assertTrue(_add_holding_profiles(pd.DataFrame()).empty)
        self.assertIsNone(_add_holding_profiles(None))

    def test_evidence_carries_the_profile_column(self):

        self.assertIn("holding_profile", EVIDENCE_COLUMNS)


class SuggestionSyncTests(unittest.TestCase):

    def test_actionable_candidates_are_selected(self):

        rows = _suggestion_candidate_rows(pd.DataFrame([
            _candidate("NVDA"),
            _candidate("NFLX", **{"Action Status": "WAIT"}),
            _candidate("ORCL", **{"Setup Valid": False}),
            _candidate("AMD", **{"Candidate Direction": "NONE"}),
        ]))

        self.assertEqual([str(row.get("Symbol")) for row in rows], ["NVDA"])

    def test_missing_columns_yield_no_rows(self):

        self.assertEqual(_suggestion_candidate_rows(pd.DataFrame([{"Symbol": "NVDA"}])), [])
        self.assertEqual(_suggestion_candidate_rows(pd.DataFrame()), [])
        self.assertEqual(_suggestion_candidate_rows(None), [])

    def test_sync_runs_from_the_scanner(self):

        df = pd.DataFrame([_candidate("NVDA")])

        with patch(
            "app.state.suggested_trade_manager.sync_suggestions_from_scan"
        ) as sync, patch(
            "app.state.suggested_trade_manager.cleanup_old_suggestions"
        ) as cleanup:

            result = sync_scan_suggestions(df)

        sync.assert_called_once()
        cleanup.assert_called_once()
        self.assertEqual(result["synced"], 1)
        self.assertIsNone(result["error"])

    def test_a_sync_failure_does_not_break_the_scan(self):

        df = pd.DataFrame([_candidate("NVDA")])

        with patch(
            "app.state.suggested_trade_manager.sync_suggestions_from_scan",
            side_effect=RuntimeError("state locked"),
        ):
            result = sync_scan_suggestions(df)

        self.assertEqual(result["synced"], 0)
        self.assertIn("state locked", result["error"])


if __name__ == "__main__":
    unittest.main()
