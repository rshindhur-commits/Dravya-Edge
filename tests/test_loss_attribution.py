import pandas as pd

from app.analytics.loss_attribution import build_loss_attribution


def test_build_loss_attribution_includes_root_cause_fields(monkeypatch):
    audit = pd.DataFrame([
        {
            "symbol": "AAPL",
            "market_move_pct": 4.5,
            "setup": "setup",
            "blocked_reason": "RR Threshold",
            "Action Status": "WAIT",
            "Action Reason": "RR Threshold",
            "Candidate RR": 1.8,
            "top_candidate": "AAPL",
        }
    ])

    monkeypatch.setattr(
        "app.analytics.loss_attribution.load_daily_inputs",
        lambda report_date: {"audit": audit},
    )

    result = build_loss_attribution("2026-07-21")

    assert not result.empty
    assert result.iloc[0]["root_cause"] == "RR Threshold"
    assert result.iloc[0]["rule"] == "RR Threshold"
    assert result.iloc[0]["threshold"] == 1.8
    assert result.iloc[0]["would_have_passed_if"] == "1.8"
    assert result.iloc[0]["confidence"] == "MEDIUM"
