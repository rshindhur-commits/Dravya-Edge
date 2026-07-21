import unittest
from unittest.mock import patch

import pandas as pd

from app.analytics.trend_capture import (
    analyze_trend_capture,
    build_trend_capture_row,
    summarize_trend_capture
)
from app.state.paper_trade_manager import _append_trend_capture_for_closed_trade


def _df(highs, lows):

    return pd.DataFrame(
        {
            "High": highs,
            "Low": lows,
            "Close": highs,
            "EMA9": [99, 101, 105][:len(highs)],
            "EMA20": [98, 100, 102][:len(highs)],
            "VWAP": [99, 100, 104][:len(highs)],
            "MACD": [0.5, 1.0, 1.2][:len(highs)],
            "MACD_SIGNAL": [0.4, 0.8, 1.0][:len(highs)],
            "RSI": [55, 60, 62][:len(highs)],
            "ATR": [2, 2, 2][:len(highs)],
            "Volume": [1000, 1100, 1200][:len(highs)],
            "REL_VOLUME": [1.1, 1.2, 1.3][:len(highs)],
            "HIGHER_HIGH": [True] * len(highs),
            "HIGHER_LOW": [True] * len(highs),
            "LOWER_HIGH": [False] * len(highs),
            "LOWER_LOW": [False] * len(highs),
        },
        index=pd.date_range(
            "2026-07-17 10:00:00",
            periods=len(highs),
            freq="5min"
        )
    )


class TrendCaptureTests(unittest.TestCase):

    def test_long_capture(self):

        result = analyze_trend_capture(
            {
                "direction": "CALL",
                "entry_price": 100,
                "close_price": 106,
                "opened_at": "2026-07-17 10:00:00",
            },
            _df([101, 105, 110], [99, 101, 104])
        )

        self.assertEqual(result["available_move"], 10)
        self.assertEqual(result["captured_move"], 6)
        self.assertEqual(result["trend_capture_pct"], 60)
        self.assertEqual(result["mfe"], 10)
        self.assertEqual(result["mae"], 1)
        self.assertEqual(result["left_on_table"], 4)

    def test_short_capture(self):

        result = analyze_trend_capture(
            {
                "direction": "PUT",
                "entry_price": 100,
                "close_price": 94,
                "opened_at": "2026-07-17 10:00:00",
            },
            _df([101, 99, 96], [98, 95, 90])
        )

        self.assertEqual(result["available_move"], 10)
        self.assertEqual(result["captured_move"], 6)
        self.assertEqual(result["trend_capture_pct"], 60)
        self.assertEqual(result["mfe"], 10)
        self.assertEqual(result["mae"], 1)
        self.assertEqual(result["left_on_table"], 4)

    def test_no_trend_zero_available_move(self):

        result = analyze_trend_capture(
            {
                "direction": "CALL",
                "entry_price": 100,
                "close_price": 99,
            },
            _df([100, 100], [98, 99])
        )

        self.assertEqual(result["available_move"], 0)
        self.assertEqual(result["trend_capture_pct"], 0)

    def test_immediate_stop(self):

        result = analyze_trend_capture(
            {
                "direction": "CALL",
                "entry_price": 100,
                "close_price": 98,
            },
            _df([101], [98])
        )

        self.assertEqual(result["captured_move"], -2)
        self.assertEqual(result["left_on_table"], 3)

    def test_immediate_target(self):

        result = analyze_trend_capture(
            {
                "direction": "CALL",
                "entry_price": 100,
                "close_price": 104,
            },
            _df([104], [99])
        )

        self.assertEqual(result["trend_capture_pct"], 100)
        self.assertEqual(result["left_on_table"], 0)

    def test_division_by_zero(self):

        result = analyze_trend_capture(
            {
                "direction": "PUT",
                "entry_price": 100,
                "close_price": 100,
            },
            _df([101], [100])
        )

        self.assertEqual(result["available_move"], 0)
        self.assertEqual(result["trend_capture_pct"], 0)

    def test_flat_trade(self):

        result = analyze_trend_capture(
            {
                "direction": "CALL",
                "entry_price": 100,
                "close_price": 100,
            },
            _df([102], [99])
        )

        self.assertEqual(result["captured_move"], 0)
        self.assertEqual(result["trend_capture_pct"], 0)

    def test_trend_capture_row_includes_exit_verdict(self):

        row = build_trend_capture_row(
            {
                "trade_key": "NVDA|CALL|2026-07-17 10:00:00",
                "symbol": "NVDA",
                "direction": "CALL",
                "entry_type": "EMA_PULLBACK",
                "entry_price": 100,
                "close_price": 104,
            },
            {
                "trend_capture_pct": 40,
                "bars_remaining": 3,
            },
            {
                "trend_health_score": 12,
                "trend_health_state": "STRONG",
                "ema9": 103,
                "ema20": 101,
                "price_above_ema9": True,
                "price_above_vwap": True,
                "ema_alignment": True,
                "macd_bullish": True,
            }
        )

        self.assertEqual(row["Trend Health State"], "STRONG")
        self.assertEqual(row["Exit Quality"], "POOR")
        self.assertEqual(row["Exit Verdict"], "EXIT_TOO_EARLY")
        self.assertTrue(row["Trend Continued"])
        self.assertEqual(row["Entry Grade"], "C")
        self.assertEqual(row["Exit Grade"], "C")
        self.assertEqual(
            row["Exit Verdict Reason"],
            "Trend remained strong after exit; review trailing/hold logic.",
        )

    def test_paper_close_hook_appends_trend_capture_row(self):

        trade = {
            "trading_day": "2026-07-17",
            "session_id": "paper_validation_2026-07-17",
            "trade_key": "NVDA|CALL|2026-07-17 10:00:00",
            "symbol": "NVDA",
            "direction": "CALL",
            "entry_price": 100,
            "close_price": 106,
            "opened_at": "2026-07-17 10:00:00",
            "closed_at": "2026-07-17 10:15:00",
            "exit_reason": "TARGET",
        }

        with patch(
            "app.indicators.technical_indicators.get_polygon_data",
            return_value=_df([101, 105, 110], [99, 101, 104])
        ), patch(
            "app.indicators.technical_indicators.compute_indicators",
            side_effect=lambda df, **kwargs: df
        ), patch(
            "app.analytics.trend_capture.append_trend_capture_row",
            return_value="trend_capture_analysis.csv"
        ) as append_trend_capture, patch(
            "app.analytics.trade_snapshot.append_trade_exit_snapshot",
            return_value="trade_exit_snapshots.csv"
        ) as append_snapshot:

            result = _append_trend_capture_for_closed_trade(trade)

        self.assertEqual(result, "trend_capture_analysis.csv")
        append_snapshot.assert_called_once()
        append_trend_capture.assert_called_once()
        self.assertEqual(
            append_trend_capture.call_args.args[1]["Trend Capture %"],
            60
        )
        self.assertEqual(
            append_trend_capture.call_args.args[1]["Exit Verdict"],
            "NEEDS_REVIEW"
        )

    def test_summary_recommends_trend_management_when_capture_low(self):

        summary = summarize_trend_capture(
            pd.DataFrame([
                {
                    "Setup": "EMA_PULLBACK",
                    "Market Regime": "TRENDING_BULL",
                    "Exit Reason": "EMA",
                    "Trend Capture %": 40,
                    "Maximum Favorable Excursion": 10,
                    "Maximum Adverse Excursion": 2,
                    "Left On Table": 6,
                },
                {
                    "Setup": "BREAKOUT",
                    "Market Regime": "TRENDING_BULL",
                    "Exit Reason": "VWAP",
                    "Trend Capture %": 50,
                    "Maximum Favorable Excursion": 8,
                    "Maximum Adverse Excursion": 1,
                    "Left On Table": 4,
                },
            ])
        )

        self.assertEqual(summary["average_capture"], 45)
        self.assertEqual(summary["median_capture"], 45)
        self.assertEqual(summary["average_mfe"], 9)
        self.assertEqual(summary["average_mae"], 1.5)
        self.assertEqual(summary["average_left_on_table"], 5)
        self.assertIsNotNone(summary["recommendation"])
        self.assertFalse(summary["by_setup"].empty)


if __name__ == "__main__":

    unittest.main()