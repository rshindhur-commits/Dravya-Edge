import pandas as pd

from app.state import suggested_trade_manager
from app.state.holding_policy import HoldingProfile, derive_holding_profile
from app.state.suggested_trade_manager import _base_status, _row_get, suggestion_id_from_row


def test_duplicate_dataframe_columns_are_normalized_to_scalars():
    row = pd.Series(
        ["NVDA", "NVDA", "CALL", "EMA_PULLBACK", "NVDA 24AUG26 180C"],
        index=["Symbol", "Symbol", "Candidate Direction", "Entry", "Option Ticker"],
    )

    assert _row_get(row, "Symbol") == "NVDA"
    assert suggestion_id_from_row(row) == "NVDA|CALL|EMA_PULLBACK|NVDA 24AUG26 180C"
    assert _base_status(row, is_new=True) == "NEW_CALL"
    assert derive_holding_profile(row) is HoldingProfile.INTRADAY


def test_suggestion_uses_15m_score_when_setup_percent_is_missing(monkeypatch):
    state = {}
    saved_states = []
    monkeypatch.setattr(suggested_trade_manager, "load_suggestions", lambda: state)
    monkeypatch.setattr(suggested_trade_manager, "save_suggestions", saved_states.append)
    row = pd.Series({
        "Symbol": "MSFT",
        "Candidate Direction": "CALL",
        "Entry": "EMA_PULLBACK",
        "Option Ticker": "MSFT260821C00400000",
        "Price": 100,
        "Candidate Entry Price": 100,
        "Candidate Stop Price": 98,
        "Candidate Target Price": 104,
        "Expiration Bucket": "PREFERRED_14_30",
        "Setup %": None,
        "15m Score": 85,
        "Candidate RR": 2.0,
        "Option Quality Score": 80,
    })

    suggestion = suggested_trade_manager.upsert_suggestion_from_scan(row)

    assert suggestion["holding_profile"] == HoldingProfile.MULTIDAY.value
    assert suggestion["original_setup_percent"] == 85
    assert suggestion["current_setup_percent"] == 85
    assert saved_states == [state]


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