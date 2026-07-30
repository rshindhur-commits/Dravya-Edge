import pandas as pd

from app.analytics.recommendation_outcomes import (
    build_horizon_outcomes,
    build_recommendation_facts,
    build_recommendation_outcome_summary,
    build_high_score_execution_audit,
)


def test_recommendation_facts_capture_only_recommended_candidates():
    facts = build_recommendation_facts(
        pd.DataFrame([
            {
                "Symbol": "NFLX", "Candidate Direction": "PUT", "Entry": "VWAP_REJECTION",
                "Candidate Entry Price": 100, "Scanner Recommendation": "ENTRY_RECOMMENDED",
            },
            {
                "Symbol": "SPY", "Candidate Direction": "CALL", "Entry": "NO_ENTRY",
                "Candidate Entry Price": 600, "Scanner Recommendation": "NO_RECOMMENDATION",
            },
        ]),
        "2026-07-20", "2026-07-20_100000", "2026-07-20T10:00:00-04:00",
    )

    assert len(facts) == 1
    assert facts.iloc[0]["symbol"] == "NFLX"
    assert facts.iloc[0]["scanner_recommendation"] == "ENTRY_RECOMMENDED"


def test_horizon_outcomes_measure_directional_and_matching_option_returns():
    facts = build_recommendation_facts(
        pd.DataFrame([{
            "Symbol": "NFLX", "Candidate Direction": "PUT", "Entry": "VWAP_REJECTION",
            "Candidate Entry Price": 100, "Option Ticker": "O:NFLX", "Option Mid Price": 2,
            "Scanner Recommendation": "ENTRY_RECOMMENDED",
        }]),
        "2026-07-20", "2026-07-20_100000", "2026-07-20T10:00:00-04:00",
    )

    outcomes = build_horizon_outcomes(
        facts,
        pd.DataFrame(),
        pd.DataFrame([{
            "Symbol": "NFLX", "Price": 90,
            "Option Ticker": "O:NFLX", "Option Mid Price": 2.5,
        }]),
        "2026-07-27", "2026-07-27T10:00:00-04:00",
    )

    assert outcomes["horizon_sessions"].tolist() == [5]
    assert outcomes.iloc[0]["underlying_return_pct"] == -10.0
    assert outcomes.iloc[0]["directional_return_pct"] == 10.0
    assert outcomes.iloc[0]["option_return_pct"] == 25.0


def test_horizon_outcomes_do_not_repeat_existing_fact_horizon_pair():
    facts = build_recommendation_facts(
        pd.DataFrame([{
            "Symbol": "AAPL", "Candidate Direction": "CALL", "Entry": "BREAKOUT",
            "Candidate Entry Price": 100, "Scanner Recommendation": "ENTRY_RECOMMENDED",
        }]),
        "2026-07-20", "2026-07-20_100000", "2026-07-20T10:00:00-04:00",
    )
    existing = pd.DataFrame([{
        "recommendation_id": facts.iloc[0]["recommendation_id"],
        "horizon_sessions": 5,
    }])

    outcomes = build_horizon_outcomes(
        facts, existing, pd.DataFrame([{"Symbol": "AAPL", "Price": 110}]),
        "2026-07-27", "2026-07-27T10:00:00-04:00",
    )

    assert outcomes.empty


def test_summary_groups_recommendations_by_rank_bucket_and_horizon():
    facts = pd.DataFrame([{
        "recommendation_id": "rank-2", "candidate_rank": 2, "execution_outcome": "OPENED",
    }, {
        "recommendation_id": "rank-8", "candidate_rank": 8, "execution_outcome": "BLOCKED",
    }])
    outcomes = pd.DataFrame([{
        "recommendation_id": "rank-2", "horizon_sessions": 5,
        "directional_return_pct": 4, "option_return_pct": 10,
    }, {
        "recommendation_id": "rank-8", "horizon_sessions": 5,
        "directional_return_pct": 2, "option_return_pct": None,
    }])

    summary = build_recommendation_outcome_summary(facts, outcomes)

    top = summary[summary["rank_bucket"] == "1-3"].iloc[0]
    middle = summary[summary["rank_bucket"] == "4-10"].iloc[0]
    assert top["execution_rate"] == 1.0
    assert middle["execution_rate"] == 0.0
    assert middle["average_directional_return_pct"] == 2.0


def test_high_score_execution_audit_retains_execution_block_reason():
    audit = build_high_score_execution_audit(
        pd.DataFrame([{
            "Symbol": "NFLX", "Candidate Direction": "PUT", "Entry": "VWAP_REJECTION",
            "Setup %": 95, "Candidate Rank": 13,
            "Scanner Recommendation": "ENTRY_RECOMMENDED",
            "Execution Eligibility": "INELIGIBLE",
            "Execution Outcome": "BLOCKED",
            "Execution Reason": "not top candidate",
        }]),
        "2026-07-29", "2026-07-29_100000",
    )

    assert len(audit) == 1
    assert audit.iloc[0]["execution_reason"] == "not top candidate"