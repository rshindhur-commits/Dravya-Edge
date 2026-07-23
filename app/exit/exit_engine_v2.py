from __future__ import annotations

from datetime import time

from app.exit.trend_health_engine import evaluate_live_trend_health


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_short(entry_type):
    value = str(entry_type or "").upper()
    return any(token in value for token in ["PUT", "SHORT", "BEAR", "BREAKDOWN", "REJECTION"])


def _rr(price, entry, stop, is_short):
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    return (entry - price if is_short else price - entry) / risk


def evaluate_shadow_exit_v2(df, risk_setup, entry_setup=None, trade_state=None):
    """Evaluate V2 exits in shadow mode; it never mutates trade state."""
    latest = df.iloc[-1]
    entry_type = (trade_state or {}).get("entry_type") or (entry_setup or {}).get("entry_type")
    is_short = _is_short(entry_type)
    direction = "PUT" if is_short else "CALL"
    entry = _number(risk_setup.get("entry_price"))
    stop = _number(risk_setup.get("stop_loss"))
    target = _number(risk_setup.get("take_profit"))
    close = _number(latest.get("Close"))
    high = _number(latest.get("High"), close)
    low = _number(latest.get("Low"), close)
    current_r = _rr(close, entry, stop, is_short)
    highest = max(_number((trade_state or {}).get("highest_price"), entry), high)
    lowest = min(_number((trade_state or {}).get("lowest_price"), entry), low)
    bars_in_trade = int((trade_state or {}).get("bars_in_trade") or 0) + 1
    mfe_r = _rr(lowest if is_short else highest, entry, stop, is_short)
    mae_r = max(0.0, _rr(highest if is_short else lowest, entry, stop, is_short) * -1)
    health = evaluate_live_trend_health(latest, direction)
    phase = "HOLD"
    exit_signal = False
    if (high >= stop if is_short else low <= stop):
        phase = "HARD_STOP"
        exit_signal = True
    elif (low <= target if is_short else high >= target):
        phase = "HARD_TARGET"
        exit_signal = True
    elif health["trend_failure_confirmed"]:
        phase = "TREND_FAILURE"
        exit_signal = True
    elif mfe_r >= 1.5 and current_r <= mfe_r - 1 and health["status"] in {"WEAKENING", "BROKEN"}:
        phase = "PROFIT_PROTECTION"
        exit_signal = True
    elif int((trade_state or {}).get("bars_in_trade") or 0) >= 24 and current_r < 0.5:
        phase = "TIME_EXIT"
        exit_signal = True
    timestamp = getattr(latest, "name", None)
    if not exit_signal and timestamp is not None:
        try:
            timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp
            if timestamp.tz_convert("America/New_York").time() >= time(15, 45):
                phase = "END_OF_DAY"
                exit_signal = True
        except Exception:
            pass
    return {
        "exit_signal": exit_signal,
        "exit_phase": phase,
        "trend_health_score": health["score"],
        "trend_health_status": health["status"],
        "trend_failure_confirmed": health["trend_failure_confirmed"],
        "mfe_r": round(mfe_r, 2),
        "mae_r": round(mae_r, 2),
        "rr_progress": round(current_r, 2),
        "highest_price": round(highest, 2),
        "lowest_price": round(lowest, 2),
        "bars_in_trade": bars_in_trade,
    }