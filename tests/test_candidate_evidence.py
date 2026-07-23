import pandas as pd

from app.analytics.candidate_evidence import build_candidate_evidence_from_frames


def test_candidate_evidence_collapses_scans_and_joins_outcomes():
    evidence = build_candidate_evidence_from_frames(
        "2026-07-22",
        pd.DataFrame([
            {
                "symbol": "AAPL", "direction": "CALL", "setup_type": "EMA_PULLBACK",
                "scan_timestamp": "2026-07-22 10:00:00", "candidate_rr": 2.1,
                "setup_percent": 75, "action_status": "REVIEW_TV_CHART",
                "option_quote_freshness": "LIVE_QUOTE",
            },
            {
                "symbol": "AAPL", "direction": "CALL", "setup_type": "EMA_PULLBACK",
                "scan_timestamp": "2026-07-22 10:05:00", "candidate_rr": 2.4,
                "setup_percent": 82, "action_status": "ENTER_PAPER",
                "replay_outcome": "TARGET_FIRST", "top_candidate": "BULLISH_TOP_1",
                "option_quote_freshness": "LIVE_QUOTE",
            },
        ]),
        suggestions={"aapl": {"symbol": "AAPL", "direction": "CALL", "setup_type": "EMA_PULLBACK", "status": "PROMOTED_TO_PAPER"}},
        paper_events=pd.DataFrame([{"symbol": "AAPL", "direction": "CALL", "event_type": "OPEN", "status": "OPEN"}]),
        trend_capture=pd.DataFrame([{"Symbol": "AAPL", "Direction": "CALL", "Setup": "EMA_PULLBACK", "Trend Capture %": 72, "Trade Efficiency Score": 88}]),
        attribution=pd.DataFrame([{"symbol": "AAPL", "setup": "EMA_PULLBACK", "root_cause": "STALE_QUOTE"}]),
    )

    assert len(evidence) == 1
    row = evidence.iloc[0]
    assert row["scan_count"] == 2
    assert row["rr"] == 2.4
    assert row["setup_score"] == 82
    assert row["entered"]
    assert row["target_first"]
    assert row["winner"]
    assert row["suggestion_status"] == "PROMOTED_TO_PAPER"
    assert row["trend_capture"] == 72
    assert row["tes"] == 88