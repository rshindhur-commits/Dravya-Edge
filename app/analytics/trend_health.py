from __future__ import annotations


TREND_HEALTH_WEIGHTS = {
    "ema_alignment": 2,
    "price_above_ema9": 2,
    "price_above_vwap": 2,
    "higher_high": 2,
    "higher_low": 1,
    "macd_bullish": 1,
    "rsi_above_50": 1,
    "relative_volume": 1,
}


def _truthy(value):

    if isinstance(value, bool):

        return value

    return str(value).strip().lower() in {"true", "1", "yes", "y", "on"}


def _float(value, default=0.0):

    try:

        if value is None:

            return default

        return float(value)

    except Exception:

        return default


def trend_health_state(score):

    if score >= 10:

        return "STRONG"

    if score >= 7:

        return "HEALTHY"

    if score >= 4:

        return "WEAKENING"

    return "BROKEN"


def evaluate_trend_health(snapshot):

    snapshot = snapshot or {}
    checks = {
        "ema_alignment": _truthy(snapshot.get("ema_alignment")),
        "price_above_ema9": _truthy(snapshot.get("price_above_ema9")),
        "price_above_vwap": _truthy(snapshot.get("price_above_vwap")),
        "higher_high": _truthy(snapshot.get("higher_high")),
        "higher_low": _truthy(snapshot.get("higher_low")),
        "macd_bullish": _truthy(snapshot.get("macd_bullish")),
        "rsi_above_50": _float(snapshot.get("rsi")) > 50,
        "relative_volume": _float(snapshot.get("relative_volume")) > 1,
    }
    score = sum(
        TREND_HEALTH_WEIGHTS[name]
        for name, passed in checks.items()
        if passed
    )
    reasons = [
        name
        for name, passed in checks.items()
        if passed
    ]

    return {
        "score": score,
        "state": trend_health_state(score),
        "reasons": reasons,
    }