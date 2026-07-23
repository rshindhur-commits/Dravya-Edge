import unittest
import pandas as pd
from app.analytics.learning_engine import build_feedback_loop

class FeedbackLoopTests(unittest.TestCase):
    def test_builds_refresh_calibration_roi_and_promotion(self):
        result = build_feedback_loop(pd.DataFrame([{"trade_quality_score": 95, "winner": True, "rule_evaluation": "RR"}, {"trade_quality_score": 75, "winner": False, "rule_evaluation": "RR"}]), pd.DataFrame([{"outcome": "LIVE_QUOTE"}, {"outcome": "STALE_QUOTE"}]), evidence_days=20, completed_trades=80)
        self.assertEqual(result["refresh_success_rate_last_50"], 50.0)
        self.assertEqual(result["rule_roi"][0]["roi"], 0)
        self.assertEqual(result["feature_promotion"][0]["recommended_action"], "PROMOTION_CANDIDATE")