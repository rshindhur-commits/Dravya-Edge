from __future__ import annotations

from datetime import date, datetime

from app.state.holding_policy import HoldingProfile, holding_policy
from app.state.paper_trade_manager import load_paper_trades, save_paper_trades
from app.state.suggested_trade_manager import (
    PAPER_PROMOTED_STATUSES,
    load_suggestions,
    save_suggestions,
)
from app.storage.session_manager import get_session_id, get_trading_day, now_et


def _opened_date(trade):
    value = trade.get("opened_at_et") or trade.get("opened_at")
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        try:
            return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S").date()
        except ValueError:
            return None


def refresh_trade_lifecycle_fields(trade, current_day=None):
    current_day = current_day or now_et().date()
    if isinstance(current_day, str):
        current_day = date.fromisoformat(current_day)
    opened = _opened_date(trade)
    if opened is None:
        return trade

    trade["days_held"] = max(1, (current_day - opened).days + 1)
    trade["overnight_count"] = max(0, (current_day - opened).days)
    return trade


def restore_open_multiday_positions(trading_day=None):
    trading_day = trading_day or get_trading_day()
    state = load_paper_trades()
    restored = []

    for trade_key, trade in state.items():
        if str(trade.get("status")).upper() != "OPEN":
            continue
        if not holding_policy(trade.get("holding_profile")).restore_next_session:
            continue
        refresh_trade_lifecycle_fields(trade, trading_day)
        trade["session_id_current"] = get_session_id(trading_day)
        trade["overnight_transition"] = bool(trade.get("overnight_count"))
        state[trade_key] = trade
        restored.append(trade)

    if restored:
        save_paper_trades(state)

    return restored


def archive_prior_session_candidates(trading_day=None):
    trading_day = trading_day or get_trading_day()
    state = load_suggestions()
    archived = []

    for suggestion_id, suggestion in state.items():
        last_seen = str(suggestion.get("last_seen_at") or "")
        if last_seen.startswith(trading_day):
            continue
        profile = holding_policy(suggestion.get("holding_profile"))
        promoted = str(suggestion.get("status") or "").upper() in PAPER_PROMOTED_STATUSES
        if promoted and not profile.archive_candidates_eod:
            continue
        if str(suggestion.get("status") or "").upper() == "ARCHIVED":
            continue
        suggestion["status"] = "ARCHIVED"
        suggestion["validity_reason"] = "archived at session start"
        suggestion["archived_at"] = now_et().isoformat(timespec="seconds")
        state[suggestion_id] = suggestion
        archived.append(suggestion_id)

    if archived:
        save_suggestions(state)

    return archived


def initialize_session_lifecycle(trading_day=None, restore_multiday_positions=True):
    return {
        "restored_positions": (
            restore_open_multiday_positions(trading_day)
            if restore_multiday_positions
            else []
        ),
        "archived_candidates": archive_prior_session_candidates(trading_day),
    }


def apply_end_of_day_policy(close_trade, close_price_by_symbol=None):
    close_price_by_symbol = close_price_by_symbol or {}
    closed = []
    held = []
    for trade in load_paper_trades().values():
        if str(trade.get("status")).upper() != "OPEN":
            continue
        policy = holding_policy(trade.get("holding_profile"))
        if policy.force_eod_exit:
            symbol = trade.get("symbol")
            close_trade(
                symbol,
                close_price_by_symbol.get(symbol, trade.get("entry_price")),
                exit_reason="Auto paper exit: end-of-day close",
            )
            closed.append(symbol)
        else:
            held.append(trade.get("symbol"))
    return {"closed": closed, "held": held}