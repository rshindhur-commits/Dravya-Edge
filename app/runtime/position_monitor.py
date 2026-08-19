"""Watch open positions between scans, because the scan is too slow to manage them.

    python -m app.runtime.position_monitor

Exits currently run inside the scan cycle (``app/main.py:5181``), which is 300s
in the regular session. Nothing looks at an open position in between.

On 2026-08-18 SPCX peaked at **+0.75R at 10:16** and was back to **+0.09R by
10:21**. The whole reversal happened inside one gap. Across the archive the
recorded intrabar peak runs **2-6x** the best price any scan observed:

    SPCX #277   engine saw 0.75R   actually reached 1.11R
    PLTR #256   engine saw 0.20R   actually reached 0.64R
    PLTR #281   engine saw 0.09R   actually reached 0.54R

So the engine is blind to most of the move it is meant to be managing, and no
threshold change fixes that -- only looking more often does.

## Why this is cheap

A universe scan prices 23 symbols and their option chains. This prices only the
symbols with an open position, which is normally 0-3 and often 0. One REST call
per held symbol. At 20s with two positions that is 6 calls a minute against a
full scan's several hundred every five, so it can run 15x more often for a small
fraction of the cost.

## What it may and may not decide

It calls ``exit_engine.evaluate_price_exits``, which is the **same module and the
same helpers** the scan uses -- only the rules that need nothing but a price:
hard stop, the breakeven move, the option give-back floor.

It must never gain a rule of its own. Momentum, volume flush and trend validity
stay in ``evaluate_exit`` because they read bars, and a bar cannot update faster
than it closes. ``paper_position_lifecycle`` carries the same warning; the way to
honour it is to widen the shared function, never to grow a second engine here.

Off unless ``POSITION_MONITOR_ENABLED`` is true.
"""

from __future__ import annotations

import os
import signal
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.runtime import position_stream

ET = ZoneInfo("America/New_York")

DEFAULT_INTERVAL_SECONDS = 20

# Only inside the entry-to-close window. Positions cannot open before 09:45 and
# the scan cycle's own end-of-day force close owns everything after the bell.
FIRST_MINUTE = 9 * 60 + 30
LAST_MINUTE = 16 * 60

_stopping = False


def _log(message):
    """Print, always flushed.

    Render pipes stdout, so Python block-buffers it at 8KB. The scan worker
    prints enough per cycle to keep filling that buffer; this process prints one
    line and then sleeps for twenty seconds, so an unflushed line sits there
    indefinitely and the service reads as dead in the dashboard while running
    perfectly. A monitor whose log is invisible is not a monitor.

    Belt and braces with PYTHONUNBUFFERED rather than instead of it -- the env
    var is a property of the service and the next one will not have it.
    """

    print(message, flush=True)



def _request_stop(signum, _frame):
    global _stopping
    _stopping = True
    _log(f"\n[POSITION MONITOR] signal {signum}; finishing this pass then exiting.")


def enabled():
    """Read at call time so the switch moves without a redeploy."""

    return str(os.getenv("POSITION_MONITOR_ENABLED", "")).strip().lower() in {
        "1", "true", "yes", "on"
    }


def interval_seconds():
    try:
        return max(5, int(os.getenv("POSITION_MONITOR_INTERVAL_SECONDS", "")))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS


def candidate_logging_enabled():
    """Observational sub-scan price capture. Off by default.

    Separate from POSITION_MONITOR_ENABLED because it writes rows nothing reads,
    and an operator should be able to run the monitor without also growing a
    table.
    """

    return str(os.getenv("POSITION_MONITOR_LOG_CANDIDATES", "")).strip().lower() in {
        "1", "true", "yes", "on"
    }


def _in_session(now=None):
    now = now or datetime.now(ET)

    if now.weekday() >= 5:
        return False

    minutes = now.hour * 60 + now.minute

    return FIRST_MINUTE <= minutes < LAST_MINUTE


def _open_positions():
    """Open paper trades, via the lifecycle module's own loader.

    Not a second query. That module already owns "what is open", and two
    definitions of an open position is how a monitor and a scanner end up
    disagreeing about whether a trade exists.
    """

    from app.runtime.paper_position_lifecycle import _open_paper_positions

    try:
        return _open_paper_positions()
    except Exception as exc:
        _log(f"[POSITION MONITOR WARNING] could not load positions: {exc}")
        return []


def _risk_setup(trade):
    return {
        "entry_price": trade.get("entry_price"),
        "stop_loss": trade.get("stop_loss"),
        "initial_stop_loss": (
            trade.get("initial_stop_loss") or trade.get("stop_loss")
        ),
        "take_profit": trade.get("take_profit"),
    }


def _trade_state(trade):
    """`direction` is carried explicitly -- see the note in `evaluate_price_exits`.

    `paper_trades` stores PUT/CALL there while the scanner passes a setup name in
    `entry_type`, and only one of them is understood by `_is_short_entry`.
    """

    return {
        "direction": trade.get("direction"),
        "entry_type": trade.get("entry_type") or trade.get("setup"),
        "initial_stop_loss": trade.get("initial_stop_loss"),
        "mfe_r": trade.get("mfe_r"),
        "option_peak_mid": trade.get("option_peak_mid"),
        "option_current_mid": trade.get("option_current_mid"),
    }


def _price_for(symbol):
    """Streamed price when it is fresh, REST otherwise.

    The stream is preferred but never trusted on its own. A socket can stop
    delivering without closing, and a frozen last-heard price looks exactly like
    a motionless market -- every protective rule would stop working while
    appearing healthy. So a streamed price is used only while its age is inside
    `POSITION_MONITOR_STREAM_MAX_AGE`, and REST is the floor beneath it.

    Returns (price, source) so the log says which one decided.
    """

    from app.utils.polygon_client import get_last_price

    if position_stream.stream_enabled():

        stream = position_stream.get_stream()
        price, age = stream.latest(symbol)

        if price and age is not None and age <= position_stream.max_age_seconds():
            return price, f"stream/{age:.1f}s"

    try:
        return get_last_price(symbol), "rest"
    except Exception as exc:
        _log(f"[POSITION MONITOR WARNING] {symbol}: price unavailable: {exc}")
        return None, None


def check_once():
    """One pass over every open position. Returns what it decided, for tests."""

    from app.exit.exit_engine import evaluate_price_exits

    positions = _open_positions()

    # Re-synced every pass rather than on open/close, so a position the monitor
    # learns about late is still watched, and a closed one stops consuming a
    # subscription slot.
    if position_stream.stream_enabled():
        stream = position_stream.get_stream()
        stream.start()
        stream.sync([t.get("symbol") for t in positions])

    decisions = []

    for trade in positions:

        symbol = trade.get("symbol")

        if not symbol:
            continue

        price, source = _price_for(symbol)

        if not price:
            continue

        verdict = evaluate_price_exits(
            _risk_setup(trade),
            _trade_state(trade),
            price,
            option_mid=trade.get("option_current_mid"),
        )

        if verdict is None:
            continue

        decisions.append((trade, price, verdict))

        _log(
            f"[POSITION MONITOR] {symbol} @ {price} ({source}) "
            f"R={verdict.get('rr_progress')} "
            f"{verdict.get('exit_code') or verdict.get('reason')}"
        )

        _act_on(trade, symbol, price, verdict)

    if candidate_logging_enabled():
        _log_candidate_prices({t.get("symbol") for t in positions})

    return decisions


def _forming_candidates(held_symbols):
    """Symbols showing a direction but holding no position, from the last scan.

    Read from `candidate_snapshot` rather than re-derived, so this observes what
    the scanner actually decided instead of inventing a second opinion about
    what counts as a candidate.
    """

    from sqlalchemy import text

    from app.db.connection import get_engine

    try:
        with get_engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT DISTINCT ON (symbol)
                           symbol, direction, setup, scanner_recommendation
                    FROM candidate_snapshot
                    WHERE trading_day = (now() AT TIME ZONE 'America/New_York')::date
                      AND direction IS NOT NULL
                      AND upper(direction) IN ('CALL', 'PUT')
                    ORDER BY symbol, created_at DESC
                    """
                )
            ).mappings().all()
    except Exception as exc:
        _log(f"[POSITION MONITOR WARNING] candidate read failed: {exc}")
        return []

    return [r for r in rows if r["symbol"] not in held_symbols]


def _log_candidate_prices(held_symbols):
    """Record sub-scan prices for candidates that have not entered.

    Purely observational -- see `032_candidate_price_log.sql`. The archive is
    5-minute snapshots, so the movement inside a gap has never been recorded, and
    every question about entry timing has had to be answered with a proxy. This
    is how that data starts existing.

    Failures are swallowed on purpose. A logging table must never be able to stop
    a monitor whose actual job is managing open positions.
    """

    from sqlalchemy import text

    from app.db.connection import get_engine
    from app.utils.polygon_client import get_last_price

    candidates = _forming_candidates(held_symbols)

    if not candidates:
        return 0

    written = 0

    for row in candidates:

        try:
            price, source = _price_for(row["symbol"])
        except Exception:
            continue

        if not price:
            continue

        try:
            with get_engine().begin() as connection:
                connection.execute(
                    text(
                        """
                        INSERT INTO candidate_price_log
                            (trading_day, symbol, price, signal, setup,
                             direction, source)
                        VALUES (
                            (now() AT TIME ZONE 'America/New_York')::date,
                            :symbol, :price, :signal, :setup, :direction, :source
                        )
                        """
                    ),
                    {
                        "symbol": row["symbol"],
                        "price": price,
                        "signal": row.get("scanner_recommendation"),
                        "setup": row.get("setup"),
                        "direction": row.get("direction"),
                        "source": source,
                    },
                )
            written += 1
        except Exception as exc:
            _log(f"[POSITION MONITOR WARNING] candidate log failed: {exc}")
            return written

    return written


def _act_on(trade, symbol, price, verdict):
    """Persist a moved stop, or close the position.

    The stop write is not optional bookkeeping. `evaluate_price_exits` is
    stateless: it re-derives the breakeven arm from whatever stop it is handed.
    If a pass moves the stop to entry and that is never written back, the next
    pass reads the original stop and the protection silently un-arms. The trade
    would then be evaluated against its opening stop for the rest of its life.

    `close_paper_trade` owns the close: the DB write, the premium fields and the
    subscriber EXIT alert. It returns early when the trade is no longer OPEN, so
    a scan cycle closing the same position a moment earlier is a no-op here
    rather than a double close -- which is the only race between the two, and
    the reason nothing in this module writes a trade row directly.
    """

    from app.state.paper_trade_manager import close_paper_trade, update_paper_trade

    updated_stop = verdict.get("updated_stop")

    if verdict.get("exit"):

        fill = verdict.get("fill_price")

        # `resolve_exit_fill` returns (price, ...) for some codes and a bare
        # price for others. The tuple's first element is the fill.
        if isinstance(fill, (tuple, list)):
            fill = fill[0] if fill else None

        try:
            close_paper_trade(
                symbol,
                close_price=fill if fill is not None else price,
                exit_reason=(
                    f"{verdict.get('exit_code')}: {verdict.get('reason')} "
                    f"(position monitor)"
                ),
                notify_exit=True,
            )
            _log(f"[POSITION MONITOR] CLOSED {symbol} at {fill or price}")
        except Exception as exc:
            _log(f"[POSITION MONITOR ERROR] close failed for {symbol}: {exc}")

        return

    if updated_stop is None or updated_stop == trade.get("stop_loss"):
        return

    try:
        update_paper_trade(
            symbol,
            highest_price=trade.get("highest_price"),
            rr_progress=verdict.get("rr_progress"),
            updated_stop=updated_stop,
            current_price=price,
        )
        _log(
            f"[POSITION MONITOR] {symbol} stop "
            f"{trade.get('stop_loss')} -> {updated_stop}"
        )
    except Exception as exc:
        _log(f"[POSITION MONITOR ERROR] stop update failed for {symbol}: {exc}")


def main():
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    if not enabled():
        _log(
            "[POSITION MONITOR] POSITION_MONITOR_ENABLED is not set; exiting. "
            "The scan cycle continues to own exits."
        )
        return

    wait = interval_seconds()
    _log(f"[POSITION MONITOR] started; checking open positions every {wait}s.")

    # A silent process and a dead one look identical in a dashboard. This one is
    # silent by design -- it prints only when a rule fires, which on a day with
    # no positions is never. So it says it is alive on a slow clock, and says so
    # again whenever the session window opens or closes, because those are the
    # two moments an operator wants confirmation without reading twenty lines.
    heartbeat_every = max(1, 300 // wait)
    passes = 0
    was_in_session = None

    while not _stopping:

        in_session = _in_session()

        if in_session != was_in_session:
            _log(
                f"[POSITION MONITOR] session "
                f"{'OPEN -- watching' if in_session else 'CLOSED -- idle'}"
            )
            was_in_session = in_session

        if in_session:

            passes += 1

            if passes % heartbeat_every == 1 or heartbeat_every == 1:
                held = [t.get("symbol") for t in _open_positions()]
                _log(
                    f"[POSITION MONITOR] alive; "
                    f"{len(held)} open position(s)"
                    + (f": {', '.join(str(h) for h in held)}" if held else "")
                )

            try:
                check_once()
            except Exception as exc:
                # A monitor that dies takes protection with it. One bad pass is
                # a provider hiccup; the scan cycle is still running the full
                # engine underneath either way.
                _log(f"[POSITION MONITOR ERROR] pass failed: {exc}")

        for _ in range(wait):
            if _stopping:
                break
            time.sleep(1)

    _log("[POSITION MONITOR] stopped.")


if __name__ == "__main__":
    main()
