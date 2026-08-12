import unittest
import pandas as pd
from app.analytics.learning_engine import build_feedback_loop

class FeedbackLoopTests(unittest.TestCase):
    def test_builds_refresh_calibration_roi_and_promotion(self):
        result = build_feedback_loop(pd.DataFrame([{"trade_quality_score": 95, "winner": True, "rule_evaluation": "RR"}, {"trade_quality_score": 75, "winner": False, "rule_evaluation": "RR"}]), pd.DataFrame([{"outcome": "LIVE_QUOTE"}, {"outcome": "STALE_QUOTE"}]), evidence_days=20, completed_trades=80)
        self.assertEqual(result["refresh_success_rate_last_50"], 50.0)
        # One row is a known winner; the other carries `winner: False` with no
        # resolution recorded, which means unknown, not "lost". Only the first
        # is scored, so the rule reads -1: it blocked one winner and no
        # measured loser. This asserted 0 until 2026-08-12, which required
        # counting the unresolved row as a prevented loss -- the same defect
        # that had LOW_RR ranked best on 1,000 rows it had never resolved.
        self.assertEqual(result["rule_roi"][0]["roi"], -1)
        self.assertEqual(result["rule_roi"][0]["resolved"], 1)
        self.assertEqual(result["rule_roi"][0]["winners_blocked"], 1)
        self.assertEqual(result["feature_promotion"][0]["status"], "SHADOW")

    def test_a_rule_that_fires_before_a_thesis_is_not_scored(self):
        """A gate rejecting a candidate with no side chosen has nothing to replay.

        Its candidates carry no direction, entry, stop or target, so "would it
        have won" is undefined rather than pending. Reporting roi from the block
        count alone is what made the earliest gates look flawless.
        """

        result = build_feedback_loop(
            pd.DataFrame([
                {"direction": "NONE", "winner": False, "rule_evaluation": "NO_DIRECTIONAL_EDGE"},
                {"direction": "NONE", "winner": False, "rule_evaluation": "NO_DIRECTIONAL_EDGE"},
            ]),
            pd.DataFrame(),
        )
        row = result["rule_roi"][0]

        self.assertFalse(row["scoreable"])
        self.assertIsNone(row["roi"])
        self.assertEqual(row["directional"], 0)

    def test_a_directional_rule_is_scored_on_resolved_rows_only(self):

        result = build_feedback_loop(
            pd.DataFrame([
                {"direction": "CALL", "winner": True, "target_first": True, "stop_first": False, "rule_evaluation": "OPTION_REJECTED"},
                {"direction": "PUT", "winner": False, "target_first": False, "stop_first": True, "rule_evaluation": "OPTION_REJECTED"},
                {"direction": "PUT", "winner": False, "target_first": False, "stop_first": False, "rule_evaluation": "OPTION_REJECTED"},
            ]),
            pd.DataFrame(),
        )
        row = result["rule_roi"][0]

        self.assertTrue(row["scoreable"])
        self.assertEqual(row["candidates"], 3)
        self.assertEqual(row["resolved"], 2, "the unresolved row must not vote")
        self.assertEqual(row["winners_blocked"], 1)
        self.assertEqual(row["roi"], 0)