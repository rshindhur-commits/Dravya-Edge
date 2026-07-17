import unittest

from app.projections.trade_projection import project_trade


class TradeProjectionTests(unittest.TestCase):

    def test_trending_bear_alias_matches_trending_bearish(self):

        base_analysis = {
            "signal": "BEARISH",
            "score": -8,
            "ATR": 2,
            "reasons": [],
        }
        entry_setup = {
            "entry_quality": "HIGH",
            "entry_type": "EMA_REJECTION_SHORT",
        }
        risk_setup = {
            "risk_reward": 2.0,
            "trade_allowed": True,
            "take_profit": 94,
            "stop_loss": 103,
        }

        short_alias = project_trade(
            "AMD",
            100,
            {**base_analysis, "market_regime": "TRENDING_BEAR"},
            entry_setup,
            risk_setup,
            alignment_score=-4,
        )
        long_alias = project_trade(
            "AMD",
            100,
            {**base_analysis, "market_regime": "TRENDING_BEARISH"},
            entry_setup,
            risk_setup,
            alignment_score=-4,
        )

        self.assertEqual(
            short_alias["expected_move_pct"],
            long_alias["expected_move_pct"],
        )
        self.assertEqual(
            short_alias["projected_option_gain"],
            long_alias["projected_option_gain"],
        )


if __name__ == "__main__":

    unittest.main()