import pandas as pd

from app.analytics.v2_learning_dataset import build_learning_record
from app.analytics.v2_learning_writer import summarize_learning_dataset


def test_learning_record_captures_execution_specific_metrics():
    record = build_learning_record("2026-07-22", {
        "engine_version": "v2", "symbol": "NVDA", "direction": "CALL",
        "entry_type": "EMA_PULLBACK", "opened_at": "2026-07-22T10:00:00",
        "closed_at": "2026-07-22T11:00:00", "entry_price": 100,
        "close_price": 102, "stop_loss": 98, "risk_reward": 2,
        "trend_age": 1, "pullback_number": 1, "bars_since_breakout": 2,
        "entry_efficiency_score": 84, "distance_from_ema9_pct": 0.2,
        "distance_from_vwap_pct": 0.4, "distance_from_ema20_pct": 0.8,
        "atr_extension": 0.3, "ema_alignment_score": 100,
        "volume_confirmation_score": 80, "max_trend_health": 90,
        "min_trend_health": 55, "trend_health_sum": 220,
        "trend_health_sum_sq": 16400, "trend_health_samples": 3,
        "trend_health_at_exit": 55, "mfe_r": 1.8, "mae_r": 0.4,
        "bars_in_trade": 4, "final_r": 1.0, "exit_phase": "TREND_FAILURE",
    })

    assert record["trend_capture_pct"] == 55.56
    assert record["entry_quality_label"] == "GOOD"
    assert record["execution_quality"] == "B"
    assert record["time_held_minutes"] == 60.0


def test_learning_summary_uses_execution_metrics():
    summary = summarize_learning_dataset(pd.DataFrame([
        {"trend_age": 1, "entry_efficiency_score": 80, "trend_health_at_exit": 60, "max_mfe_r": 2, "max_mae_r": 0.5, "trend_capture_pct": 70, "tes": 85},
    ]))

    assert summary["Completed learning records"] == 1
    assert summary["Avg Trend Capture %"] == 70