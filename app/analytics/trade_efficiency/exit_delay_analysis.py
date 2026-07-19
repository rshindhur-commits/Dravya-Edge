from __future__ import annotations

from app.analytics.trade_efficiency.trend_continuation import _direction, _position


DELAY_BARS = [1, 2, 3, 5]


def analyze_exit_delay(exit_bar, df, direction):

    if df is None or df.empty or "Close" not in df.columns:

        return {
            "profit_1_bar": None,
            "profit_2_bars": None,
            "profit_3_bars": None,
            "profit_5_bars": None,
            "best_delay": None,
            "best_profit": None,
            "delay_recommendation": None,
        }

    pos = _position(exit_bar, df)

    if pos is None:

        return {
            "profit_1_bar": None,
            "profit_2_bars": None,
            "profit_3_bars": None,
            "profit_5_bars": None,
            "best_delay": None,
            "best_profit": None,
            "delay_recommendation": None,
        }

    exit_price = float(df["Close"].iloc[pos])
    is_short = _direction(direction) == "SHORT"
    profits = {}

    for delay in DELAY_BARS:

        future_pos = pos + delay

        if future_pos >= len(df):

            profits[delay] = None
            continue

        future_price = float(df["Close"].iloc[future_pos])
        profits[delay] = round(
            (exit_price - future_price) if is_short else (future_price - exit_price),
            4
        )

    available = {
        delay: value
        for delay, value in profits.items()
        if value is not None
    }
    best_delay = max(
        available,
        key=available.get
    ) if available else None
    best_profit = available.get(best_delay) if best_delay is not None else None
    recommendation = None

    if best_profit is not None and best_profit > 0:

        recommendation = f"{best_delay} candle delay would have improved capture."

    return {
        "profit_1_bar": profits.get(1),
        "profit_2_bars": profits.get(2),
        "profit_3_bars": profits.get(3),
        "profit_5_bars": profits.get(5),
        "best_delay": best_delay,
        "best_profit": best_profit,
        "delay_recommendation": recommendation,
    }