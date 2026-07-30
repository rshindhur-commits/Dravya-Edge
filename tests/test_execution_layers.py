import pandas as pd

from app.main import _initialize_execution_fields


def test_scanner_recommendation_is_separate_from_legacy_action_status():
    rows = _initialize_execution_fields(pd.DataFrame([
        {"Action Status": "ENTER_PAPER"},
        {"Action Status": "WAIT"},
        {"Action Status": "REVIEW_TV_CHART"},
    ]))

    assert rows["Action Status"].tolist() == ["ENTER_PAPER", "WAIT", "REVIEW_TV_CHART"]
    assert rows["Scanner Recommendation"].tolist() == [
        "ENTRY_RECOMMENDED",
        "NO_RECOMMENDATION",
        "REVIEW_RECOMMENDED",
    ]
    assert rows["Trade Status"].tolist() == ["NOT_CREATED", "NOT_CREATED", "NOT_CREATED"]
    assert rows["Telegram Status"].tolist() == [
        "NO_LIFECYCLE_EVENT",
        "NO_LIFECYCLE_EVENT",
        "NO_LIFECYCLE_EVENT",
    ]