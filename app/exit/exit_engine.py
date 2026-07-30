from datetime import time
import os

import pandas as pd

from app.analytics.exit_waterfall import build_exit_waterfall
from app.exit.exit_confidence import evaluate_exit_confidence
from app.exit.trend_health_engine import evaluate_live_trend_health
from app.utils.runtime_logging import debug_print


EXIT_PRIORITY = {
    "HARD_STOP": 100,
    "HARD_TARGET": 95,
    "PROFIT_PROTECTION": 85,
    "EMA": 80,
    "VWAP": 70,
    "MACD": 60,
    "FAILED_BREAKOUT": 50,
    "TIME_EXIT": 40,
    "NEAR_CLOSE": 30
}


def _env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


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


def resolve_risk_per_share(entry_price, initial_stop_loss, current_stop_loss):
    """Risk per share as fixed at entry — the only valid R denominator.

    R must be measured against the risk the trade was opened with. Deriving it
    from the *current* stop breaks the moment a stop is moved: a breakeven move
    makes `abs(entry - stop)` zero, so rr_progress and mfe_r collapse to 0 and
    never recover. That silently disables partial profit, the ATR trailing stop,
    and multiday profit protection, all of which are gated on R thresholds.

    Falls back to the current stop for trades opened before `initial_stop_loss`
    was persisted.
    """

    for candidate in (initial_stop_loss, current_stop_loss):

        if candidate is None or entry_price is None:
            continue

        try:
            risk = abs(float(entry_price) - float(candidate))
        except (TypeError, ValueError):
            continue

        if risk > 0:
            return risk

    return 0.0


def _calculate_rr_progress(
    current_price,
    entry_price,
    stop_loss,
    is_short,
    risk_per_share=None
):

    risk = (
        risk_per_share
        if risk_per_share is not None
        else abs(entry_price - stop_loss)
    )

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


def _exit_diagnostic(code, reason):

    return {
        "code": code,
        "reason": reason,
        "priority": EXIT_PRIORITY.get(code, 0)
    }


def _select_primary_exit(exit_reasons):

    if not exit_reasons:

        return None

    return sorted(
        exit_reasons,
        key=lambda item: item.get("priority", 0),
        reverse=True
    )[0]


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

    # The protective stop may have been moved to breakeven or trailed. R is
    # always measured against the risk frozen at entry, never the moved stop.
    initial_stop_loss = (
        risk_setup.get("initial_stop_loss")
        or (trade_state or {}).get("initial_stop_loss")
    )
    risk_per_share = resolve_risk_per_share(
        entry_price,
        initial_stop_loss,
        stop_loss
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
        is_short=is_short,
        risk_per_share=risk_per_share
    )
    direction = "PUT" if is_short else "CALL"
    trend_health = evaluate_live_trend_health(latest, direction)
    mfe_r = _calculate_rr_progress(
        lowest_price if is_short else highest_price,
        entry_price,
        stop_loss,
        is_short,
        risk_per_share=risk_per_share,
    )
    exit_confidence = evaluate_exit_confidence(
        latest,
        trend_health,
        rr_progress,
        mfe_r,
        bars_in_trade,
        is_short,
    )

    holding_profile = str((trade_state or {}).get("holding_profile") or "INTRADAY").upper()
    profit_lock_mfe_r = _env_float("MULTIDAY_PROFIT_LOCK_MFE_R", 2.0)
    profit_lock_r = _env_float("MULTIDAY_PROFIT_LOCK_R", 1.0)
    profit_exit_mfe_r = _env_float("MULTIDAY_PROFIT_EXIT_MFE_R", 3.0)
    profit_max_giveback_r = _env_float("MULTIDAY_PROFIT_MAX_GIVEBACK_R", 1.0)
    profit_protection_active = False
    profit_lock_stop = None
    profit_giveback_r = max(0.0, mfe_r - rr_progress)

    updated_stop = stop_loss
    trailing_stop = stop_loss
    exit_signal = False
    exit_reason = "Hold"
    exit_reasons = []
    trade_action = "HOLD"
    adjustment_reason = "Trend intact"

    # Hard stop and hard target are evaluated before softer invalidation rules.
    if is_short:

        if latest["High"] >= stop_loss:

            exit_reasons.append(_exit_diagnostic("HARD_STOP", "Hard stop hit (short)"))

        if latest["Low"] <= take_profit:

            exit_reasons.append(_exit_diagnostic("HARD_TARGET", "Profit target reached (short)"))

    else:

        if latest["Low"] <= stop_loss:

            exit_reasons.append(_exit_diagnostic("HARD_STOP", "Hard stop hit (long)"))

        if latest["High"] >= take_profit:

            exit_reasons.append(_exit_diagnostic("HARD_TARGET", "Profit target reached (long)"))

    primary_exit = _select_primary_exit(exit_reasons)

    if primary_exit:

        exit_signal = True
        exit_reason = primary_exit["reason"]

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

    if not exit_signal and holding_profile == "MULTIDAY" and mfe_r >= profit_lock_mfe_r:

        risk = risk_per_share
        profit_lock_stop = (
            entry_price - (risk * profit_lock_r)
            if is_short
            else entry_price + (risk * profit_lock_r)
        )
        updated_stop = (
            min(updated_stop, profit_lock_stop)
            if is_short
            else max(updated_stop, profit_lock_stop)
        )
        trailing_stop = updated_stop
        profit_protection_active = True
        adjustment_reason = f"Multiday profit lock: {profit_lock_r:.1f}R protected"

        if mfe_r >= profit_exit_mfe_r and profit_giveback_r >= profit_max_giveback_r:
            exit_reasons.append(_exit_diagnostic(
                "PROFIT_PROTECTION",
                f"Multiday profit protection: {profit_giveback_r:.2f}R giveback from {mfe_r:.2f}R peak",
            ))
            primary_exit = _select_primary_exit(exit_reasons)
            exit_signal = True
            exit_reason = primary_exit["reason"]

    if True:

        if is_short and pd.notna(latest.get("EMA9")):

            if (
                current_price > latest["EMA9"]
                and latest.get("EMA9_SLOPE", 0) > 0
            ):

                exit_reasons.append(_exit_diagnostic("EMA", "EMA9 invalidation (short)"))

        elif not is_short and pd.notna(latest.get("EMA9")):

            if (
                current_price < latest["EMA9"]
                and latest.get("EMA9_SLOPE", 0) < 0
            ):

                exit_reasons.append(_exit_diagnostic("EMA", "EMA9 invalidation (long)"))

        primary_exit = _select_primary_exit(exit_reasons)

        if primary_exit:

            exit_signal = True
            exit_reason = primary_exit["reason"]

    if pd.notna(latest.get("VWAP")):

        if is_short and current_price > latest["VWAP"]:

            exit_reasons.append(_exit_diagnostic("VWAP", "VWAP invalidation (short)"))

        elif not is_short and current_price < latest["VWAP"]:

            exit_reasons.append(_exit_diagnostic("VWAP", "VWAP invalidation (long)"))

        primary_exit = _select_primary_exit(exit_reasons)

        if primary_exit:

            exit_signal = True
            exit_reason = primary_exit["reason"]

    if True:

        if (
            is_short
            and pd.notna(latest.get("MACD"))
            and pd.notna(latest.get("MACD_SIGNAL"))
            and latest["MACD"] > latest["MACD_SIGNAL"]
        ):

            exit_reasons.append(_exit_diagnostic("MACD", "MACD bullish crossover (short)"))

        elif (
            not is_short
            and pd.notna(latest.get("MACD"))
            and pd.notna(latest.get("MACD_SIGNAL"))
            and latest["MACD"] < latest["MACD_SIGNAL"]
        ):

            exit_reasons.append(_exit_diagnostic("MACD", "MACD bearish crossover (long)"))

        primary_exit = _select_primary_exit(exit_reasons)

        if primary_exit:

            exit_signal = True
            exit_reason = primary_exit["reason"]

    if latest.get("FAILED_BREAKOUT", False):

        exit_reasons.append(_exit_diagnostic("FAILED_BREAKOUT", "Failed breakout"))
        primary_exit = _select_primary_exit(exit_reasons)
        exit_signal = True
        exit_reason = primary_exit["reason"]

    if bars_in_trade >= 24 and rr_progress < 0.5:

        exit_reasons.append(_exit_diagnostic("TIME_EXIT", "Time exit: trade stagnation"))
        primary_exit = _select_primary_exit(exit_reasons)
        exit_signal = True
        exit_reason = primary_exit["reason"]

    latest_et = _get_timestamp_et(latest)

    if (
        latest_et is not None
        and latest_et.time() >= time(15, 45)
        and rr_progress < 1
    ):

        exit_reasons.append(_exit_diagnostic("NEAR_CLOSE", "Near-close exit without sufficient profit"))
        primary_exit = _select_primary_exit(exit_reasons)
        exit_signal = True
        exit_reason = primary_exit["reason"]

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

    selected_exit = _select_primary_exit(exit_reasons) if exit_signal else None
    first_ema_break = (
        selected_exit is not None
        and selected_exit.get("code") == "EMA"
        and len(exit_reasons) == 1
        and not bool((trade_state or {}).get("v1_ema_grace_pending"))
    )
    grace_zone_active = first_ema_break and exit_confidence["grace_zone_eligible"]
    if grace_zone_active:
        exit_signal = False
        exit_reason = "Hold"
        trade_action = "HOLD"
        adjustment_reason = "EMA grace zone active; awaiting one-bar confirmation"

    if exit_signal:

        trade_action = "EXIT"
        adjustment_reason = exit_reason

    selected_exit = (
        _select_primary_exit(exit_reasons)
        if exit_signal
        else None
    )
    exit_rule = selected_exit.get("code") if selected_exit else "HOLD"
    exit_waterfall = build_exit_waterfall(
        exit_reasons,
        selected_rule=exit_rule if exit_signal else None
    )

    debug_print(
        f"[EXIT DEBUG] "
        f"is_short={is_short} "
        f"rr_progress={round(rr_progress, 2)} "
        f"bars={bars_in_trade} "
        f"exit_signal={exit_signal} "
        f"reason={exit_reason} "
        f"all_reasons={[item['reason'] for item in exit_reasons]}"
    )

    return {
        "exit_signal": exit_signal,
        "exit_reason": exit_reason,
        "exit_reasons": [item["reason"] for item in exit_reasons],
        "exit_diagnostics": exit_reasons,
        "primary_exit": exit_reason,
        "exit_waterfall": exit_waterfall,
        "exit_rule": exit_rule,
        "exit_stage": (
            next(
                (
                    item["stage"]
                    for item in exit_waterfall
                    if item["rule"] == exit_rule
                ),
                None
            )
            if exit_signal
            else None
        ),
        "secondary_exits": [
            item["reason"]
            for item in exit_reasons
            if item["reason"] != exit_reason
        ],
        "ignored_exit_signals": [
            item["reason"]
            for item in exit_reasons
            if not exit_signal or item["reason"] != exit_reason
        ],
        "trailing_stop": _round_float(trailing_stop),
        "updated_stop": _round_float(updated_stop),
        "rr_progress": _round_float(rr_progress),
        # V1's own R measurements, against the risk frozen at entry. Exposed so
        # callers no longer have to borrow MFE from the V2 shadow, which carries
        # its own independent risk denominator.
        "mfe_r": _round_float(mfe_r),
        "risk_per_share": _round_float(risk_per_share),
        "highest_price": _round_float(highest_price),
        "lowest_price": _round_float(lowest_price),
        "bars_in_trade": int(bars_in_trade),
        "partial_profit_taken": partial_profit_taken,
        "trade_action": trade_action,
        "adjustment_reason": adjustment_reason,
        "trend_health_score": trend_health["score"],
        "exit_confidence_score": exit_confidence["exit_confidence_score"],
        "profit_protection_active": profit_protection_active,
        "profit_lock_stop": _round_float(profit_lock_stop),
        "profit_giveback_r": _round_float(profit_giveback_r),
        "grace_zone_active": grace_zone_active,
        "v1_ema_grace_pending": grace_zone_active,
    }