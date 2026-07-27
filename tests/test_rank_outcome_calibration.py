import pandas as pd

from app.analytics.rank_outcome_calibration import build_rank_outcome_calibration


def test_rank_outcome_calibration_groups_trade_id_outcomes_by_rank_bucket():
    calibration = build_rank_outcome_calibration(
        [
            {"trade_id": "trade-1", "candidate_rank": 1},
            {"trade_id": "trade-2", "candidate_rank": 2},
            {"trade_id": "trade-3", "candidate_rank": 7},
        ],
        [
            {"trade_id": "trade-1", "final_r": 2.0, "trend_capture": 80, "mfe": 3.0},
            {"trade_id": "trade-2", "final_r": -1.0, "trend_capture": 20, "mfe": 0.5},
            {"trade_id": "trade-3", "final_r": 1.0, "trend_capture": 60, "mfe": 2.0},
        ],
    )

    by_bucket = calibration.set_index("rank_bucket")
    assert set(by_bucket.index) == {"1", "2-3", "6+"}
    assert by_bucket.loc["1", "average_final_r"] == 2.0
    assert by_bucket.loc["2-3", "win_rate_pct"] == 0.0
    assert by_bucket.loc["6+", "average_mfe"] == 2.0


def test_rank_outcome_calibration_returns_empty_without_completed_outcomes():
    calibration = build_rank_outcome_calibration(
        [{"trade_id": "trade-1", "candidate_rank": 1}],
        pd.DataFrame(),
    )

    assert calibration.empty