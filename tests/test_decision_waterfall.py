import json
import unittest

from app.analytics.decision_waterfall import (
    build_decision_waterfall,
    build_v1_v2_waterfall_comparison,
    summarize_blocking_stages,
    waterfall_rule_records,
)


class DecisionWaterfallTests(unittest.TestCase):

    def test_merges_entry_conditions_and_first_failed_rule(self):

        result = build_decision_waterfall({
            "Symbol": "NVDA",
            "Scan ID": "scan-1",
            "Final Signal": "BULLISH",
            "Entry": "EMA_PULLBACK",
            "Action Status": "WAIT",
            "Setup %": 82,
            "Candidate RR": 1.4,
            "Option Quality Score": 75,
            "Option Spread %": 4,
            "Option Quote Freshness": "LIVE_QUOTE",
            "Affordable": True,
            "ENTRY_DIAGNOSTICS_JSON": json.dumps({
                "candidate_setup": "EMA_PULLBACK",
                "setups": [{
                    "setup": "EMA_PULLBACK",
                    "conditions": [
                        {
                            "name": "BULLISH_SIGNAL",
                            "actual": "BULLISH",
                            "required": "BULLISH",
                            "passed": True,
                        },
                        {
                            "name": "PULLBACK_TO_EMA9",
                            "actual": 102,
                            "required": "within EMA9",
                            "passed": False,
                        },
                    ],
                }],
            }),
        })

        self.assertEqual(result["symbol"], "NVDA")
        self.assertEqual(result["final_action"], "WAIT")
        self.assertEqual(result["blocking_stage"], "Entry")
        self.assertEqual(result["blocking_rule"], "PULLBACK_TO_EMA9")
        self.assertEqual(result["first_blocker"]["rule"], "PULLBACK_TO_EMA9")
        self.assertEqual(result["first_blocker"]["actual"], 102)
        self.assertEqual(
            [stage["stage"] for stage in result["stages"]],
            [
                "Momentum", "Entry", "Risk", "Option",
                "Affordability", "Realtime", "Telegram", "Paper",
                "Decision",
            ],
        )
        risk = next(stage for stage in result["stages"] if stage["stage"] == "Risk")
        self.assertTrue(any(
            rule["rule"] == "RR" and rule["actual"] == 1.4
            for rule in risk["rules"]
        ))

    def test_blocking_summary_uses_first_blocking_stage(self):

        summary = summarize_blocking_stages([
            {"blocking_stage": "Risk"},
            {"blocking_stage": "Risk"},
            {"blocking_stage": "Option"},
            {"blocking_stage": None},
        ])

        self.assertEqual(summary["total_candidates"], 4)
        self.assertEqual(summary["total_blocked"], 3)
        self.assertEqual(summary["stages"][0]["stage"], "Risk")
        self.assertEqual(summary["stages"][0]["percentage"], 66.7)

    def test_rule_records_flatten_evaluated_rules_and_blocker(self):

        waterfall = build_decision_waterfall({
            "Symbol": "NVDA",
            "Final Signal": "NEUTRAL",
            "Entry": "NO_ENTRY",
            "Action Status": "WAIT",
        }, scan_id="scan-1")
        records = waterfall_rule_records(waterfall, "scan-1")

        self.assertTrue(records)
        self.assertTrue(any(record["blocking"] for record in records))
        self.assertTrue(all(record["scan_id"] == "scan-1" for record in records))

    def test_v1_v2_comparison_identifies_entry_disagreement(self):

        comparison = build_v1_v2_waterfall_comparison({
            "Symbol": "NVDA",
            "Final Signal": "BULLISH",
            "Entry": "NO_ENTRY",
            "Action Status": "WAIT",
            "V2 Entry Suggested": True,
            "V2 Entry Reason": "FIRST_PULLBACK_EFFICIENT",
        })

        self.assertTrue(comparison["actions_disagree"])
        self.assertEqual(comparison["first_disagreement"], "Entry")


if __name__ == "__main__":

    unittest.main()