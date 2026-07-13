import unittest

import pandas as pd

from app.diagnostics import (
    build_entry_diagnostics,
    build_entry_diagnostics_from_snapshot,
    classify_entry_gate_failure_stage,
    diagnostics_to_json,
    summarize_entry_diagnostics,
)
from tools.replay_today import build_replay_summary, replay_coverage


class EntryDiagnosticsTests(unittest.TestCase):

    def test_records_closest_candidate_for_non_entry(self):

        df = pd.DataFrame(
            [
                {
                    "Open": 100,
                    "High": 101,
                    "Low": 99,
                    "Close": 100,
                    "VWAP": 99,
                    "EMA9": 100,
                    "EMA20": 99,
                    "ATR": 4,
                    "REL_VOLUME": 1.0,
                    "BODY_STRENGTH": 0.4,
                    "BREAKDOWN": False,
                    "LOWER_HIGH": False,
                },
                {
                    "Open": 100,
                    "High": 102,
                    "Low": 100.2,
                    "Close": 101.5,
                    "VWAP": 100,
                    "EMA9": 101,
                    "EMA20": 99,
                    "ATR": 4,
                    "REL_VOLUME": 0.9,
                    "BODY_STRENGTH": 0.45,
                    "BREAKDOWN": False,
                    "LOWER_HIGH": False,
                },
            ]
        )

        diagnostics = build_entry_diagnostics(
            "TEST",
            df,
            {"signal": "BULLISH", "score": 82},
            market_regime="TRENDING_BULL",
            selected_entry={"entry_type": "NO_ENTRY"},
        )

        self.assertTrue(diagnostics["candidate_setup"])
        self.assertGreater(diagnostics["readiness"], 0)
        self.assertIn("failed_conditions", diagnostics)

    def test_summary_reads_persisted_json_payload(self):

        diagnostics = {
            "candidate_setup": "BREAKDOWN_SHORT",
            "failed_conditions": ["BODY_STRENGTH", "REL_VOLUME"],
            "market_regime": "TRENDING_BEAR",
        }
        summary = summarize_entry_diagnostics(
            [
                {
                    "ENTRY_DIAGNOSTICS_JSON": diagnostics_to_json(diagnostics),
                    "Action Status": "WAIT",
                    "Market Regime": "TRENDING_BEAR",
                }
            ]
        )

        self.assertEqual(summary["failure_counts"]["BODY_STRENGTH"], 1)
        self.assertEqual(
            summary["regime_summary"]["TRENDING_BEAR"]["top_failure"],
            "BODY_STRENGTH",
        )

    def test_replays_from_persisted_indicator_snapshot(self):

        diagnostics = build_entry_diagnostics_from_snapshot(
            {
                "Symbol": "NVDA",
                "Final Signal": "HIGH CONVICTION BEARISH",
                "15m Score": -88,
                "Market Regime": "TRENDING_BEAR",
                "Entry": "BREAKDOWN_SHORT",
                "ENTRY_OPEN": 100,
                "ENTRY_HIGH": 101,
                "ENTRY_LOW": 96,
                "ENTRY_CLOSE": 95,
                "ENTRY_EMA9": 97,
                "ENTRY_EMA20": 99,
                "ENTRY_VWAP": 98,
                "ENTRY_REL_VOLUME": 1.0,
                "ENTRY_BODY_STRENGTH": 0.4,
                "ENTRY_ATR": 3,
                "ENTRY_BREAKDOWN": True,
                "ENTRY_LOWER_HIGH": True,
                "ENTRY_RECENT_LOW": 96,
            }
        )

        self.assertEqual(diagnostics["candidate_setup"], "BREAKDOWN_SHORT")
        self.assertIn("BODY_STRENGTH", diagnostics["failed_conditions"])
        self.assertIn("REL_VOLUME", diagnostics["failed_conditions"])

    def test_classifies_gate_failure_stage(self):

        self.assertEqual(
            classify_entry_gate_failure_stage(
                {
                    "Final Signal": "HIGH CONVICTION BEARISH",
                    "Entry": "BREAKDOWN_SHORT",
                    "Action Status": "AVOID",
                    "Option Rejection Reason": "OPTION_TOO_EXPENSIVE",
                }
            ),
            "Affordability",
        )

    def test_replay_summary_shape_and_coverage(self):

        replay = pd.DataFrame(
            [
                {
                    "Symbol": "NVDA",
                    "ENTRY_SETUP_CANDIDATE": "BREAKDOWN_SHORT",
                    "ENTRY_READINESS": 82,
                    "FAILED_ENTRY_CONDITIONS": "BODY_STRENGTH",
                    "PASSED_ENTRY_CONDITIONS": "VWAP, EMA_ALIGNMENT",
                    "Action Status": "WAIT",
                    "ENTRY_GATE_FAILURE_STAGE": "Entry",
                    "REPLAY_SOURCE": "replayed_snapshot",
                }
            ]
        )
        summary = build_replay_summary(replay)
        coverage = replay_coverage(replay, scanner_rows=1)

        self.assertEqual(
            list(summary.columns),
            [
                "Symbol",
                "Closest Setup",
                "Readiness",
                "Failed Conditions",
                "Passed Conditions",
                "Final Decision",
                "Gate Failure Stage",
                "First Failed Rule",
                "Recommendation",
                "Replay Source",
            ],
        )
        self.assertEqual(summary.iloc[0]["First Failed Rule"], "BODY_STRENGTH")
        self.assertEqual(summary.iloc[0]["Recommendation"], "Wait for stronger candle body")
        self.assertEqual(coverage["missing_indicators"], 0)
        self.assertEqual(coverage["coverage_pct"], 100.0)


if __name__ == "__main__":

    unittest.main()