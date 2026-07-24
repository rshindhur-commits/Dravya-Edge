from __future__ import annotations


def evaluate_market_regime(regime, breadth=None, volatility=None):
    raw = str(regime or "UNKNOWN").upper()
    breadth_value = float(breadth or 0)
    mapping = {"TRENDING_BULL": "STRONG_BULL", "TRENDING_BEAR": "BEAR", "RANGE_BOUND": "RANGE", "HIGH_VOLATILITY": "VOLATILE", "LOW_VOLATILITY": "RANGE"}
    state = mapping.get(raw, "RISK_OFF" if raw == "UNKNOWN" else raw)
    risk_mode = "AGGRESSIVE" if state == "STRONG_BULL" and breadth_value >= 0 else "DEFENSIVE" if state in {"BEAR", "RISK_OFF", "VOLATILE"} else "NORMAL"
    return {"state": state, "confidence": min(100, round(50 + abs(breadth_value) * 10, 1)), "strength": breadth_value, "volatility": volatility or raw, "breadth": breadth_value, "risk_mode": risk_mode}