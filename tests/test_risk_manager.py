import unittest

import pandas as pd

from app.risk.risk_manager import calculate_risk


class RiskManagerTests(unittest.TestCase):

    def test_ema_pullback_does_not_force_full_atr_stop_floor(self):

        df = pd.DataFrame([
            {
                "High": 101.25,
                "Low": 99.55,
                "Close": 100.00,
                "ATR": 2.00,
                "EMA9": 99.80,
                "VWAP": 99.40,
                "ROLLING_RESISTANCE": 104.00,
                "ROLLING_SUPPORT": 98.00,
                "PREV_HIGH": 103.00,
                "PREV_LOW": 98.50,
            }
        ] * 20)

        result = calculate_risk(
            df=df,
            analysis={
                "signal": "BULLISH",
                "market_regime": "TRENDING_BULL"
            },
            entry_setup={
                "entry_type": "EMA_PULLBACK",
                "entry_quality": "HIGH",
                "avoid_chasing": False
            }
        )

        self.assertTrue(result["trade_allowed"])
        self.assertEqual(result["stop_loss"], 99.25)
        self.assertGreaterEqual(result["risk_reward"], 3.0)
        self.assertFalse(
            any(
                reason.startswith("ATR floor adjusted stop")
                for reason in result["reasons"]
            )
        )


if __name__ == "__main__":

    unittest.main()