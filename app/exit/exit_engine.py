from datetime import time

import pandas as pd

from app.utils.runtime_logging import debug_print


def _is_short_entry(entry_type):

    entry_type = str(entry_type or "").upper()

    return (
        "SHORT" in entry_type
        or "BEARISH" in entry_type
        or "BREAKDOWN" in entry_type
        or "REJECTION" in entry_type
    )


def _get_timestamp_et(latest):

    timestamp = getattr(
        latest,
        "name",
        None
    )

    if timestamp is None:

        return None

    try:

        if timestamp.tzinfo is None:

            timestamp = timestamp.tz_localize("UTC")

        return timestamp.tz_convert("America/New_York")

    except Exception:

        return None


def _calculate_rr_progress(
    current_price,
    entry_price,
    stop_loss,
    is_short
):

    risk = abs(entry_price - stop_loss)

    if risk <= 0:

        return 0

    if is_short:

        reward = entry_price - current_price

    else:

        reward = current_price - entry_price

    return reward / risk


def _round_float(value, digits=2):

    try:

        return round(float(value), digits)

    except (TypeError, ValueError):

        return value


def trend_still_valid(df, direction):

    latest = df.iloc[-1]
    direction = str(direction or "").upper()

    if direction == "CALL":

        return (
            latest["Close"] > latest["VWAP"]
            and latest["EMA9"] > latest["EMA20"]
            and latest["RSI"] > 55
        )

    return (
        latest["Close"] < latest["VWAP"]
        and latest["EMA9"] < latest["EMA20"]
        and latest["RSI"] < 45
    )


def _should_guard_early_exit(df, exit_reason, bars_in_trade, rr_progress, is_short):

    if bars_in_trade > 3:

        return False

    if abs(rr_progress) >= 0.25:

        return False

    weak_exit_reasons = [
        "EMA9 invalidation",
        "VWAP invalidation",
        "MACD",
        "Failed breakout"
    ]

    if not any(reason in str(exit_reason) for reason in weak_exit_reasons):

        return False

    return trend_still_valid(
        df,
        "PUT" if is_short else "CALL"
    )


def evaluate_exit(
    df,
    analysis,
    risk_setup,
    entry_setup=None,
    trade_state=None
):

    latest = df.iloc[-1]

    entry_type = (
        entry_setup.get("entry_type")
        if entry_setup
        else None
    )

    if trade_state and trade_state.get("entry_type"):

        entry_type = trade_state.get("entry_type")

    is_short = _is_short_entry(entry_type)

    entry_price = risk_setup.get(
        "entry_price"
    )
    stop_loss = risk_setup.get(
        "stop_loss"
    )
    take_profit = risk_setup.get(
        "take_profit"
    )

    current_price = latest["Close"]
    atr = latest.get("ATR", 0) or 0

    highest_price = (
        trade_state.get("highest_price")
        if trade_state
        else entry_price
    )
    lowest_price = (
        trade_state.get("lowest_price")
        if trade_state
        else entry_price
    )

    if highest_price is None:

        highest_price = entry_price

    if lowest_price is None:

        lowest_price = entry_price

    highest_price = max(
        highest_price,
        current_price
    )
    lowest_price = min(
        lowest_price,
        current_price
    )

    bars_in_trade = (
        trade_state.get("bars_in_trade", 0)
        if trade_state
        else 0
    ) + 1

    partial_profit_taken = (
        trade_state.get("partial_profit_taken", False)
        if trade_state
        else False
    )

    rr_progress = _calculate_rr_progress(
        current_price=current_price,
        entry_price=entry_price,
        stop_loss=stop_loss,
        is_short=is_short
    )

    updated_stop = stop_loss
    trailing_stop = stop_loss
    exit_signal = False
    exit_reason = "Hold"
    trade_action = "HOLD"
    adjustment_reason = "Trend intact"

    # Hard stop and hard target are evaluated before softer invalidation rules.
    if is_short:

        if latest["High"] >= stop_loss:

            exit_signal = True
            exit_reason = "Hard stop hit (short)"

        elif latest["Low"] <= take_profit:

            exit_signal = True
            exit_reason = "Profit target reached (short)"

    else:

        if latest["Low"] <= stop_loss:

            exit_signal = True
            exit_reason = "Hard stop hit (long)"

        elif latest["High"] >= take_profit:

            exit_signal = True
            exit_reason = "Profit target reached (long)"

    if not exit_signal and rr_progress >= 1:

        if is_short:

            updated_stop = min(
                updated_stop,
                entry_price
            )

        else:

            updated_stop = max(
                updated_stop,
                entry_price
            )

        adjustment_reason = "Moved stop to breakeven"

    if not exit_signal and rr_progress >= 1.5:

        partial_profit_taken = True
        trade_action = "PARTIAL_PROFIT"
        adjustment_reason = "Partial profit threshold reached"

    if not exit_signal and rr_progress >= 2 and atr > 0:

        if is_short:

            trailing_stop = min(
                updated_stop,
                current_price + atr
            )
            updated_stop = trailing_stop

        else:

            trailing_stop = max(
                updated_stop,
                current_price - atr
            )
            updated_stop = trailing_stop

        adjustment_reason = "ATR trailing stop active"

    if not exit_signal:

        if is_short and pd.notna(latest.get("EMA9")):

            if (
                current_price > latest["EMA9"]
                and latest.get("EMA9_SLOPE", 0) > 0
            ):

                exit_signal = True
                exit_reason = "EMA9 invalidation (short)"

        elif not is_short and pd.notna(latest.get("EMA9")):

            if (
                current_price < latest["EMA9"]
                and latest.get("EMA9_SLOPE", 0) < 0
            ):

                exit_signal = True
                exit_reason = "EMA9 invalidation (long)"

    if not exit_signal and pd.notna(latest.get("VWAP")):

        if is_short and current_price > latest["VWAP"]:

            exit_signal = True
            exit_reason = "VWAP invalidation (short)"

        elif not is_short and current_price < latest["VWAP"]:

            exit_signal = True
            exit_reason = "VWAP invalidation (long)"

    if not exit_signal:

        if (
            is_short
            and pd.notna(latest.get("MACD"))
            and pd.notna(latest.get("MACD_SIGNAL"))
            and latest["MACD"] > latest["MACD_SIGNAL"]
        ):

            exit_signal = True
            exit_reason = "MACD bullish crossover (short)"

        elif (
            not is_short
            and pd.notna(latest.get("MACD"))
            and pd.notna(latest.get("MACD_SIGNAL"))
            and latest["MACD"] < latest["MACD_SIGNAL"]
        ):

            exit_signal = True
            exit_reason = "MACD bearish crossover (long)"

    if not exit_signal and latest.get("FAILED_BREAKOUT", False):

        exit_signal = True
        exit_reason = "Failed breakout"

    if not exit_signal and bars_in_trade >= 24 and rr_progress < 0.5:

        exit_signal = True
        exit_reason = "Time exit: trade stagnation"

    latest_et = _get_timestamp_et(latest)

    if (
        not exit_signal
        and latest_et is not None
        and latest_et.time() >= time(15, 45)
        and rr_progress < 1
    ):

        exit_signal = True
        exit_reason = "Near-close exit without sufficient profit"

    if exit_signal and _should_guard_early_exit(
        df,
        exit_reason,
        bars_in_trade,
        rr_progress,
        is_short
    ):

        exit_signal = False
        exit_reason = "Hold"
        adjustment_reason = "Early weak exit guarded; trend intact"

    if exit_signal:

        trade_action = "EXIT"
        adjustment_reason = exit_reason

    debug_print(
        f"[EXIT DEBUG] "
        f"is_short={is_short} "
        f"rr_progress={round(rr_progress, 2)} "
        f"bars={bars_in_trade} "
        f"exit_signal={exit_signal} "
        f"reason={exit_reason}"
    )

    return {
        "exit_signal": exit_signal,
        "exit_reason": exit_reason,
        "trailing_stop": _round_float(trailing_stop),
        "updated_stop": _round_float(updated_stop),
        "rr_progress": _round_float(rr_progress),
        "highest_price": _round_float(highest_price),
        "lowest_price": _round_float(lowest_price),
        "bars_in_trade": int(bars_in_trade),
        "partial_profit_taken": partial_profit_taken,
        "trade_action": trade_action,
        "adjustment_reason": adjustment_reason
    }