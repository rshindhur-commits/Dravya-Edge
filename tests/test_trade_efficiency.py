import unittest

import pandas as pd

from app.analytics.trade_efficiency import (
    analyze_exit_delay,
    analyze_post_exit_trend,
    calculate_trade_efficiency_score,
    generate_trade_efficiency_recommendation,
)


def _df():

    return pd.DataFrame(
        {
            "High": [100, 102, 104, 106, 105, 107],
            "Low": [99, 100, 101, 102, 103, 104],
            "Close": [100, 101, 103, 105, 104, 106],
        },
        index=pd.date_range(
            "2026-07-17 10:00:00",
            periods=6,
            freq="5min"
        )
    )


class TradeEfficiencyTests(unittest.TestCase):

    def test_post_exit_trend_long(self):

        result = analyze_post_exit_trend(
            2,
            _df(),
            "CALL"
        )

        self.assertTrue(result["trend_continued"])
        self.assertEqual(result["remaining_move"], 4)
        self.assertEqual(result["bars_remaining"], 3)
        self.assertEqual(result["minutes_remaining"], 15)
        self.assertEqual(result["peak_price"], 107)

    def test_post_exit_trend_short(self):

        result = analyze_post_exit_trend(
            0,
            pd.DataFrame(
                {
                    "High": [100, 99, 98],
                    "Low": [99, 96, 95],
                    "Close": [100, 98, 96],
                }
            ),
            "PUT"
        )

        self.assertTrue(result["trend_continued"])
        self.assertEqual(result["remaining_move"], 5)

    def test_exit_delay_analysis(self):

        result = analyze_exit_delay(
            1,
            _df(),
            "CALL"
        )

        self.assertEqual(result["profit_1_bar"], 2)
        self.assertEqual(result["profit_2_bars"], 4)
        self.assertEqual(result["best_delay"], 2)
        self.assertEqual(result["best_profit"], 4)
        self.assertIn("delay", result["delay_recommendation"])

    def test_trade_efficiency_score(self):

        score = calculate_trade_efficiency_score(
            pd.DataFrame([
                {
                    "Trend Capture %": 80,
                    "Trend Health Score": 9,
                    "Available Move": 10,
                    "Left On Table": 2,
                }
            ])
        )

        self.assertGreater(score, 75)

    def test_recommendation_for_continued_poor_capture(self):

        recommendation = generate_trade_efficiency_recommendation(
            pd.DataFrame([
                {
                    "Trend Capture %": 40,
                    "Trend Continued": True,
                    "Trend Health State": "STRONG",
                    "Profit +2 Bars": 0.1,
                    "Left On Table": 1,
                },
                {
                    "Trend Capture %": 45,
                    "Trend Continued": True,
                    "Trend Health State": "STRONG",
                    "Profit +2 Bars": 0.1,
                    "Left On Table": 1,
                },
            ])
        )

        self.assertEqual(recommendation["priority"], "HIGH")
        self.assertIn("EMA", recommendation["recommendation"])


if __name__ == "__main__":

    unittest.main()