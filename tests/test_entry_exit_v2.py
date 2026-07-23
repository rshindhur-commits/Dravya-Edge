import pandas as pd

from app.exit.exit_engine_v2 import evaluate_shadow_exit_v2
from app.strategies.entry_engine_v2 import evaluate_shadow_entry_v2


def _trend_frame(close=101, low=100, high=102):
    return pd.DataFrame([
        {
            "Close": close, "Low": low, "High": high, "EMA9": 100,
            "EMA20": 98, "VWAP": 99, "ATR": 2, "REL_VOLUME": 1.5,
            "MACD": 1, "MACD_SIGNAL": 0.5, "RSI": 62,
            "HIGHER_HIGH": True, "HIGHER_LOW": True, "BREAKOUT": False,
        }
        for _ in range(4)
    ])


def test_entry_v2_scores_first_pullback_independently_of_setup_quality():
    result = evaluate_shadow_entry_v2(
        _trend_frame(close=100.3, low=99.9, high=101),
        {"signal": "BULLISH"},
    )

    assert result["entry_efficiency_score"] >= 65
    assert result["suggested_entry"]
    assert result["pullback_number"] == 1


def test_entry_v2_can_propose_a_pullback_v1_did_not_enter():
    result = evaluate_shadow_entry_v2(
        _trend_frame(close=100.3, low=99.9, high=101),
        {"signal": "BULLISH"},
    )

    assert result["suggested_entry"]
    assert result["reason"] == "FIRST_PULLBACK_EFFICIENT"


def test_exit_v2_requires_confirmed_failure_for_soft_exit():
    result = evaluate_shadow_exit_v2(
        _trend_frame(),
        {"entry_price": 100, "stop_loss": 98, "take_profit": 104},
        {"entry_type": "EMA_PULLBACK"},
        {"highest_price": 102, "lowest_price": 99, "bars_in_trade": 4},
    )

    assert not result["exit_signal"]
    assert result["exit_phase"] == "HOLD"
    assert result["trend_health_status"] in {"STRONG", "HEALTHY"}


def test_exit_v2_preserves_hard_stop():
    result = evaluate_shadow_exit_v2(
        _trend_frame(close=97, low=97, high=99),
        {"entry_price": 100, "stop_loss": 98, "take_profit": 104},
        {"entry_type": "EMA_PULLBACK"},
        {"highest_price": 101, "lowest_price": 97, "bars_in_trade": 4},
    )

    assert result["exit_signal"]
    assert result["exit_phase"] == "HARD_STOP"
    assert result["bars_in_trade"] == 5


def test_exit_v2_monitors_a_single_healthy_ema_loss():
    frame = _trend_frame(close=99, low=98.5, high=100)
    frame["VWAP"] = 98
    result = evaluate_shadow_exit_v2(
        frame,
        {"entry_price": 100, "stop_loss": 96, "take_profit": 108},
        {"entry_type": "EMA_PULLBACK"},
        {"highest_price": 107, "lowest_price": 99, "bars_in_trade": 4},
    )

    assert not result["exit_signal"]
    assert result["exit_phase"] == "MONITOR"
    assert result["grace_zone_eligible"]
    assert result["soft_exit_confirmation_streak"] == 1