"""Scanner-owned paper position lifecycle.

The scanner is the single owner of the paper trade lifecycle. Its per-symbol loop
manages and closes open trades through ``app/exit/exit_engine.py::evaluate_exit()``,
which remains the only source of truth for exit decisions.

This module owns the lifecycle work that is *not* per-symbol and therefore has no
home inside that loop:

* session start restore/archival (``initialize_paper_session``)
* one-per-session ``POSITION CONTINUES`` alerts for multi-day positions
* the holding-policy end-of-day force close of ``INTRADAY`` positions
* visibility for open positions the scanner could not manage this scan

It deliberately contains **no** stop, target, or profit-R rules. Adding market
exit rules here would recreate the second exit engine this module replaced.
"""

from __future__ import annotations


NO_ACTIVE_TRADE_MARKER = "No active trade"

# Written by the scanner when an open position exists but the 15m frame the exit
# engine needs could not be built. Keeps the persisted row honest: the previous
# default claimed "No active trade" while a position was open.
EXIT_NOT_EVALUATED_MARKER = "Exit not evaluated: insufficient 15m data"

UNMANAGED_EXIT_REASONS = frozenset({
    NO_ACTIVE_TRADE_MARKER,
    EXIT_NOT_EVALUATED_MARKER,
})


def _controls(controls):
    return controls or {}


def _current_prices(df):

    if df is None or getattr(df, "empty", True):
        return {}

    if "Symbol" not in df.columns or "Price" not in df.columns:
        return {}

    try:
        return df.set_index("Symbol")["Price"].to_dict()
    except Exception:
        return {}


def _scanner_rows_by_symbol(df):

    if df is None or getattr(df, "empty", True) or "Symbol" not in df.columns:
        return {}

    rows = {}

    for _, row in df.iterrows():
        symbol = str(row.get("Symbol") or "").strip().upper()
        if symbol and symbol not in rows:
            rows[symbol] = row

    return rows


def _open_paper_positions():

    from app.state.paper_trade_manager import load_paper_trades

    try:
        trades = load_paper_trades()
    except Exception as exc:
        print(f"[PAPER LIFECYCLE WARNING] could not load paper trades: {exc}")
        return []

    return [
        trade for trade in trades.values()
        if str(trade.get("status") or "").upper() == "OPEN"
    ]


def _price_for(trade, prices):

    symbol = trade.get("symbol")

    return prices.get(
        symbol,
        trade.get("current_price") or trade.get("entry_price")
    )


_positions_restored = False


def _restore_lost_positions_once():
    """Re-adopt open positions from Postgres on the first scan of a process.

    Runs before the session lifecycle so a recovered MULTIDAY position is present
    when `restore_open_multiday_positions()` looks for it, and before any entry
    gating so the daily cap counts it.

    Once per process, not once per scan: the state file is only ever lost when the
    container restarts, and that is exactly when a new process starts. Repeating
    the query every five minutes would add nothing and cost a database round trip
    inside the scan path.
    """

    global _positions_restored

    if _positions_restored:
        return []

    try:
        from app.state.paper_trade_manager import restore_open_trades_from_db

        restored = restore_open_trades_from_db()

    except Exception as exc:
        print(f"[PAPER STATE RESTORE WARNING] {exc}")
        return []

    # Latched only once a read has actually succeeded. Setting it beforehand
    # meant a worker that started during a database blip gave up permanently and
    # traded the whole session believing the book was empty -- which is the exact
    # state the docstring above describes: a second NVDA position opened against
    # an invisible first, and a daily cap of 3 that produced 6 trades.
    if restored is None:

        print("[PAPER STATE RESTORE] read failed; will retry on the next scan")

        return []

    _positions_restored = True

    return restored


def initialize_paper_session(controls=None, trading_day=None):
    """Restore multi-day positions and archive stale candidates at scan start.

    Previously reachable only from a dashboard render, which meant a standalone
    scanner run never restored carried positions.
    """

    from app.state.trade_session_lifecycle import initialize_session_lifecycle

    controls = _controls(controls)

    _restore_lost_positions_once()

    try:

        lifecycle = initialize_session_lifecycle(
            trading_day,
            restore_multiday_positions=controls.get(
                "restore_multiday_positions",
                True
            ),
        )

    except Exception as exc:

        print(f"[PAPER SESSION LIFECYCLE WARNING] {exc}")
        return {
            "restored_positions": [],
            "carried_intraday_positions": [],
            "archived_candidates": [],
        }

    for trade in lifecycle.get("carried_intraday_positions") or []:

        print(
            "[INTRADAY OVERNIGHT CARRY] "
            f"{trade.get('symbol')}: "
            f"{trade.get('overnight_carry_warning')}"
        )

    print(
        "[PAPER SESSION LIFECYCLE] "
        f"restored={len(lifecycle.get('restored_positions') or [])} "
        f"carried_intraday={len(lifecycle.get('carried_intraday_positions') or [])} "
        f"archived_candidates={len(lifecycle.get('archived_candidates') or [])}"
    )

    return lifecycle


def _publish_continuation_alerts(positions, prices, rows_by_symbol):

    from app.alerts.telegram_alerts import (
        maybe_send_multiday_position_continue_alert,
    )
    from app.runtime.paper_automation_support import _scanner_context_from_row

    sent = []

    for trade in positions:

        symbol = trade.get("symbol")
        row = rows_by_symbol.get(str(symbol or "").strip().upper())
        scanner_context = None

        if row is not None:

            try:
                scanner_context = _scanner_context_from_row(row)
            except Exception:
                scanner_context = None

        try:

            result = maybe_send_multiday_position_continue_alert(
                trade,
                _price_for(trade, prices),
                scanner_context,
            )

        except Exception as exc:

            print(f"[POSITION CONTINUES ALERT ERROR] {symbol}: {exc}")
            continue

        if isinstance(result, dict) and result.get("sent"):
            sent.append(symbol)

    return sent


def _force_close_intraday_at_eod(positions, controls, prices):

    from app.runtime.paper_automation_support import (
        _close_paper_trade,
        eod_force_close_reason,
    )

    result = {"closed": [], "held": []}

    for trade in positions:

        symbol = trade.get("symbol")
        reason = eod_force_close_reason(trade, controls)

        if not reason:

            if controls.get("eod_close_enabled", False):
                result["held"].append(symbol)

            continue

        try:

            _close_paper_trade(
                symbol,
                _price_for(trade, prices),
                exit_reason=reason,
            )

        except Exception as exc:

            print(f"[PAPER EOD CLOSE WARNING] {symbol}: {exc}")
            continue

        result["closed"].append(symbol)

    if result.get("closed"):

        print(
            "[PAPER EOD CLOSE] force closed intraday: "
            + ", ".join(str(symbol) for symbol in result["closed"])
        )

    if result.get("held"):

        print(
            "[PAPER EOD CLOSE] multi-day positions held: "
            + ", ".join(str(symbol) for symbol in result["held"])
        )

    return result


def _unmanaged_positions(positions, rows_by_symbol):
    """Open positions whose symbol produced no managed scanner row this scan.

    Three ways a position escapes exit evaluation, all detected here:

    * its symbol produced no row at all
    * its symbol was skipped before trade management (fetch failure, no 5m
      candles, invalid 5m analysis), leaving the ``No active trade`` default
    * its 5m data was usable but too short to build the 15m frame the exit
      engine needs, leaving the explicit not-evaluated marker
    """

    unmanaged = []

    for trade in positions:

        symbol = str(trade.get("symbol") or "").strip().upper()

        if not symbol:
            continue

        row = rows_by_symbol.get(symbol)

        if row is None:
            unmanaged.append((trade.get("symbol"), "NO_SCANNER_ROW"))
            continue

        live_exit_reason = str(row.get("Live Exit Reason") or "").strip()

        if live_exit_reason in UNMANAGED_EXIT_REASONS:

            if live_exit_reason == EXIT_NOT_EVALUATED_MARKER:
                reason = "INSUFFICIENT_15M_DATA"
            else:
                reason = str(
                    row.get("Blocked By")
                    or row.get("Market Data Status")
                    or "SYMBOL_SKIPPED"
                )

            unmanaged.append((trade.get("symbol"), reason))

    return unmanaged


def _suggestion_candidate_rows(df):

    from app.gates import price_geometry_error
    from app.runtime.paper_automation_support import (
        _affordability_mask,
        _env_bool,
        _is_valid_new_entry_type,
    )

    if df is None or getattr(df, "empty", True):
        return []

    required = ["Symbol", "Candidate Direction", "Setup Valid", "Action Status", "Entry"]

    if any(column not in df.columns for column in required):
        return []

    rows = df[
        (df["Setup Valid"] == True)  # noqa: E712 - pandas mask, not identity
        & (df["Candidate Direction"].isin(["CALL", "PUT"]))
        & (df["Action Status"].isin(["REVIEW_TV_CHART", "ENTER", "ENTER_PAPER"]))
        & _affordability_mask(df, _env_bool("SUGGESTIONS_IGNORE_AFFORDABILITY", True))
    ].copy()

    if rows.empty:
        return []

    rows = rows[rows["Entry"].map(_is_valid_new_entry_type)].copy()

    if rows.empty:
        return []

    rows = rows[rows.apply(lambda row: price_geometry_error(row) is None, axis=1)]

    return [row for _, row in rows.iterrows()]


def sync_scan_suggestions(df):
    """Advance the suggestion lifecycle from the scan that just completed.

    Previously reachable only from a dashboard render, so a standalone scanner run
    recorded no suggestions at all: `suggested_trade_state.json` held zero entries
    for 2026-07-29 despite 884 evaluations. Without it there is no record of a
    candidate expiring unentered, which makes "how many setups timed out before
    they could be taken?" unanswerable.
    """

    from app.state.suggested_trade_manager import (
        cleanup_old_suggestions,
        sync_suggestions_from_scan,
    )

    rows = _suggestion_candidate_rows(df)

    try:
        sync_suggestions_from_scan(rows)
        cleanup_old_suggestions()

    except Exception as exc:
        print(f"[SUGGESTION SYNC WARNING] {exc}")
        return {"synced": 0, "error": str(exc)}

    print(f"[SUGGESTION SYNC] tracked {len(rows)} candidate suggestion(s)")
    return {"synced": len(rows), "error": None}


def run_paper_position_lifecycle(df, controls=None):
    """Run the non-per-symbol paper lifecycle work after the scanner loop."""

    controls = _controls(controls)
    positions = _open_paper_positions()

    if not positions:
        return {
            "continuation_alerts": [],
            "eod_closed": [],
            "eod_held": [],
            "unmanaged": [],
        }

    prices = _current_prices(df)
    rows_by_symbol = _scanner_rows_by_symbol(df)

    unmanaged = _unmanaged_positions(positions, rows_by_symbol)

    if unmanaged:

        print(
            "[PAPER LIFECYCLE WARNING] open positions not managed this scan: "
            + ", ".join(f"{symbol} ({reason})" for symbol, reason in unmanaged)
        )

    continuation_alerts = _publish_continuation_alerts(
        positions,
        prices,
        rows_by_symbol
    )

    if continuation_alerts:

        print(
            "[POSITION CONTINUES] sent for "
            + ", ".join(str(symbol) for symbol in continuation_alerts)
        )

    eod_result = _force_close_intraday_at_eod(positions, controls, prices)

    return {
        "continuation_alerts": continuation_alerts,
        "eod_closed": eod_result.get("closed") or [],
        "eod_held": eod_result.get("held") or [],
        "unmanaged": [symbol for symbol, _ in unmanaged],
    }
