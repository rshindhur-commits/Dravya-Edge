from __future__ import annotations

import pandas as pd


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _direction(signal):
    return "PUT" if "BEAR" in str(signal or "").upper() else "CALL"


def _trend_age_bars(df, direction):
    if df is None or df.empty:
        return 0
    age = 0
    for _, bar in df.iloc[::-1].iterrows():
        close = _number(bar.get("Close"))
        ema9 = _number(bar.get("EMA9"))
        ema20 = _number(bar.get("EMA20"))
        vwap = _number(bar.get("VWAP"))
        aligned = (
            close > vwap and ema9 > ema20
            if direction == "CALL"
            else close < vwap and ema9 < ema20
        )
        if not aligned:
            break
        age += 1
    return age


def _pullback_count(df, direction):
    if df is None or df.empty:
        return 0
    count = 0
    in_pullback = False
    for _, bar in df.tail(20).iterrows():
        atr = max(_number(bar.get("ATR")), 0.01)
        ema9 = _number(bar.get("EMA9"))
        threshold = atr * 0.25
        touched = (
            _number(bar.get("Low")) <= ema9 + threshold
            if direction == "CALL"
            else _number(bar.get("High")) >= ema9 - threshold
        )
        if touched and not in_pullback:
            count += 1
        in_pullback = touched
    return count


def _bars_since_breakout(df, direction):
    if df is None or df.empty:
        return 0
    field = "BREAKOUT" if direction == "CALL" else "BREAKDOWN"
    for offset, (_, bar) in enumerate(df.iloc[::-1].iterrows()):
        if bool(bar.get(field, False)):
            return offset
    return len(df)


def evaluate_shadow_entry_v2(df, analysis):
    """Score entry location without changing the V1 entry decision."""
    if df is None or df.empty:
        return {"suggested_entry": False, "entry_efficiency_score": 0, "reason": "NO_MARKET_DATA"}

    latest = df.iloc[-1]
    direction = _direction((analysis or {}).get("signal"))
    atr = max(_number(latest.get("ATR")), 0.01)
    close = _number(latest.get("Close"))
    ema9 = _number(latest.get("EMA9"))
    ema20 = _number(latest.get("EMA20"))
    vwap = _number(latest.get("VWAP"))
    ema_extension_atr = abs(close - ema9) / atr
    vwap_extension_atr = abs(close - vwap) / atr
    distance_from_ema9_pct = (close - ema9) / ema9 * 100 if ema9 else None
    distance_from_ema20_pct = (close - ema20) / ema20 * 100 if ema20 else None
    distance_from_vwap_pct = (close - vwap) / vwap * 100 if vwap else None
    trend_age = _trend_age_bars(df, direction)
    pullback_number = _pullback_count(df, direction)
    bars_since_breakout = _bars_since_breakout(df, direction)
    rel_volume = _number(latest.get("REL_VOLUME"))
    score = 100
    score -= min(40, round(ema_extension_atr * 30))
    score -= min(25, round(vwap_extension_atr * 12))
    score -= min(20, max(0, trend_age - 3) * 3)
    score -= min(15, max(0, pullback_number - 1) * 7)
    score -= 10 if rel_volume < 1 else 0
    score += 5 if rel_volume >= 1.5 else 0
    score = max(0, min(100, int(score)))
    is_first_pullback = pullback_number == 1
    signal = str((analysis or {}).get("signal") or "").upper()
    directional_signal = signal in {
        "BULLISH",
        "HIGH CONVICTION BULLISH",
        "BEARISH",
        "HIGH CONVICTION BEARISH",
    }
    suggested_entry = (
        directional_signal
        and is_first_pullback
        and score >= 65
        and trend_age <= 8
    )
    reason = (
        "FIRST_PULLBACK_EFFICIENT"
        if suggested_entry and is_first_pullback
        else "ENTRY_EFFICIENCY_ACCEPTED"
        if suggested_entry
        else "LATE_TREND_OR_EXTENSION"
    )
    return {
        "suggested_entry": suggested_entry,
        "direction": direction,
        "entry_type": "EMA_REJECTION_SHORT" if direction == "PUT" else "EMA_PULLBACK",
        "trend_age_bars": trend_age,
        "pullback_number": pullback_number,
        "bars_since_breakout": bars_since_breakout,
        "ema9_extension_atr": round(ema_extension_atr, 3),
        "vwap_extension_atr": round(vwap_extension_atr, 3),
        "distance_from_ema9_pct": round(distance_from_ema9_pct, 3) if distance_from_ema9_pct is not None else None,
        "distance_from_ema20_pct": round(distance_from_ema20_pct, 3) if distance_from_ema20_pct is not None else None,
        "distance_from_vwap_pct": round(distance_from_vwap_pct, 3) if distance_from_vwap_pct is not None else None,
        "atr_extension": round(max(ema_extension_atr, vwap_extension_atr), 3),
        "ema_alignment_score": 100 if (latest.get("EMA9") > latest.get("EMA20") if direction == "CALL" else latest.get("EMA9") < latest.get("EMA20")) else 0,
        "volume_confirmation": rel_volume >= 1.0,
        "volume_confirmation_score": min(100, round(rel_volume * 50)),
        "entry_efficiency_score": score,
        "reason": reason,
    }