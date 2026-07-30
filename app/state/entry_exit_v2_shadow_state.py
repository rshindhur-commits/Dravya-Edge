from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.utils.json_store import load_json_file, save_json_file


ROOT_DIR = Path(__file__).resolve().parents[2]
SHADOW_STATE_FILE = ROOT_DIR / "app" / "state" / "entry_exit_v2_shadow_state.json"

# Buckets in which V1 is permitted to open a new position. A shadow is only
# evidence if it trades under the same constraints as the engine it is measured
# against.
SHADOW_ENTRY_BUCKETS = {"OPENING_RANGE", "AUTO_ENTRY_WINDOW"}


def shadow_entry_allowed(opened_at):
    """Whether V2 may open a shadow position at this time.

    V2 had no clock at all: open_shadow_trade() recorded whatever the scan handed
    it, at any hour. On 2026-07-30 that produced four entries at or after the
    bell, including V2's single best result -- INTC at +3.12R opened 17:07 ET, an
    hour after the market closed and at a price no order could have been filled
    at. Those trades inflated the V2 side of v2_learning_metrics and the V1/V2
    comparison, which is the evidence used to decide whether to promote V2.

    Stopping post-close scans removes the current source of these, but that is a
    cadence setting; SCAN_AFTER_CLOSE_MINUTES can be raised at any time. The
    constraint belongs on the engine, so it is enforced here rather than relying
    on nothing ever calling this late.
    """

    if opened_at is None:
        return True

    moment = opened_at

    if isinstance(moment, str):

        try:
            moment = datetime.fromisoformat(moment)
        except ValueError:
            # An unparseable timestamp is not evidence of a bad entry time, and
            # dropping a legitimate shadow trade is the worse error.
            return True

    if not isinstance(moment, datetime):
        return True

    from app.storage.auto_paper_decision_store import classify_decision_time

    bucket = classify_decision_time(moment).get("market_session")

    return bucket in SHADOW_ENTRY_BUCKETS


def load_shadow_trades():
    return load_json_file(str(SHADOW_STATE_FILE), {})


def save_shadow_trades(state):
    save_json_file(str(SHADOW_STATE_FILE), state)


def open_shadow_trade(symbol, entry_setup, risk_setup, opened_at):

    if not shadow_entry_allowed(opened_at):

        print(f"[V2 SHADOW] {symbol} entry refused at {opened_at}: outside entry window")
        return None

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
        "soft_exit_confirmations": [],
        "soft_exit_confirmation_streak": 0,
        "last_exit_confidence_score": None,
        "last_exit_health_state": None,
        "grace_zone_active": False,
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
    trade["soft_exit_confirmations"] = exit_setup.get(
        "soft_exit_confirmations",
        trade.get("soft_exit_confirmations", []),
    )
    trade["soft_exit_confirmation_streak"] = exit_setup.get(
        "soft_exit_confirmation_streak",
        trade.get("soft_exit_confirmation_streak", 0),
    )
    trade["last_exit_confidence_score"] = exit_setup.get(
        "exit_confidence_score"
    )
    trade["last_exit_health_state"] = exit_setup.get(
        "exit_health_state"
    )
    trade["grace_zone_active"] = (
        exit_setup.get("exit_phase") == "MONITOR"
    )
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
        "exit_confidence_score": exit_setup.get("exit_confidence_score"),
        "exit_health_state": exit_setup.get("exit_health_state"),
        "soft_exit_confirmations": exit_setup.get("soft_exit_confirmations"),
        "soft_exit_confirmation_streak": exit_setup.get(
            "soft_exit_confirmation_streak"
        ),
        "grace_zone_active": exit_setup.get("grace_zone_eligible"),
    })
    save_shadow_trades(state)
    return trade