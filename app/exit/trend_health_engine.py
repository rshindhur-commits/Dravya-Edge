from __future__ import annotations


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def evaluate_live_trend_health(latest, direction):
    """Return a lightweight live trend-health score for V2 shadow exits."""
    direction = str(direction or "CALL").upper()
    is_short = direction in {"PUT", "SHORT"}
    close = _number(latest.get("Close"))
    ema9 = _number(latest.get("EMA9"))
    ema20 = _number(latest.get("EMA20"))
    vwap = _number(latest.get("VWAP"))
    macd = _number(latest.get("MACD"))
    macd_signal = _number(latest.get("MACD_SIGNAL"))
    rsi = _number(latest.get("RSI"))
    rel_volume = _number(latest.get("REL_VOLUME"))
    ema_aligned = ema9 < ema20 if is_short else ema9 > ema20
    above_vwap = close < vwap if is_short else close > vwap
    structure = bool(latest.get("LOWER_HIGH") and latest.get("LOWER_LOW")) if is_short else bool(latest.get("HIGHER_HIGH") and latest.get("HIGHER_LOW"))
    macd_aligned = macd < macd_signal if is_short else macd > macd_signal
    rsi_aligned = rsi < 45 if is_short else rsi > 55
    score = (
        30 * ema_aligned
        + 25 * above_vwap
        + 20 * structure
        + 15 * macd_aligned
        + 5 * rsi_aligned
        + 5 * (rel_volume >= 1.0)
    )
    status = "STRONG" if score >= 80 else "HEALTHY" if score >= 60 else "WEAKENING" if score >= 40 else "BROKEN"
    ema_lost = not ema_aligned
    vwap_lost = not above_vwap
    structure_failed = bool(latest.get("HIGHER_LOW") is False and latest.get("LOWER_LOW")) if not is_short else bool(latest.get("LOWER_HIGH") is False and latest.get("HIGHER_HIGH"))
    trend_failure_confirmed = status == "BROKEN" or (ema_lost and vwap_lost) or structure_failed
    return {
        "score": int(score),
        "status": status,
        "ema_lost": ema_lost,
        "vwap_lost": vwap_lost,
        "structure_failed": structure_failed,
        "trend_failure_confirmed": trend_failure_confirmed,
    }