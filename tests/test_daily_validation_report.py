import pandas as pd

from tools.daily_validation_report import (
    build_data_quality_checks,
    build_quote_diagnostics,
    build_state_reconciliation,
)


def test_opened_trade_count_uses_scorecard_source_precedence():
    checks = build_data_quality_checks(
        scanner_df=pd.DataFrame(),
        candidate_df=pd.DataFrame(),
        suggested_trade_state={},
        decision_log=[{"decision": "OPENED"}, {"decision": "OPENED"}],
        paper_events_df=pd.DataFrame([
            {"event_type": "OPEN", "trade_key": "paper-1"},
        ]),
        paper_trade_state={
            "paper-1": {},
            "paper-2": {},
            "paper-3": {},
        },
    )
    values = dict(zip(checks["Check"], checks["Count"]))

    assert values["Actual opened trades count"] == 1
    assert values["Actual opened trades source"] == "paper_trade_events"
    assert values["Opened count from auto_paper_decision_log"] == 2
    assert values["Opened count from paper_trade_state"] == 3


def test_state_reconciliation_reports_statuses_and_mismatches():
    reconciliation, warnings = build_state_reconciliation(
        paper_trade_state={"paper-1": {"status": "OPEN"}},
        trade_state={"NVDA": {"status": "OPEN"}},
        suggested_trade_state={"suggestion-1": {"status": "PROMOTED_TO_PAPER"}},
        paper_events_df=pd.DataFrame([
            {"event_type": "OPEN", "trade_key": "paper-1"},
            {"event_type": "OPEN", "trade_key": "paper-2"},
        ]),
        lifecycle_events_df=pd.DataFrame([{"symbol": "NVDA"}]),
        lifecycle_transitions_df=pd.DataFrame(),
    )

    rows = reconciliation.to_dict(orient="records")
    assert {"Source": "suggested_trade_state", "State": "PROMOTED_TO_PAPER", "Count": 1} in rows
    assert any("Paper OPEN state count" in warning for warning in warnings)
    assert any("Lifecycle observations exist" in warning for warning in warnings)


def test_quote_diagnostics_reports_freshness_rejection_details():
    diagnostics = build_quote_diagnostics(
        scanner_df=pd.DataFrame([{
            "Symbol": "AAPL",
            "Option Quote Freshness": "STALE_QUOTE",
            "Option Quote Timestamp": "2026-07-22T13:41:28+00:00",
            "Option Quote Timestamp Field": "sip_timestamp",
            "Option Quote Checked At": "2026-07-22T13:42:11+00:00",
            "Option Quote Age Seconds": 43,
            "Option Quote Allowed Age Seconds": 30,
            "Option Quote Freshness Reason": "AGE_EXCEEDS_ALLOWED_AGE",
        }]),
        candidate_df=pd.DataFrame(),
        lifecycle_events_df=pd.DataFrame(),
    )

    assert diagnostics.to_dict(orient="records") == [{
        "Source": "scanner_output",
        "Symbol": "AAPL",
        "Quote Timestamp": "2026-07-22T13:41:28+00:00",
        "Timestamp Field": "sip_timestamp",
        "Current Time": "2026-07-22T13:42:11+00:00",
        "Age (sec)": 43,
        "Threshold (sec)": 30,
        "Decision": "STALE_QUOTE",
        "Reason": "AGE_EXCEEDS_ALLOWED_AGE",
    }]