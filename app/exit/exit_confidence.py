from __future__ import annotations


def _number(value, default=0.0):

    try:

        return float(value)

    except (TypeError, ValueError):

        return default


def exit_health_state(score):

    if score >= 95:

        return "VERY_HEALTHY"

    if score >= 80:

        return "HEALTHY"

    if score >= 65:

        return "WEAKENING"

    if score >= 45:

        return "AT_RISK"

    return "FAILED"


def evaluate_exit_confidence(latest, health, current_r, mfe_r, bars_in_trade, is_short):
    """Score trend deterioration for V2 shadow exits; 100 means exit confidence."""

    latest = latest if latest is not None else {}
    health = health if health is not None else {}
    score = _number(health.get("score"))
    macd = _number(latest.get("MACD"))
    macd_signal = _number(latest.get("MACD_SIGNAL"))
    rel_volume = _number(latest.get("REL_VOLUME"))
    atr = max(_number(latest.get("ATR")), 0.01)
    close = _number(latest.get("Close"))
    ema9 = _number(latest.get("EMA9"))
    adverse_macd = macd > macd_signal if is_short else macd < macd_signal
    price_ema_lost = close > ema9 if is_short else close < ema9
    ema_lost = bool(health.get("ema_lost")) or price_ema_lost
    vwap_lost = bool(health.get("vwap_lost"))
    atr_exhaustion = abs(close - ema9) / atr >= 2 if ema9 else False
    soft_confirmations = []

    if ema_lost:

        soft_confirmations.append("EMA_LOST")

    if vwap_lost:

        soft_confirmations.append("VWAP_LOST")

    if adverse_macd:

        soft_confirmations.append("MACD_REVERSAL")

    if rel_volume < 0.8:

        soft_confirmations.append("VOLUME_FADE")

    if atr_exhaustion:

        soft_confirmations.append("ATR_EXHAUSTION")

    components = {
        "trend_health": round((100 - score) * 0.30, 2),
        "ema_structure": 20 if ema_lost else 0,
        "vwap_structure": 15 if vwap_lost else 0,
        "macd_momentum": 10 if adverse_macd else 0,
        "relative_volume": 10 if rel_volume < 0.8 else 0,
        "mfe_achieved": 5 if _number(mfe_r) >= 1.5 else 0,
        "time_in_trade": 5 if int(bars_in_trade or 0) >= 12 else 0,
        "atr_exhaustion": 5 if atr_exhaustion else 0,
    }
    confidence = round(min(100, max(0, sum(components.values()))), 2)

    return {
        "exit_confidence_score": confidence,
        "exit_health_state": exit_health_state(score),
        "soft_confirmations": soft_confirmations,
        "soft_confirmation_count": len(soft_confirmations),
        "grace_zone_eligible": (
            score >= 80
            and (_number(current_r) > 0 or _number(mfe_r) >= 1)
            and len(soft_confirmations) == 1
            and not bool(health.get("trend_failure_confirmed"))
        ),
        "components": components,
    }