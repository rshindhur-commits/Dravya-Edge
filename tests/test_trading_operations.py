from app.ui.pages.trading import _risk_alerts, _trading_day


def test_trading_day_prefers_explicit_value_then_scan_id():
    assert _trading_day({"trading_day": "2026-07-28"}) == "2026-07-28"
    assert _trading_day({"scan_id": "2026-07-28_155902"}) == "2026-07-28"


def test_risk_monitor_flags_actionable_position_conditions():
    alerts = _risk_alerts([{
        "symbol": "AAPL",
        "rr_progress": -0.9,
        "last_exit_confidence_score": 92,
        "last_exit_phase": "TREND_FAILURE",
        "holding_profile_override_source": "MANUAL_OVERRIDE",
    }])

    assert len(alerts) == 4
    assert {alert[1] for alert in alerts} == {
        "Stop proximity",
        "High exit confidence",
        "Exit signal",
        "Manual profile override",
    }