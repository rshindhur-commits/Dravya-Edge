import json

import pandas as pd

from app.analytics.activity_trace import (
    build_activity_trace,
    persist_activity_trace,
    write_daily_activity_trace,
)


def test_activity_trace_captures_scanner_and_auto_paper_events(tmp_path, monkeypatch):
    pd.DataFrame([{
        "timestamp": "2026-07-28 10:00:00",
        "symbol": "AAPL",
        "decision": "BLOCKED",
        "reason": "not top candidate",
        "scan_id": "2026-07-28_100000",
    }]).to_csv(tmp_path / "auto_paper_decisions.csv", index=False)
    monkeypatch.setattr(
        "app.analytics.activity_trace.live_path",
        lambda _name: tmp_path / "telegram_dispatch_audit.jsonl",
    )
    trace = build_activity_trace(
        "2026-07-28",
        scanner_rows=[{
            "Symbol": "AAPL",
            "Action Status": "ENTER_PAPER",
            "Action Reason": "Risk passed",
            "Current ET": "2026-07-28 09:59:00",
        }],
        scan_id="2026-07-28_100000",
        directory=tmp_path,
    )

    assert {"Scanner decision", "Auto-paper gate", "Rule evaluation"}.issubset(
        set(trace["origin"])
    )
    assert {"Scanner", "Paper"}.issubset(set(trace["category"]))
    assert {"ENTER PAPER", "BLOCKED"}.issubset(set(trace["event"]))


def test_activity_trace_emits_rule_pass_and_failure_events():
    trace = build_activity_trace(
        "2026-07-28",
        scanner_rows=[{
            "Symbol": "AAPL",
            "Entry": "EMA_PULLBACK",
            "Action Status": "ENTER_PAPER",
            "Setup %": 85,
            "Candidate RR": 1.5,
            "Option Quality Score": 80,
            "Option Spread %": 4,
            "Option Quote Freshness": "LIVE_QUOTE",
            "Affordable": True,
            "Current ET": "2026-07-28 10:00:00",
        }],
        scan_id="2026-07-28_100000",
        directory=".",
    )

    rules = trace[trace["origin"] == "Rule evaluation"]
    assert "RULE FAILED" in set(rules["event"])
    assert "RR" in set(rules["rule"])
    rr = rules[rules["rule"] == "RR"].iloc[0]
    assert not bool(rr["passed"])
    assert rr["actual"] == 1.5


def test_activity_trace_records_decision_candle_and_runtime_values():
    trace = build_activity_trace(
        "2026-07-28",
        scanner_rows=[{
            "Symbol": "AAPL",
            "Action Status": "ENTER_PAPER",
            "Setup %": 85,
            "Candidate RR": 2.4,
            "Option Quality Score": 88,
            "Decision Candle Time ET": "2026-07-28T10:00:00-04:00",
            "Decision Candle Open": 210.1,
            "Decision Candle High": 211.0,
            "Decision Candle Low": 209.9,
            "Decision Candle Close": 210.8,
            "Decision Candle Volume": 123456,
        }],
        scan_id="2026-07-28_100000",
        directory=".",
    )

    decision = trace[trace["origin"] == "Scanner decision"].iloc[0]
    assert decision["setup_score"] == 85
    assert decision["rr"] == 2.4
    assert decision["option_quality"] == 88
    assert decision["candle_close"] == 210.8
    assert decision["candle_time"] == "2026-07-28T10:00:00-04:00"


def test_activity_trace_marks_action_state_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.analytics.activity_trace.daily_path",
        lambda *_args: tmp_path / "activity_trace.csv",
    )

    write_daily_activity_trace(
        "2026-07-28",
        scanner_rows=[{"Symbol": "AAPL", "Action Status": "AVOID", "Current ET": "2026-07-28 09:55:00"}],
    )
    result = write_daily_activity_trace(
        "2026-07-28",
        scanner_rows=[{"Symbol": "AAPL", "Action Status": "ENTER_PAPER", "Current ET": "2026-07-28 10:00:00"}],
    )

    decisions = [event for event in result["events"] if event["origin"] == "Scanner decision"]
    latest = next(event for event in decisions if event["event"] == "ENTER PAPER")
    assert latest["previous_state"] == "AVOID"
    assert latest["state_changed"] is True


def test_activity_trace_persistence_uses_stable_event_ids(monkeypatch):
    persisted = []

    class Repository:
        def batch_upsert(self, events):
            persisted.extend(events)
            return len(events)

    monkeypatch.setattr(
        "app.db.activity_trace_repository.ActivityTraceRepository",
        Repository,
    )

    events = [{"event_id": "trace-123", "event": "RULE FAILED"}]
    assert persist_activity_trace(events) == 1
    assert persisted == events