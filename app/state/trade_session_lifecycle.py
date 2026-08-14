from __future__ import annotations

from datetime import date, datetime

from app.state.holding_policy import holding_policy
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


def force_close_orphaned_intraday():
    """Whether an intraday position that outlived its session is closed on sight.

    On by default. An INTRADAY profile sets `force_eod_exit=True`, so a position
    still open the next morning means the 15:55 flatten did not happen, and
    something has already failed. Carrying it is strictly worse than closing it:
    nothing is managing it, and the longer it runs the further its recorded price
    drifts from any price it could have been closed at.

    Set `FORCE_CLOSE_ORPHANED_INTRADAY=false` to restore the old carry-with-a-
    warning behaviour, which is the behaviour that produced the SMCI position
    below and is not recommended.
    """

    from app.config.settings import get_bool_env

    return get_bool_env("FORCE_CLOSE_ORPHANED_INTRADAY", True)


def restore_carried_intraday_positions(trading_day=None):
    """Close intraday positions that survived their own session.

    This function used to *restore* them: it detected the case exactly as it
    still does, wrote `overnight_carry_warning`, and put the trade back in the
    book to keep running. It kept running.

    `O:SMCI260814P00030000` opened 2026-08-05 10:11 ET on an INTRADAY profile and
    closed 2026-08-13 09:57 -- nine days and eight overnights later, with
    `updated_at` untouched for eight of them, so nothing was managing it. It was
    finally closed against a quote frozen at 2026-08-05 11:05 and still stamped
    `LIVE_QUOTE` with an age of 0.7 minutes, 22.7R past its stop. The contract had
    gone from 1.84-2.21 to 0.01-0.03; the book recorded **-1.92%** against a real
    **-99.5%**, about -$201 of a $202 position. Two trades like it are 90% of all
    recorded losses, which contaminated every comparison drawn from live P&L.

    So the detection was never the problem -- the response was. Two changes:

    * the position is **closed**, not carried, at the last price the trade itself
      recorded;
    * the exit is stamped as reconstructed rather than live, so no analysis
      mistakes it for a real fill.

    The root cause -- why the 15:55 flatten did not fire -- is still not
    established. This is a net, not a repair, and it is deliberately independent
    of that answer: whatever lets a position escape the EOD close, the second
    failure of running it for another eight sessions is worth preventing on its
    own.
    """

    from app.state.paper_trade_manager import close_paper_trade

    trading_day = trading_day or get_trading_day()
    current_session_id = get_session_id(trading_day)
    state = load_paper_trades()
    carried = []
    dirty = False

    for trade_key, trade in list(state.items()):
        if str(trade.get("status")).upper() != "OPEN":
            continue
        if not holding_policy(trade.get("holding_profile")).force_eod_exit:
            continue
        opened = _opened_date(trade)
        if opened is None or str(opened) >= trading_day:
            continue
        if trade.get("carried_intraday_session_id") == current_session_id:
            continue

        refresh_trade_lifecycle_fields(trade, trading_day)
        trade["session_id_current"] = current_session_id
        trade["overnight_transition"] = False
        trade["overnight_intraday_carry"] = True
        trade["carried_intraday_session_id"] = current_session_id
        trade["overnight_carry_warning"] = (
            "Intraday trade carried overnight because Auto Close Intraday Trades "
            "was disabled."
        )

        if not force_close_orphaned_intraday():
            state[trade_key] = trade
            carried.append(trade)
            dirty = True
            continue

        # Never a live quote. The whole defect is that a price this old was
        # presented as current, so it is labelled before the close rather than
        # after, and the close inherits the label.
        trade["option_quote_freshness"] = "RECONSTRUCTED_AT_FORCE_CLOSE"
        trade["option_quote_age_minutes"] = None
        trade["needs_repricing"] = True
        trade["force_close_reason"] = (
            f"intraday position opened {opened} still open on {trading_day}; "
            f"closed on its last recorded price, which is not a fill"
        )
        state[trade_key] = trade
        dirty = True

    if dirty:
        save_paper_trades(state)

    if not force_close_orphaned_intraday():
        return carried

    closed = []

    for trade_key, trade in list(state.items()):
        if not trade.get("needs_repricing"):
            continue
        if str(trade.get("status")).upper() != "OPEN":
            continue

        # `close_paper_trade` re-reads state, so it is called after the marks
        # above are saved rather than inside the loop that writes them.
        result = close_paper_trade(
            trade.get("symbol"),
            close_price=_last_known_price(trade),
            exit_reason="ORPHANED_INTRADAY_FORCE_CLOSE",
            notify_exit=False,
        )

        if result is not None:
            closed.append(result)

    return closed


def _last_known_price(trade):
    """The most recent underlying price this trade recorded, if any.

    Deliberately not a fresh fetch. Session initialisation must not depend on a
    market-data call that can fail or be rate limited, and a stale number that is
    labelled stale is safer than a position left running because a quote request
    timed out. `needs_repricing` marks the result so the cash figure is not read
    as real.
    """

    for field in ("current_price", "last_price", "close_price", "entry_price"):
        value = trade.get(field)
        try:
            if value is not None and float(value) > 0:
                return float(value)
        except (TypeError, ValueError):
            continue

    return None


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
        "carried_intraday_positions": restore_carried_intraday_positions(trading_day),
        "archived_candidates": archive_prior_session_candidates(trading_day),
    }
