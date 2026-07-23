from __future__ import annotations

from pathlib import Path

from app.utils.json_store import load_json_file, save_json_file


ROOT_DIR = Path(__file__).resolve().parents[2]
SHADOW_STATE_FILE = ROOT_DIR / "app" / "state" / "entry_exit_v2_shadow_state.json"


def load_shadow_trades():
    return load_json_file(str(SHADOW_STATE_FILE), {})


def save_shadow_trades(state):
    save_json_file(str(SHADOW_STATE_FILE), state)


def open_shadow_trade(symbol, entry_setup, risk_setup, opened_at):
    state = load_shadow_trades()
    entry_price = risk_setup.get("entry_price")
    state[symbol] = {
        "engine_version": "v2",
        "status": "OPEN",
        "symbol": symbol,
        "direction": entry_setup.get("direction"),
        "entry_type": entry_setup.get("entry_type"),
        "entry_reason": entry_setup.get("reason"),
        "entry_efficiency_score": entry_setup.get("entry_efficiency_score"),
        "trend_age": entry_setup.get("trend_age_bars"),
        "pullback_number": entry_setup.get("pullback_number"),
        "bars_since_breakout": entry_setup.get("bars_since_breakout"),
        "distance_from_ema9_pct": entry_setup.get("distance_from_ema9_pct"),
        "distance_from_ema20_pct": entry_setup.get("distance_from_ema20_pct"),
        "distance_from_vwap_pct": entry_setup.get("distance_from_vwap_pct"),
        "atr_extension": entry_setup.get("atr_extension"),
        "ema_alignment_score": entry_setup.get("ema_alignment_score"),
        "volume_confirmation_score": entry_setup.get("volume_confirmation_score"),
        "opened_at": opened_at,
        "entry_price": entry_price,
        "stop_loss": risk_setup.get("stop_loss"),
        "take_profit": risk_setup.get("take_profit"),
        "risk_reward": risk_setup.get("risk_reward"),
        "highest_price": entry_price,
        "lowest_price": entry_price,
        "bars_in_trade": 0,
        "mfe_r": 0.0,
        "mae_r": 0.0,
        "max_trend_health": None,
        "min_trend_health": None,
        "trend_health_sum": 0.0,
        "trend_health_sum_sq": 0.0,
        "trend_health_samples": 0,
    }
    save_shadow_trades(state)
    return state[symbol]


def update_shadow_trade(symbol, exit_setup):
    state = load_shadow_trades()
    trade = state.get(symbol)
    if not trade:
        return None
    trade["highest_price"] = exit_setup.get("highest_price", trade.get("highest_price"))
    trade["lowest_price"] = exit_setup.get("lowest_price", trade.get("lowest_price"))
    trade["bars_in_trade"] = exit_setup.get("bars_in_trade", trade.get("bars_in_trade", 0))
    trade["mfe_r"] = exit_setup.get("mfe_r", trade.get("mfe_r"))
    trade["mae_r"] = max(
        float(trade.get("mae_r") or 0),
        float(exit_setup.get("mae_r") or 0),
    )
    health = exit_setup.get("trend_health_score")
    if health is not None:
        health = float(health)
        samples = int(trade.get("trend_health_samples") or 0) + 1
        trade["trend_health_samples"] = samples
        trade["trend_health_sum"] = float(trade.get("trend_health_sum") or 0) + health
        trade["trend_health_sum_sq"] = float(trade.get("trend_health_sum_sq") or 0) + health * health
        trade["max_trend_health"] = health if trade.get("max_trend_health") is None else max(float(trade["max_trend_health"]), health)
        trade["min_trend_health"] = health if trade.get("min_trend_health") is None else min(float(trade["min_trend_health"]), health)
    trade["last_trend_health_score"] = exit_setup.get("trend_health_score")
    trade["last_trend_health_status"] = exit_setup.get("trend_health_status")
    trade["last_exit_phase"] = exit_setup.get("exit_phase")
    state[symbol] = trade
    save_shadow_trades(state)
    return trade


def close_shadow_trade(symbol, exit_setup, closed_at, close_price):
    state = load_shadow_trades()
    trade = state.pop(symbol, None)
    if not trade:
        return None
    trade.update({
        "status": "CLOSED",
        "closed_at": closed_at,
        "close_price": close_price,
        "exit_phase": exit_setup.get("exit_phase"),
        "final_r": exit_setup.get("rr_progress"),
        "mfe_r": exit_setup.get("mfe_r", trade.get("mfe_r")),
        "mae_r": exit_setup.get("mae_r", trade.get("mae_r")),
        "trend_health_score": exit_setup.get("trend_health_score"),
        "trend_health_status": exit_setup.get("trend_health_status"),
        "trend_health_at_exit": exit_setup.get("trend_health_score"),
    })
    save_shadow_trades(state)
    return trade