from __future__ import annotations

import pandas as pd
from app.analytics.trend_health import trend_health_percent


def _num(series, default=0):

    try:

        return pd.to_numeric(series, errors="coerce")

    except Exception:

        return pd.Series(dtype=float)


def _mean(df, column):

    if df is None or df.empty or column not in df.columns:

        return None

    values = _num(df[column]).dropna()

    return float(values.mean()) if not values.empty else None


def calculate_trade_efficiency_score(df):

    if df is None or df.empty:

        return None

    capture = _mean(df, "Trend Capture %") or 0
    # Same column, same normalisation, one constant. The literal 12 here and the
    # literal 80 in learning_engine were the two halves of the same confusion.
    trend_health = trend_health_percent(_mean(df, "Trend Health Score")) or 0
    opportunity_cost = 100

    if "Available Move" in df.columns and "Left On Table" in df.columns:

        available = _num(df["Available Move"])
        left = _num(df["Left On Table"])
        valid = available > 0

        if valid.any():

            opportunity_cost = max(
                0,
                100 - float((left[valid] / available[valid] * 100).mean())
            )

    exit_quality = opportunity_cost

    return round(
        capture * 0.40
        + exit_quality * 0.20
        + opportunity_cost * 0.20
        + trend_health * 0.20,
        2
    )


def generate_trade_efficiency_recommendation(df):

    if df is None or df.empty:

        return {
            "priority": "NONE",
            "reason": "No trade efficiency rows yet.",
            "recommendation": "Collect more closed paper trades."
        }

    capture = _mean(df, "Trend Capture %") or 0
    trend_continued_rate = 0
    strong_at_exit_rate = 0
    delay_two_gain = _mean(df, "Profit +2 Bars") or 0
    opportunity_cost = _mean(df, "Left On Table") or 0

    if "Trend Continued" in df.columns:

        trend_continued_rate = df["Trend Continued"].astype(str).str.lower().isin(["true", "1", "yes"]).mean() * 100

    if "Trend Health State" in df.columns:

        strong_at_exit_rate = df["Trend Health State"].astype(str).str.upper().eq("STRONG").mean() * 100

    if capture < 55 and trend_continued_rate > 70:

        return {
            "priority": "HIGH",
            "reason": f"{round(trend_continued_rate, 1)}% of trades continued after exit while capture averaged {round(capture, 1)}%.",
            "recommendation": "Investigate EMA-based trailing logic before adding new entries."
        }

    if capture < 55 and trend_continued_rate < 20:

        return {
            "priority": "LOW",
            "reason": "Low capture occurred mostly after trends stopped continuing.",
            "recommendation": "Current exits appear appropriate; review entry quality first."
        }

    if delay_two_gain > 0.2:

        return {
            "priority": "MEDIUM",
            "reason": f"Waiting 2 bars adds {round(delay_two_gain, 2)} average price units.",
            "recommendation": "Investigate short confirmation delays before exiting."
        }

    if opportunity_cost > 25:

        return {
            "priority": "HIGH",
            "reason": f"Average opportunity cost is {round(opportunity_cost, 2)} price units per trade.",
            "recommendation": "Highest ROI engineering work is exit optimization."
        }

    if strong_at_exit_rate > 80:

        return {
            "priority": "HIGH",
            "reason": f"{round(strong_at_exit_rate, 1)}% of exits occurred while trend health was STRONG.",
            "recommendation": "Review exit logic that fires while trend remains intact."
        }

    return {
        "priority": "LOW",
        "reason": "Trade efficiency is within current validation expectations.",
        "recommendation": "Continue collecting paper-trade evidence before changing exits."
    }