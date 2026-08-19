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
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from app.runtime import position_stream

ET = ZoneInfo("America/New_York")

DEFAULT_INTERVAL_SECONDS = 20

# Only inside the entry-to-close window. Positions cannot open before 09:45 and
# the scan cycle's own end-of-day force close owns everything after the bell.
FIRST_MINUTE = 9 * 60 + 30
LAST_MINUTE = 16 * 60

# Matches AUTO_PAPER_EOD_CLOSE. Imported rather than restated would be better,
# but that module pulls the whole scanner support surface into a process whose
# point is to stay small; the test below asserts the two agree.
EOD_CLOSE_TIME = dt_time(15, 55)

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


def momentum_enabled():
    """Run the momentum exit rules here too, on 1-minute data. Off by default.

    ## Why this is not simply "the scan, but faster"

    The scan builds its 15-minute frame by resampling its own 5-minute pull, and
    `resample_timeframe` emits the final bucket while it is still forming. So the
    forming 15m bar's Close is the last completed **5-minute** bar, and it
    changes once every five minutes -- the same cadence as the scan. Evaluating
    MACD, VWAP or EMA more often than that against the scan's own frame reads
    identical numbers repeatedly and cannot make any signal arrive sooner.

    Feeding the same resample from 1-minute bars changes that, and changes only
    that. Verified against ORCL over 3 sessions, 80 completed 15-minute buckets:
    Open, High, Low, Close and Volume are **identical to the last decimal** in
    every bucket, from either source. A completed bar is the same bar. What moves
    is the forming bar, which now refreshes every minute instead of every five.

    That distinction is the whole safety argument. Because completed bars do not
    change, every measured result about these rules still describes them -- the
    momentum-exit study that found holding costs -18.6R and -23.8R is about this
    same signal. A rule fires up to four minutes earlier inside a bucket; it is
    not a different rule.

    ## What is knowingly made worse

    Those four minutes are bought from a bar that has not finished. The engine's
    own note is that VWAP and MACD are bare state comparisons with no buffer and
    no confirmation, evaluated against a still-forming bar, and that on
    2026-07-29 nine of thirteen exits were soft invalidations. Refreshing that
    bar five times as often gives that failure five times as many chances.

    The existing defences are deliberately left in place rather than special
    cased: the grace zone still defers a lone momentum exit for one bar, the
    profit lock still converts a low-confidence exit into a stop ratchet, and
    trend health still guards early weak exits. This changes when the engine is
    asked, never what it is allowed to answer.
    """

    return str(os.getenv("POSITION_MONITOR_MOMENTUM_ENABLED", "")).strip().lower() in {
        "1", "true", "yes", "on"
    }


def momentum_interval_seconds():
    """How often to rebuild the frame.

    Defaults to 60 because a 1-minute bar is the input: below that the resampled
    bucket cannot have changed, so a shorter interval spends quota to re-read
    numbers that are identical, and the price-level rules on the 20s loop are
    already covering everything that moves faster.
    """

    try:
        return max(20, int(os.getenv("POSITION_MONITOR_MOMENTUM_SECONDS", "")))
    except (TypeError, ValueError):
        return 60


def eod_close_enabled():
    """Independently enforce the end-of-day close on intraday positions. Off by
    default.

    ## Why a second enforcement of a rule that already exists

    `eod_force_close_reason` already closes any position whose holding profile
    forces an end-of-day exit, at any scan from 15:55 onward, and
    `eod_close_enabled` already defaults on. When the scan runs, this does
    nothing at all.

    The value is entirely in the case where the scan does not run. Two positions
    in the archive survived their own session close:

        #149 SMCI PUT  INTRADAY  08-05 -> 08-13   8 nights   -23.67R
        #29  NVDA PUT  MULTIDAY  07-30 -> 07-31   1 night     -4.12R

    Between them they account for **27.79R of the 29.04R** ever lost beyond the
    -1.00R the stop was supposed to cap -- 96%. Every other overshoot in fifty
    trades adds up to 2.37R. SMCI was INTRADAY: policy said close it that
    afternoon, and nothing did, for eight days.

    Two independent processes are now watching, on separate machines with
    separate failure modes. Neither can close a position twice --
    `close_paper_trade` returns early once the row is no longer OPEN, which is
    the same guard the price rules already rely on.

    ## Why it is a guard and not a rule

    It closes only what the holding policy already says must be closed, only
    after `AUTO_PAPER_EOD_CLOSE`, and it invents no exit of its own. A MULTIDAY
    position is left exactly where it is -- #29 is in the list above as evidence
    that overnight carry has its own unwatched hours, not as something this
    should close.
    """

    return str(os.getenv("POSITION_MONITOR_EOD_CLOSE_ENABLED", "")).strip().lower() in {
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


# What this path is allowed to close on. Everything `evaluate_exit` can return
# that is NOT here -- HARD_STOP, HARD_TARGET, NEAR_CLOSE -- is a price level, and
# price levels belong to the 20-second loop, which reads a live quote rather than
# a bar that finished up to a minute ago.
MOMENTUM_EXIT_RULES = {"EMA", "VWAP", "MACD", "FAILED_BREAKOUT", "TIME_EXIT"}

_momentum_frames = {}


def _momentum_frame(symbol, now=None):
    """A 15-minute frame resampled from 1-minute bars, with indicators.

    Cached per symbol for `momentum_interval_seconds()`. The 20-second price loop
    calls this on every pass and the underlying bar cannot change faster than a
    minute, so without the cache this would spend three Polygon calls per symbol
    per minute to compute the same answer three times.
    """

    from app.indicators.technical_indicators import compute_indicators, get_polygon_data
    from app.utils.timeframe_resampler import resample_timeframe

    now = now if now is not None else time.monotonic()
    cached = _momentum_frames.get(symbol)

    if cached and now - cached[0] < momentum_interval_seconds():
        return cached[1]

    try:
        bars = get_polygon_data(symbol, 1, "minute", 2)
        frame = compute_indicators(
            resample_timeframe(bars, "15m"), interval="15m", symbol=symbol
        )
    except Exception as exc:
        _log(f"[POSITION MONITOR WARNING] {symbol}: 1m frame unavailable: {exc}")
        # Cached as None so a symbol whose data is down does not get retried on
        # every 20s pass for the rest of the session.
        _momentum_frames[symbol] = (now, None)
        return None

    if frame is None or frame.empty:
        _momentum_frames[symbol] = (now, None)
        return None

    _momentum_frames[symbol] = (now, frame)
    return frame


def _direction_agrees(trade):
    """Does the recorded direction match what the exit engine will infer?

    `_is_short_entry` reads the setup NAME -- SHORT, BEARISH, BREAKDOWN,
    REJECTION -- while `paper_trades.direction` holds PUT or CALL. When the two
    disagree the engine evaluates a short as a long: R inverts, and every
    momentum rule fires on the wrong side of the trade.

    The price-level path solves this by resolving `direction` first, but
    `evaluate_exit` has no such parameter and its setup name feeds other logic,
    so overriding it would be worse than not running. A trade whose two records
    disagree is therefore skipped and logged, not guessed at.
    """

    from app.exit.exit_engine import _is_short_entry

    direction = str(trade.get("direction") or "").upper()

    if direction not in {"PUT", "CALL"}:
        return True

    inferred_short = _is_short_entry(
        trade.get("entry_type") or trade.get("setup")
    )

    return inferred_short == (direction == "PUT")


def _check_momentum(trade, symbol):
    """Run the full exit engine on fresh 1-minute-sourced bars.

    Returns the verdict when it says EXIT, else None. Only an exit is acted on:
    the stop the engine proposes here is derived from an unfinished bar, and the
    20-second price loop already owns stop movement from a source that cannot be
    revised.
    """

    from app.exit.exit_engine import evaluate_exit
    from app.strategies.momentum_strategy import analyze_setup

    if not _direction_agrees(trade):
        _log(
            f"[POSITION MONITOR WARNING] {symbol}: direction "
            f"{trade.get('direction')} disagrees with setup "
            f"{trade.get('entry_type') or trade.get('setup')}; momentum skipped"
        )
        return None

    frame = _momentum_frame(symbol)

    if frame is None:
        return None

    try:
        verdict = evaluate_exit(
            frame,
            analyze_setup(frame),
            _risk_setup(trade),
            trade_state={
                **_trade_state(trade),
                "highest_price": trade.get("highest_price"),
                "lowest_price": trade.get("lowest_price"),
            },
        )
    except Exception as exc:
        _log(f"[POSITION MONITOR WARNING] {symbol}: momentum evaluation failed: {exc}")
        return None

    if not verdict or not verdict.get("exit_signal"):
        return None

    # Only the rules this path was added for. `evaluate_exit` also decides stops
    # and targets, and it decides them from `frame.iloc[-1]["Close"]` -- the last
    # completed 1-minute bar, up to a minute behind the tape. The 20-second loop
    # judges those same levels against a live quote. Letting the staler of the
    # two close a position means a stop that was touched and recovered can still
    # take the trade out a minute later.
    if verdict.get("exit_rule") not in MOMENTUM_EXIT_RULES:
        return None

    return verdict


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

        if verdict is not None:

            decisions.append((trade, price, verdict))

            _log(
                f"[POSITION MONITOR] {symbol} @ {price} ({source}) "
                f"R={verdict.get('rr_progress')} "
                f"{verdict.get('exit_code') or verdict.get('reason')}"
            )

            _act_on(trade, symbol, price, verdict)

            if verdict.get("exit"):
                continue

        # After the price rules, never instead of them. A stop is a fact about a
        # level and needs no confirmation; a momentum reading is an opinion about
        # an unfinished bar. If both fire on the same pass the stop has already
        # closed the position and this is skipped.
        if not momentum_enabled():
            continue

        momentum = _check_momentum(trade, symbol)

        if momentum is None:
            continue

        decisions.append((trade, price, momentum))

        _log(
            f"[POSITION MONITOR] {symbol} momentum exit "
            f"R={momentum.get('rr_progress')} "
            f"{momentum.get('exit_rule')}: {momentum.get('exit_reason')}"
        )

        _act_on(trade, symbol, price, _as_price_verdict(momentum))

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


def force_close_intraday(now=None):
    """Close intraday positions the session close should already have taken.

    Returns the symbols closed, and separately anything still open that this is
    not allowed to close -- a MULTIDAY carry -- so the log distinguishes "the
    guard fired" from "something is still out there".
    """

    from app.state.holding_policy import holding_policy
    from app.state.paper_trade_manager import close_paper_trade

    now = now or datetime.now(ET)

    if now.time() < EOD_CLOSE_TIME:
        return [], []

    closed, carried = [], []

    for trade in _open_positions():

        symbol = trade.get("symbol")

        if not symbol:
            continue

        if not holding_policy(trade.get("holding_profile")).force_eod_exit:
            carried.append(symbol)
            continue

        price, source = _price_for(symbol)

        if not price:
            _log(
                f"[POSITION MONITOR ERROR] {symbol} is intraday and still open "
                f"at the close, and no price is available to close it"
            )
            continue

        try:
            close_paper_trade(
                symbol,
                close_price=price,
                exit_reason=(
                    "Auto paper exit: end-of-day close (position monitor guard)"
                ),
                notify_exit=True,
            )
            closed.append(symbol)
            _log(
                f"[POSITION MONITOR] EOD GUARD closed {symbol} at {price} "
                f"({source}) -- the scan cycle had not closed it"
            )
        except Exception as exc:
            _log(f"[POSITION MONITOR ERROR] EOD close failed for {symbol}: {exc}")

    if carried:
        _log(
            "[POSITION MONITOR] held overnight by holding policy: "
            + ", ".join(str(s) for s in carried)
        )

    return closed, carried


def _as_price_verdict(verdict):
    """`evaluate_exit`'s shape, in `evaluate_price_exits`' vocabulary.

    Two engines, two dialects: `exit_signal`/`exit_reason`/`exit_fill_price`
    against `exit`/`exit_code`/`fill_price`. `_act_on` speaks the second, and
    translating here keeps the single close path rather than growing a second
    one that writes trade rows on its own terms.

    `updated_stop` is deliberately dropped. The engine proposes a trail from an
    unfinished bar, and the 20-second price loop already moves the stop from a
    source that cannot be revised; letting both write would mean a stop that
    walks backwards when the forming bar changes its mind.
    """

    return {
        "exit": True,
        "exit_code": verdict.get("exit_rule") or "MOMENTUM",
        "reason": verdict.get("exit_reason"),
        "fill_price": verdict.get("exit_fill_price"),
        "updated_stop": None,
        "rr_progress": verdict.get("rr_progress"),
    }


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

            # On the way out, not on the way in. Anything intraday still open
            # here is about to spend the night unwatched by either process,
            # which is how SMCI booked -23.67R over eight days.
            if was_in_session and not in_session and eod_close_enabled():
                try:
                    force_close_intraday()
                except Exception as exc:
                    _log(f"[POSITION MONITOR ERROR] EOD guard failed: {exc}")

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
