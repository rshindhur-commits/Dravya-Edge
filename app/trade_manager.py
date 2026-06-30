import pandas as pd


def manage_trade(

    latest,
    entry_price,
    stop_loss,
    take_profit,
    highest_price,
    bars_in_trade

):

    trade_action = "HOLD"

    adjustment_reason = "Trend intact"

    updated_stop = stop_loss

    current_price = latest["Close"]

    # =========================
    # Risk/Reward Progress
    # =========================

    initial_risk = (
        entry_price - stop_loss
    )

    current_reward = (
        current_price - entry_price
    )

    if initial_risk <= 0:

        rr_progress = 0

    else:

        rr_progress = (
            current_reward / initial_risk
        )

    # =========================
    # Track Highest Price
    # =========================

    highest_price = max(
        highest_price,
        current_price
    )

    # =========================
    # Move Stop to Breakeven
    # =========================

    if rr_progress >= 1:

        updated_stop = max(
            updated_stop,
            entry_price
        )

        adjustment_reason = (
            "Moved stop to breakeven"
        )

    # =========================
    # Trail EMA9 During Strong Trend
    # =========================

    if (

        rr_progress >= 2
        and latest["EMA9"] > updated_stop

    ):

        updated_stop = latest["EMA9"]

        adjustment_reason = (
            "Trailing EMA9 stop"
        )

    # =========================
    # Failed Breakout Exit
    # =========================

    if latest["FAILED_BREAKOUT"]:

        trade_action = "EXIT"

        adjustment_reason = (
            "Failed breakout"
        )

    # =========================
    # EMA20 Breakdown Exit
    # =========================

    elif current_price < latest["EMA20"]:

        trade_action = "EXIT"

        adjustment_reason = (
            "Lost EMA20 support"
        )

    # =========================
    # Momentum Collapse Exit
    # =========================

    elif (

        latest["EMA9_SLOPE"] < 0
        and latest["RSI_SLOPE"] < 0
        and current_price < latest["EMA9"]

    ):

        trade_action = "EXIT"

        adjustment_reason = (
            "Momentum breakdown"
        )

    # =========================
    # Time-Based Exit
    # =========================

    elif (

        bars_in_trade > 24
        and rr_progress < 0.5

    ):

        trade_action = "EXIT"

        adjustment_reason = (
            "Trade stagnation"
        )

    # =========================
    # Profit Locking
    # =========================

    elif rr_progress >= 3:

        trade_action = (
            "PARTIAL_PROFIT"
        )

        adjustment_reason = (
            "Lock partial gains"
        )

    return {

        "trade_action": trade_action,

        "updated_stop": round(
            updated_stop,
            2
        ),

        "rr_progress": round(
            rr_progress,
            2
        ),

        "highest_price": round(
            highest_price,
            2
        ),

        "adjustment_reason": (
            adjustment_reason
        )

    }