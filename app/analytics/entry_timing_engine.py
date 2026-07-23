from __future__ import annotations


def _number(value, default=0.0):

    try:

        return float(value)

    except (TypeError, ValueError):

        return default


def _grade(score):

    if score > 80:

        return "EXCELLENT"

    if score >= 70:

        return "GOOD"

    if score >= 55:

        return "AVERAGE"

    return "LATE_ENTRY"


def evaluate_entry_timing(v2_entry):
    """Translate V2 entry-location diagnostics into an observational score."""

    v2_entry = v2_entry or {}
    efficiency = _number(v2_entry.get("entry_efficiency_score"))
    trend_age = _number(v2_entry.get("trend_age_bars"))
    pullbacks = _number(v2_entry.get("pullback_number"))
    bars_since_breakout = _number(v2_entry.get("bars_since_breakout"))
    ema_extension = _number(v2_entry.get("ema9_extension_atr"))
    vwap_extension = _number(v2_entry.get("vwap_extension_atr"))

    efficiency_component = efficiency * 0.35
    trend_component = max(0, 100 - max(0, trend_age - 2) * 12) * 0.20
    pullback_component = max(0, 100 - abs(pullbacks - 1) * 35) * 0.20
    breakout_component = max(0, 100 - max(0, bars_since_breakout - 2) * 15) * 0.10
    ema_component = max(0, 100 - ema_extension * 50) * 0.10
    vwap_component = max(0, 100 - vwap_extension * 40) * 0.05
    score = round(min(100, max(0, sum([
        efficiency_component,
        trend_component,
        pullback_component,
        breakout_component,
        ema_component,
        vwap_component,
    ]))), 2)

    reasons = []

    if pullbacks == 1:

        reasons.append("FIRST_PULLBACK")

    if trend_age > 8:

        reasons.append("MATURE_TREND")

    if ema_extension > 1 or vwap_extension > 1.5:

        reasons.append("EXTENDED_FROM_REFERENCE")

    if bars_since_breakout > 5:

        reasons.append("LATE_AFTER_BREAKOUT")

    if not reasons:

        reasons.append("BALANCED_ENTRY_LOCATION")

    return {
        "entry_timing_score": score,
        "entry_timing_grade": _grade(score),
        "entry_timing_reason": "; ".join(reasons),
    }