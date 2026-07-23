import unittest

from app.exit.exit_confidence import evaluate_exit_confidence


class ExitConfidenceTests(unittest.TestCase):

    def test_healthy_single_ema_loss_is_grace_eligible(self):

        result = evaluate_exit_confidence(
            {
                "Close": 99,
                "EMA9": 100,
                "ATR": 2,
                "MACD": 1,
                "MACD_SIGNAL": 0.5,
                "REL_VOLUME": 1.2,
            },
            {
                "score": 88,
                "ema_lost": True,
                "vwap_lost": False,
                "trend_failure_confirmed": False,
            },
            current_r=1.2,
            mfe_r=1.8,
            bars_in_trade=4,
            is_short=False,
        )

        self.assertEqual(result["exit_health_state"], "HEALTHY")
        self.assertEqual(result["soft_confirmations"], ["EMA_LOST"])
        self.assertLess(result["exit_confidence_score"], 50)
        self.assertTrue(result["grace_zone_eligible"])

    def test_stacked_deterioration_increases_exit_confidence(self):

        result = evaluate_exit_confidence(
            {
                "Close": 95,
                "EMA9": 100,
                "ATR": 2,
                "MACD": -1,
                "MACD_SIGNAL": 0,
                "REL_VOLUME": 0.5,
            },
            {
                "score": 42,
                "ema_lost": True,
                "vwap_lost": True,
                "trend_failure_confirmed": True,
            },
            current_r=0.2,
            mfe_r=2,
            bars_in_trade=15,
            is_short=False,
        )

        self.assertEqual(result["exit_health_state"], "FAILED")
        self.assertGreater(result["exit_confidence_score"], 70)
        self.assertGreaterEqual(result["soft_confirmation_count"], 3)


if __name__ == "__main__":

    unittest.main()