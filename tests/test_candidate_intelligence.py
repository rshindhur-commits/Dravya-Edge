import pandas as pd

from app.analytics.candidate_intelligence import build_candidate_intelligence


def test_candidate_intelligence_classifies_good_candidates_and_misses():
    intelligence = build_candidate_intelligence(pd.DataFrame([
        {
            "candidate_id": "mu", "symbol": "MU", "setup": "EMA_PULLBACK", "direction": "CALL",
            "rr": 2.4, "setup_score": 82, "option_quality": 90, "trend_health": "HEALTHY",
            "entered": False, "target_first": False, "stop_first": True,
            "rule_evaluation": "STALE_QUOTE",
        },
        {
            "candidate_id": "aapl", "symbol": "AAPL", "setup": "BREAKOUT", "direction": "CALL",
            "rr": 3.2, "setup_score": 85, "option_quality": 88, "trend_health": "STRONG",
            "entered": False, "target_first": True, "stop_first": False,
            "rule_evaluation": "STALE_QUOTE", "quote_freshness": "STALE_QUOTE",
        },
        {
            "candidate_id": "nvda", "symbol": "NVDA", "setup": "VWAP_RECLAIM", "direction": "CALL",
            "rr": 3.4, "setup_score": 80, "option_quality": 85, "trend_health": "HEALTHY",
            "entered": True, "target_first": False, "stop_first": True,
        },
    ]))

    assert intelligence["summary"] == {
        "good_candidates": 3,
        "opened": 1,
        "skipped": 0,
        "blocked": 2,
        "correct_skips": 1,
        "correct_blocks": 1,
        "missed_winners": 1,
        "investigate": 2,
    }
    blocked = intelligence["high_quality_blocked"]
    assert set(blocked["symbol"]) == {"MU", "AAPL"}
    missed = intelligence["good_candidates"].query("symbol == 'AAPL'").iloc[0]
    assert missed["missed_winner_type"] == "OPERATIONAL_MISS"
    assert "AAPL" in set(intelligence["investigation_queue"]["symbol"])