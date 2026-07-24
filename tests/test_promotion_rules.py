import unittest
from app.promotion.promotion_rules import evaluate_promotion
from app.promotion.promotion_manager import review_status

class PromotionRulesTests(unittest.TestCase):
    def test_requires_sample_confidence_and_positive_lift(self):
        self.assertEqual(evaluate_promotion(99, 5, 99)[0], "SHADOW")
        self.assertEqual(evaluate_promotion(100, 5, 94)[0], "SHADOW")
        self.assertEqual(evaluate_promotion(100, 0, 95)[0], "RETIRED")
        self.assertEqual(evaluate_promotion(100, 5, 95)[0], "PROMOTION_CANDIDATE")
        self.assertFalse(review_status("Entry Timing", "APPROVED_FOR_CONTROLLED_VALIDATION", "operator")["automatic_v1_change"])