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

# This scorer runs 0-12. `app/exit/trend_health_engine.py` scores the same idea
# 0-100 and both write a column called `trend_health_score`, which has already
# cost one silently dead metric: `learning_engine` tested `Trend Health Score >=
# 80` against these twelfths, so `premature` exits could never be anything but
# zero, while `trade_efficiency/recommendations` divided the same column by 12.
#
# Anything comparing this score against a percentage must go through
# `trend_health_percent` rather than carry its own divisor.
TREND_HEALTH_MAX = sum(TREND_HEALTH_WEIGHTS.values())


def trend_health_percent(score):
    """This scorer's 0-12 reading as a 0-100 percentage."""

    value = _float(score, None) if score is not None else None

    if value is None:

        return None

    return round(value / TREND_HEALTH_MAX * 100, 1)


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
    """Score a trend out of 12 from the eight checks above.

    Reads `snapshot["trend_inputs"]` when present. Those are the same readings
    oriented to the trade's own direction by `build_trade_snapshot`, and without
    them every check here is a bullish one: `price_above_vwap` scores a point of
    health for a PUT whose short is failing. Ten of the fifteen trades closed
    between 2026-08-19 and 08-21 were PUTs and all ten were scored that way.

    Falls back to the flat snapshot so callers that pass raw readings -- and the
    archived rows already written that way -- still resolve.
    """

    snapshot = (snapshot or {}).get("trend_inputs") or snapshot or {}
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