from unittest.mock import patch

from app.state import entry_exit_v2_shadow_state as shadow_state


def test_v2_shadow_trade_state_is_independent_and_tracks_exit():
    state = {}

    with patch.object(shadow_state, "load_shadow_trades", side_effect=lambda: state), patch.object(
        shadow_state, "save_shadow_trades", side_effect=lambda value: state.update(value)
    ):
        trade = shadow_state.open_shadow_trade(
            "NVDA",
            {"direction": "CALL", "entry_type": "EMA_PULLBACK", "reason": "FIRST_PULLBACK_EFFICIENT", "entry_efficiency_score": 82},
            {"entry_price": 100, "stop_loss": 98, "take_profit": 104, "risk_reward": 2},
            "2026-07-22T10:00:00-04:00",
        )
        shadow_state.update_shadow_trade("NVDA", {
            "highest_price": 103,
            "lowest_price": 99,
            "bars_in_trade": 3,
            "mfe_r": 1.5,
            "trend_health_score": 75,
            "trend_health_status": "HEALTHY",
            "exit_phase": "HOLD",
        })
        closed = shadow_state.close_shadow_trade("NVDA", {
            "exit_phase": "TREND_FAILURE",
            "rr_progress": 1.2,
            "mfe_r": 1.5,
            "trend_health_score": 42,
            "trend_health_status": "WEAKENING",
        }, "2026-07-22T11:00:00-04:00", 102.4)

    assert trade["engine_version"] == "v2"
    assert closed["status"] == "CLOSED"
    assert closed["exit_phase"] == "TREND_FAILURE"
    assert closed["final_r"] == 1.2
    assert "NVDA" not in state