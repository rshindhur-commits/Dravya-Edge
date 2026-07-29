from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.analytics.candidate_evidence import (
    build_candidate_evidence,
    build_candidate_evidence_from_frames,
    write_candidate_evidence,
)
from app.db.candidate_evidence_repository import CandidateEvidenceRepository


def test_candidate_evidence_collapses_scans_and_joins_outcomes():
    evidence = build_candidate_evidence_from_frames(
        "2026-07-22",
        pd.DataFrame([
            {
                "symbol": "AAPL", "direction": "CALL", "setup_type": "EMA_PULLBACK",
                "scan_timestamp": "2026-07-22 10:00:00", "candidate_rr": 2.1,
                "setup_percent": 75, "action_status": "REVIEW_TV_CHART",
                "entry_timing_score": 64, "trade_quality_score": 72, "candidate_rank": 2,
                "option_quote_freshness": "LIVE_QUOTE",
            },
            {
                "symbol": "AAPL", "direction": "CALL", "setup_type": "EMA_PULLBACK",
                "scan_timestamp": "2026-07-22 10:05:00", "candidate_rr": 2.4,
                "setup_percent": 82, "action_status": "ENTER_PAPER",
                "entry_timing_score": 88, "entry_timing_grade": "EXCELLENT",
                "trade_quality_score": 91, "entry_priority_adjustment": 35,
                "expected_remaining_trend": 86, "projected_entry_grade": "A",
                "ranking_score": 126, "candidate_rank": 1,
                "replay_outcome": "TARGET_FIRST", "top_candidate": "BULLISH_TOP_1",
                "option_quote_freshness": "LIVE_QUOTE",
            },
        ]),
        suggestions={"aapl": {"symbol": "AAPL", "direction": "CALL", "setup_type": "EMA_PULLBACK", "status": "PROMOTED_TO_PAPER"}},
        paper_events=pd.DataFrame([{"symbol": "AAPL", "direction": "CALL", "event_type": "OPEN", "status": "OPEN"}]),
        auto_paper_decisions=pd.DataFrame([{"symbol": "AAPL", "decision": "OPENED"}]),
        trend_capture=pd.DataFrame([{"Symbol": "AAPL", "Direction": "CALL", "Setup": "EMA_PULLBACK", "Trend Capture %": 72, "Trade Efficiency Score": 88}]),
        attribution=pd.DataFrame([{"symbol": "AAPL", "setup": "EMA_PULLBACK", "root_cause": "STALE_QUOTE"}]),
    )

    assert len(evidence) == 1
    row = evidence.iloc[0]
    assert row["scan_count"] == 2
    assert row["rr"] == 2.4
    assert row["setup_score"] == 82
    assert row["entry_timing_score"] == 88
    assert row["entry_timing_grade"] == "EXCELLENT"
    assert row["trade_quality_score"] == 91
    assert row["entry_priority_adjustment"] == 35
    assert row["expected_remaining_trend"] == 86
    assert row["projected_entry_grade"] == "A"
    assert row["ranking_score"] == 126
    assert row["candidate_rank"] == 1
    assert row["entered"]
    assert row["auto_paper_decision"] == "OPENED"
    assert row["target_first"]
    assert row["winner"]
    assert row["suggestion_status"] == "PROMOTED_TO_PAPER"
    assert row["trend_capture"] == 72
    assert row["tes"] == 88


def test_candidate_evidence_preserves_actionable_decision_after_later_wait():
    evidence = build_candidate_evidence_from_frames(
        "2026-07-29",
        pd.DataFrame([
            {
                "symbol": "NFLX", "direction": "CALL", "setup_type": "BREAKOUT",
                "scan_timestamp": "2026-07-29 10:03:00", "scan_id": "scan-enter",
                "action_status": "ENTER_PAPER", "action_reason": "Risk, option, and timing checks passed",
            },
            {
                "symbol": "NFLX", "direction": "CALL", "setup_type": "BREAKOUT",
                "scan_timestamp": "2026-07-29 10:08:00", "scan_id": "scan-wait",
                "action_status": "WAIT", "action_reason": "No actionable entry trigger",
            },
        ]),
    )

    row = evidence.iloc[0]
    assert row["decision"] == "ENTER_PAPER"
    assert row["latest_decision"] == "WAIT"
    assert row["first_actionable_decision"] == "ENTER_PAPER"
    assert row["first_actionable_scan_id"] == "scan-enter"
    assert [item["decision"] for item in row["decision_history"]] == ["ENTER_PAPER", "WAIT"]


def test_evidence_repository_normalizes_missing_actionable_timestamp(monkeypatch):
    captured = []
    repository = CandidateEvidenceRepository()
    monkeypatch.setattr(
        repository,
        "_batch_execute",
        lambda _statement, records: captured.extend(records) or len(records),
    )

    repository.batch_upsert([{
        "candidate_id": "candidate-1",
        "first_actionable_at": pd.NaT,
        "option_quality": float("nan"),
        "decision_history": [],
    }])

    assert captured[0]["first_actionable_at"] is None
    assert 'NaN' not in captured[0]["payload"]


def test_writer_uses_finalized_candidates_without_rereading_snapshots(tmp_path):
    daily_dir = tmp_path / "2026-07-27"

    def fake_daily_path(_trading_day, filename):
        daily_dir.mkdir(parents=True, exist_ok=True)
        return daily_dir / filename

    candidates = pd.DataFrame([
        {
            "Symbol": "NVDA",
            "Candidate Direction": "PUT",
            "Entry": "VWAP_REJECTION",
            "Data Timestamp ET": "2026-07-27 10:00:00",
            "Action Status": "BLOCKED",
        },
        {
            "Symbol": "AAPL",
            "Candidate Direction": "CALL",
            "Entry": "EMA_PULLBACK",
            "Data Timestamp ET": "2026-07-27 10:00:00",
            "Action Status": "REVIEW_TV_CHART",
        },
    ])

    with patch(
        "app.analytics.candidate_evidence.daily_path",
        side_effect=fake_daily_path,
    ), patch(
        "app.analytics.candidate_evidence.db_writes_enabled",
        return_value=False,
    ), patch(
        "app.db.candidate_evidence_repository.CandidateEvidenceRepository.batch_upsert",
    ) as batch_upsert:
        result = write_candidate_evidence(
            "2026-07-27",
            candidate_snapshots=candidates,
        )

    assert result["rows"] == 2
    assert Path(result["path"]).exists()
    assert Path(result["status_path"]).exists()
    assert result["status"]["database_status"] == "DISABLED"
    assert result["status"]["database_rows"] == 0
    assert result["status"]["rows_expected"] == 2
    assert result["status"]["rows_written"] == 2
    assert result["status"]["duplicates_removed"] == 0
    assert result["status"]["db_rows_persisted"] == 0
    batch_upsert.assert_not_called()


def test_writer_records_failed_database_promotion(tmp_path):
    daily_dir = tmp_path / "2026-07-28"

    def fake_daily_path(_trading_day, filename):
        daily_dir.mkdir(parents=True, exist_ok=True)
        return daily_dir / filename

    candidates = pd.DataFrame([
        {
            "Symbol": "AAPL",
            "Candidate Direction": "CALL",
            "Entry": "EMA_PULLBACK",
            "Data Timestamp ET": "2026-07-28 10:00:00",
            "Action Status": "REVIEW_TV_CHART",
        },
    ])

    with patch(
        "app.analytics.candidate_evidence.daily_path",
        side_effect=fake_daily_path,
    ), patch(
        "app.analytics.candidate_evidence.db_writes_enabled",
        return_value=True,
    ), patch(
        "app.analytics.candidate_evidence._read_database_snapshots",
        return_value=pd.DataFrame(),
    ), patch(
        "app.db.candidate_evidence_repository.CandidateEvidenceRepository.batch_upsert",
        return_value=0,
    ):
        result = write_candidate_evidence(
            "2026-07-28",
            candidate_snapshots=candidates,
        )

    assert result["rows"] == 1
    assert Path(result["path"]).exists()
    assert Path(result["status_path"]).exists()
    assert result["status"]["database_status"] == "FAILED"
    assert result["status"]["database_rows"] == 0
    assert result["status"]["rows_expected"] == 1
    assert result["status"]["rows_written"] == 1
    assert result["status"]["db_rows_persisted"] == 0


def test_builder_prefers_accumulated_daily_snapshots_over_latest_scan(monkeypatch):
    accumulated = pd.DataFrame([
        {
            "Symbol": "AAPL", "Candidate Direction": "CALL", "Entry": "EMA_PULLBACK",
            "scan_timestamp": "2026-07-28 10:00:00", "Candidate RR": 2.0,
        },
        {
            "Symbol": "AAPL", "Candidate Direction": "CALL", "Entry": "EMA_PULLBACK",
            "scan_timestamp": "2026-07-28 10:05:00", "Candidate RR": 2.5,
        },
    ])
    latest_scan = accumulated.tail(1).copy()
    monkeypatch.setattr(
        "app.analytics.candidate_evidence._read_snapshot",
        lambda _day: accumulated,
    )
    monkeypatch.setattr(
        "app.analytics.candidate_evidence._read_database_snapshots",
        lambda _day: pd.DataFrame(),
    )
    monkeypatch.setattr(
        "app.analytics.candidate_evidence.load_json_file",
        lambda *_args: {},
    )
    monkeypatch.setattr(
        "app.analytics.candidate_evidence.build_loss_attribution",
        lambda _day: pd.DataFrame(),
    )

    evidence = build_candidate_evidence(
        "2026-07-28",
        candidate_snapshots=latest_scan,
    )

    assert len(evidence) == 1
    assert evidence.iloc[0]["scan_count"] == 2
    assert evidence.iloc[0]["rr"] == 2.5


def test_builder_merges_durable_and_current_snapshots_for_decision_lineage(monkeypatch):
    durable = pd.DataFrame([{
        "symbol": "NFLX", "direction": "PUT", "setup_type": "VWAP_REJECTION",
        "scan_timestamp": "2026-07-29 10:03:00", "scan_id": "scan-enter",
        "action_status": "ENTER_PAPER",
    }])
    current = pd.DataFrame([{
        "Symbol": "NFLX", "Candidate Direction": "PUT", "Entry": "VWAP_REJECTION",
        "Current ET": "2026-07-29 10:08:00", "Scan ID": "scan-wait",
        "Action Status": "WAIT",
    }])
    monkeypatch.setattr("app.analytics.candidate_evidence._read_database_snapshots", lambda _day: durable)
    monkeypatch.setattr("app.analytics.candidate_evidence._read_snapshot", lambda _day: pd.DataFrame())
    monkeypatch.setattr("app.analytics.candidate_evidence.load_json_file", lambda *_args: {})
    monkeypatch.setattr("app.analytics.candidate_evidence.build_loss_attribution", lambda _day: pd.DataFrame())

    evidence = build_candidate_evidence("2026-07-29", candidate_snapshots=current)

    row = evidence.iloc[0]
    assert row["scan_count"] == 2
    assert row["decision"] == "ENTER_PAPER"
    assert row["latest_decision"] == "WAIT"


def test_builder_uses_auto_paper_gate_reason_not_enter_paper_action():
    evidence = build_candidate_evidence_from_frames(
        "2026-07-28",
        pd.DataFrame([{
            "Symbol": "AAPL",
            "Candidate Direction": "CALL",
            "Entry": "EMA_PULLBACK",
            "Action Status": "ENTER_PAPER",
            "Blocked By": "ENTER_PAPER",
        }]),
        auto_paper_decisions=pd.DataFrame([{
            "symbol": "AAPL",
            "decision": "BLOCKED",
            "reason": "not top candidate",
            "timestamp": "2026-07-28 10:00:00",
        }]),
    )

    assert evidence.iloc[0]["rule_evaluation"] == "not top candidate"
    assert evidence.iloc[0]["auto_paper_decision"] == "BLOCKED"
    assert evidence.iloc[0]["auto_paper_blocked_by"] == "not top candidate"