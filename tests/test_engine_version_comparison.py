import pandas as pd

from app.analytics.engine_version_comparison import (
    build_daily_trend_outcomes,
    build_trade_comparisons,
    summarize_completed_comparisons,
)


def test_engine_trade_comparison_pairs_versions_and_calculates_deltas():
    comparisons = build_trade_comparisons(pd.DataFrame([
        {"engine_version": "v1", "symbol": "NVDA", "direction": "CALL", "entry_time": "2026-07-22T10:15:00", "entry_price": 211.2, "exit_time": "2026-07-22T15:10:00", "exit_price": 213.1, "final_r": 0.8, "mfe_r": 1.2, "tes": 68},
        {"engine_version": "v2", "symbol": "NVDA", "direction": "CALL", "entry_time": "2026-07-22T10:30:00", "entry_price": 210.65, "exit_time": "2026-07-22T15:42:00", "exit_price": 214.25, "final_r": 1.6, "mfe_r": 2.0, "tes": 84},
    ]))

    assert len(comparisons) == 1
    comparison = comparisons.iloc[0]
    assert comparison["entry_delta_minutes"] == 15
    assert comparison["exit_delta_minutes"] == 32
    assert comparison["final_r_delta"] == 0.8
    assert comparison["tes_delta"] == 16

    summary = summarize_completed_comparisons(comparisons)
    assert summary["Trades compared"] == 1
    assert summary["V2 higher R"] == 1
    assert summary["Avg R improvement"] == 0.8
    assert summary["Avg TES improvement"] == 16


def test_daily_trend_outcome_flags_failed_execution_in_strong_trend():
    outcomes = build_daily_trend_outcomes(pd.DataFrame([
        {"engine_version": "v1", "symbol": "NVDA", "direction": "CALL", "trade_direction": "CALL", "entry_price": 100, "stop_loss": 98, "exit_price": 97.6, "final_r": -1.2},
    ]), pd.DataFrame([{"Symbol": "NVDA", "Price": 103}]))

    outcome = outcomes.iloc[0]
    assert outcome["stock_direction"] == "BULLISH"
    assert outcome["stock_finish"] == "GREEN"
    assert outcome["trade_finish"] == "LOSS"
    assert outcome["trend_outcome"] == "STRONG_TREND_EXECUTION_FAILED"
    assert not outcome["engine_captured_trend"]