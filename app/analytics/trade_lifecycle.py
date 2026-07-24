from __future__ import annotations


def evaluate_trade_lifecycle(trade):
    trade = trade or {}
    action = str(trade.get("action_status") or trade.get("Action Status") or "").upper()
    if action in {"ENTER", "ENTER_PAPER"}: phase = "READY"
    elif str(trade.get("exit_phase") or "").upper() not in {"", "HOLD", "MONITOR"}: phase = "EXIT"
    elif trade.get("grace_zone_active"): phase = "PROTECTION"
    elif str(trade.get("last_exit_health_state") or "").upper() in {"WEAKENING", "AT_RISK", "FAILED"}: phase = "WEAKENING"
    elif float(trade.get("mfe_r") or 0) >= 1: phase = "EXPANSION"
    elif trade.get("entry_price") is not None: phase = "EARLY"
    else: phase = "SETUP"
    return {"phase": phase, "confidence": trade.get("last_exit_confidence_score"), "health": trade.get("last_trend_health_score"), "age": trade.get("bars_in_trade", 0), "momentum": trade.get("mfe_r", 0)}