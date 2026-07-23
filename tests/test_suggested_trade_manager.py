from app.state import suggested_trade_manager


def test_promotion_reconciles_expired_suggestion(monkeypatch):
    state = {
        "NVDA|CALL|EMA_PULLBACK|NVDA260821C00150000": {
            "symbol": "NVDA",
            "direction": "CALL",
            "setup_type": "EMA_PULLBACK",
            "option_ticker": "NVDA260821C00150000",
            "status": "EXPIRED_NOT_ENTERED",
        }
    }
    saved_states = []
    monkeypatch.setattr(suggested_trade_manager, "load_suggestions", lambda: state)
    monkeypatch.setattr(suggested_trade_manager, "save_suggestions", saved_states.append)

    promoted = suggested_trade_manager.promote_suggestion_to_paper_trade(
        symbol="NVDA",
        direction="CALL",
        setup_type="EMA_PULLBACK",
        option_ticker="NVDA260821C00150000",
        opened_at="2026-07-22T10:00:00-04:00",
        trade_key="paper-nvda",
    )

    assert promoted
    assert state["NVDA|CALL|EMA_PULLBACK|NVDA260821C00150000"]["status"] == "PROMOTED_TO_PAPER"
    assert state["NVDA|CALL|EMA_PULLBACK|NVDA260821C00150000"]["paper_trade_key"] == "paper-nvda"
    assert saved_states == [state]