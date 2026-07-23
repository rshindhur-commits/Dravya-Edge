from datetime import datetime, timezone

import pandas as pd

from app.analytics.entry_exit_v2_shadow import (
    build_shadow_rows,
    build_engine_differences,
    summarize_shadow_comparison,
)


def test_shadow_rows_preserve_v1_v2_comparison_fields():

    rows = build_shadow_rows(
        [
            {
                "Symbol": "NVDA",
                "Entry": "EMA_PULLBACK",
                "Entry Quality": "HIGH",
                "V2 Entry Suggested": True,
                "V2 Entry Efficiency Score": 82,
                "V2 Trend Age Bars": 2,
                "V2 Pullback Number": 1,
                "V2 Bars Since Breakout": 3,
                "V2 EMA9 Extension ATR": 0.2,
                "V2 VWAP Extension ATR": 0.5,
                "V2 Entry Reason": "FIRST_PULLBACK_EFFICIENT",
                "Live Exit Signal": False,
                "Live Exit Reason": "Hold",
                "V2 Exit Signal": False,
                "V2 Exit Phase": "HOLD",
                "V2 Trend Health Score": 85,
                "V2 Trend Health Status": "STRONG",
                "V2 Trend Failure Confirmed": False,
                "V2 MFE R": 1.4,
                "V2 RR Progress": 0.8,
            }
        ],
        "2026-07-22",
        "2026-07-22_100000",
        datetime(2026, 7, 22, 10, tzinfo=timezone.utc)
    )

    assert len(rows) == 1
    record = rows.iloc[0]
    assert record["v1_entry_type"] == "EMA_PULLBACK"
    assert record["v2_entry_efficiency_score"] == 82
    assert record["v2_exit_phase"] == "HOLD"


def test_shadow_summary_reports_entry_exit_disagreements():

    summary, phases = summarize_shadow_comparison(pd.DataFrame([
        {
            "v2_suggested_entry": True,
            "v1_exit_signal": False,
            "v2_exit_signal": True,
            "v2_exit_phase": "TREND_FAILURE",
            "v2_entry_efficiency_score": 80,
            "v2_mfe_r": 1.5,
        },
        {
            "v2_suggested_entry": False,
            "v1_exit_signal": True,
            "v2_exit_signal": True,
            "v2_exit_phase": "HARD_STOP",
            "v2_entry_efficiency_score": 60,
            "v2_mfe_r": 0.2,
        },
    ]))

    assert summary["Shadow rows"] == 2
    assert summary["Exit disagreements"] == 1
    assert set(phases["V2 Exit Phase"]) == {"TREND_FAILURE", "HARD_STOP"}


def test_engine_differences_capture_independent_v2_entry():

    differences = build_engine_differences(build_shadow_rows(
        [{
            "Symbol": "MRVL",
            "Entry": "NO_ENTRY",
            "V2 Entry Suggested": True,
            "V2 Entry Efficiency Score": 87,
            "V2 Entry Reason": "FIRST_PULLBACK_EFFICIENT",
            "Live Exit Signal": False,
            "V2 Exit Signal": False,
        }],
        "2026-07-22",
        "scan-1",
        datetime(2026, 7, 22, 10, tzinfo=timezone.utc),
    ))

    assert differences.iloc[0]["stage"] == "ENTRY"
    assert differences.iloc[0]["v2_decision"] == "ENTER"