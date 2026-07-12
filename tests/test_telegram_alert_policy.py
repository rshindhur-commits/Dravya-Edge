import unittest

from app.alerts.telegram_alerts import (
    _paper_policy_allowed,
    _real_review_policy_allowed,
)
from app.decision import evaluate_candidate


class TelegramAlertPolicyTests(unittest.TestCase):

    def test_decision_engine_marks_enter_paper_candidate_unblocked(self):

        decision = evaluate_candidate(
            {
                "Action Status": "ENTER_PAPER",
                "Setup %": 86,
                "Candidate RR": 2.4,
                "Option Quality Score": 72,
                "Entry Alert Score": 86,
                "Realtime Ready": True,
            }
        )

        self.assertEqual(decision.action, "ENTER_PAPER")
        self.assertEqual(decision.score, 86)
        self.assertFalse(decision.blocked)

    def test_paper_policy_accepts_enter_paper_without_real_review_gates(self):

        allowed, reason, decision = _paper_policy_allowed(
            {
                "Action Status": "ENTER_PAPER",
                "Setup %": 86,
                "Candidate RR": 1.8,
                "Option Quality Score": 72,
                "Entry Alert Score": 86,
                "Realtime Ready": True,
            },
            min_score=85,
        )

        self.assertTrue(allowed)
        self.assertEqual(reason, "ELIGIBLE")
        self.assertEqual(decision.action, "ENTER_PAPER")

    def test_real_review_policy_stays_strict(self):

        allowed, reason, decision = _real_review_policy_allowed(
            {
                "Action Status": "ENTER_PAPER",
                "Setup %": 86,
                "Candidate RR": 1.8,
                "Option Quality Score": 72,
                "Option Spread %": 6,
                "Entry Alert Score": 86,
                "Realtime Ready": True,
                "Top Candidate": "BULLISH_TOP_3",
                "Candidate Scan Count": 1,
            }
        )

        self.assertFalse(allowed)
        self.assertIn(
            reason,
            {
                "DECISION_SCORE_BELOW_MIN",
                "RR_BELOW_THRESHOLD",
                "OPTION_QUALITY_BELOW_THRESHOLD",
                "REAL_REVIEW_PERSISTENCE_REQUIRED",
                "REAL_REVIEW_TOP1_REQUIRED",
            }
        )
        self.assertEqual(decision.action, "ENTER_PAPER")


if __name__ == "__main__":

    unittest.main()