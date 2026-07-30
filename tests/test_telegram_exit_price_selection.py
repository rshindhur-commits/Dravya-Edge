from app.alerts.telegram_alerts import (
    build_trade_exit_alert_message,
    resolve_exit_price_context,
)


def test_prefers_latest_quote_over_5m_and_15m():
    result = resolve_exit_price_context(
        candidate_prices={
            "latest_quote": 101.5,
            "df_5m_latest_close": 100.8,
            "df_15m_latest_close": 100.2,
        }
    )

    assert result["current_price"] == 101.5
    assert result["price_source"] == "latest_quote"


def test_falls_back_to_5m_close_when_quote_missing():
    result = resolve_exit_price_context(
        candidate_prices={
            "df_5m_latest_close": 100.9,
            "df_15m_latest_close": 100.1,
        }
    )

    assert result["current_price"] == 100.9
    assert result["price_source"] == "df_5m_latest_close"


def test_exit_message_includes_trend_capture_when_available():
    message = build_trade_exit_alert_message(
        "NFLX",
        {"entry_price": 100, "option_ticker": "O:NFLX"},
        "Trend failure",
        r_multiple=1.1,
        trend_capture_pct=71.4,
    )

    assert "TRADE CLOSED" in message
    # The result block renders the outcome and the R multiple on separate lines,
    # so assert on the parts rather than a single "WIN: 1.1R" string.
    assert "WIN" in message
    assert "1.1R" in message
    assert "Trend Capture: 71.4%" in message
