import pandas as pd

from app.analytics.candidate_outcomes import build_candidate_outcomes


def test_candidate_outcomes_identify_telegram_miss_and_false_alert(monkeypatch):
    audit = pd.DataFrame([
        {"symbol": "AAPL", "setup": "EMA_PULLBACK", "action": "WAIT", "final_outcome": "TARGET", "Telegram Sent": False},
        {"symbol": "MSFT", "setup": "BREAKOUT", "action": "ENTER_PAPER", "final_outcome": "STOP", "Telegram Sent": True},
    ])
    monkeypatch.setattr("app.analytics.candidate_outcomes.load_daily_inputs", lambda _day: {"audit": audit})
    outcomes = build_candidate_outcomes("2026-07-21")
    assert outcomes.iloc[0]["telegram_miss"]
    assert outcomes.iloc[1]["false_alert"]
    assert outcomes.iloc[1]["entered"]


def test_candidate_outcomes_collapses_repeated_observations(monkeypatch):
    audit = pd.DataFrame([
        {"symbol": "AAPL", "direction": "CALL", "setup": "EMA_PULLBACK", "action": "WAIT", "final_outcome": "", "Telegram Sent": False},
        {"symbol": "AAPL", "direction": "CALL", "setup": "EMA_PULLBACK", "action": "ENTER_PAPER", "final_outcome": "TARGET", "Telegram Sent": True},
    ])
    monkeypatch.setattr("app.analytics.candidate_outcomes.load_daily_inputs", lambda _day: {"audit": audit})

    outcomes = build_candidate_outcomes("2026-07-28")

    assert len(outcomes) == 1
    assert outcomes.iloc[0]["entered"]
    assert outcomes.iloc[0]["target_hit"]
