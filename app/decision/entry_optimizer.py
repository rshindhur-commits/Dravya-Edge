from __future__ import annotations


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def evaluate_entry_optimizer(entry_diagnostics):
    """Rank a valid setup by remaining opportunity without changing eligibility."""
    diagnostics = entry_diagnostics or {}
    trend_age = _number(diagnostics.get("trend_age_bars"))
    pullbacks = _number(diagnostics.get("pullback_number"))
    bars_since_breakout = _number(diagnostics.get("bars_since_breakout"))
    ema_extension = _number(diagnostics.get("ema9_extension_atr"))
    vwap_extension = _number(diagnostics.get("vwap_extension_atr"))
    relative_volume = _number(diagnostics.get("relative_volume"))
    adx = _number(diagnostics.get("adx"))
    body_strength = _number(diagnostics.get("body_strength"))

    pullback_priority = 25 if pullbacks == 1 else 10 if pullbacks == 2 else -20
    trend_priority = 20 if trend_age <= 2 else 10 if trend_age <= 5 else -25 if trend_age > 6 else 0
    breakout_priority = 15 if bars_since_breakout <= 8 else -15 if bars_since_breakout > 20 else 0
    priority_adjustment = pullback_priority + trend_priority + breakout_priority

    expected_remaining_trend = 100
    expected_remaining_trend -= min(35, max(0, trend_age - 2) * 6)
    expected_remaining_trend -= 20 if pullbacks >= 3 else 8 if pullbacks == 2 else 0
    expected_remaining_trend -= min(20, ema_extension * 12)
    expected_remaining_trend -= min(15, vwap_extension * 8)
    expected_remaining_trend += 8 if relative_volume >= 1.5 else 3 if relative_volume >= 1.0 else -8
    expected_remaining_trend += 8 if adx >= 25 else 3 if adx >= 20 else -5
    expected_remaining_trend += 5 if body_strength >= 0.7 else 2 if body_strength >= 0.5 else -4
    expected_remaining_trend = round(max(0, min(100, expected_remaining_trend)), 2)

    projected_grade = (
        "A" if expected_remaining_trend >= 80
        else "B" if expected_remaining_trend >= 60
        else "C"
    )
    return {
        "entry_priority_adjustment": priority_adjustment,
        "expected_remaining_trend": expected_remaining_trend,
        "projected_entry_grade": projected_grade,
    }