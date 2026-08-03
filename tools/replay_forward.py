"""Replay trading days the harness has never seen, choosing contracts itself.

Every gate so far has been a parity check: the replay is driven at live's own
scan clock and its contract is pinned to the one live bought, which validates
the decision path and says nothing about unseen history. This runs the other
way. There is no recorded trade to copy, so the scan clock is a fixed grid and
the contract comes from ``contract_selector``, which ranks a reconstructed
chain with live's own ranker and walks live's bundle.

The question it answers is therefore not "does this match" -- there is nothing
to match against -- but "does this pick contracts a person would defend": in
the DTE window the strategy claims to trade, near enough the money to carry
delta, and inside the account's cost profile. Those are reported per trade and
summarised, because a forward run that quietly selects 4-DTE lottery tickets
would otherwise show up only as a bad P&L months later.

Cost is why this takes a day range rather than a year. Roughly 150 requests
per entry: every candidate contract needs a bar and an NBBO, and quotes are not
cached. Underlying bars are, so re-running a day already fetched is cheap; the
option quotes are re-fetched every time.

    python tools/replay_forward.py --days 2026-07-29
    python tools/replay_forward.py --days 2026-07-27,2026-07-28 --symbols NVDA,ORCL
    python tools/replay_forward.py --days 2026-07-29 --cadence 15

Needs POLYGON_API_KEY and hits the network.
"""

import argparse
import json
import pathlib
import sys
import warnings
from datetime import datetime, time as clock_time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd
from dotenv import load_dotenv

# compute_indicators trips pandas' downcasting FutureWarning once per frame, so
# a day across the watchlist emits thousands of identical lines and buries the
# report. Silenced here only -- the warning is real and belongs to the
# indicator code, not to this tool.
warnings.filterwarnings(
    "ignore", category=FutureWarning, module="app.indicators.*"
)

load_dotenv()

from app.backtesting.contract_selector import SelectionConfig, select_contract
from app.backtesting.historical_greeks import parse_occ_ticker
from app.backtesting.historical_market_data import (
    HistoricalDataError,
    load_replay_frames,
)
from app.backtesting.replay_engine import ReplayConfig, build_frames, replay_day
from app.config.watchlist import WATCHLIST

ET = "America/New_York"


def scan_grid(trading_day, cadence_minutes, start="09:45", end="15:30"):
    """The scan clock a forward run has to invent.

    Parity runs use live's real ``scanner_runs`` cadence, which is uneven. No
    such record exists for a day the system did not trade, so this is a uniform
    grid -- and that is a real difference, not a detail: which 5m bar a decision
    lands on moves the entry price. Kept as a parameter so the sensitivity can
    be measured rather than assumed.
    """

    day = pd.Timestamp(trading_day).date()

    first = pd.Timestamp(
        datetime.combine(day, clock_time.fromisoformat(start))
    ).tz_localize(ET)

    last = pd.Timestamp(
        datetime.combine(day, clock_time.fromisoformat(end))
    ).tz_localize(ET)

    return list(pd.date_range(first, last, freq=f"{cadence_minutes}min"))


def make_recording_selector(frames, replay_config, selection_config, log):
    """``make_selector``, but keeping the diagnostics for the report.

    Spot has to be the close of the same candle the entry was decided on.
    ``_open_trade`` uses ``df_5m["Close"].iloc[-1]`` from ``build_frames``, so
    this recomputes exactly that rather than refetching a price, which could
    land on a different bar and quietly decide a different contract.
    """

    def _select(symbol, direction, moment):

        raw = frames.get(symbol)

        if raw is None or raw.empty:

            return None

        df_5m = build_frames(raw, moment, symbol, replay_config)[0]

        if df_5m is None or df_5m.empty:

            return None

        spot = float(df_5m["Close"].iloc[-1])

        try:

            ticker, contract, diagnostics = select_contract(
                symbol, direction, moment, spot, selection_config
            )

        except HistoricalDataError as exc:

            log.append(
                {
                    "symbol": symbol,
                    "moment": moment,
                    "ticker": None,
                    "error": str(exc)[:120],
                }
            )

            return None

        log.append(
            {
                "symbol": symbol,
                "moment": moment,
                "spot": spot,
                "ticker": ticker,
                "contract": contract,
                "diagnostics": diagnostics,
            }
        )

        # A watchlist day takes half an hour, almost all of it in indicator
        # recomputation with nothing on stdout. Entry attempts are the only
        # interesting moments, so they are also the progress indicator.
        print(
            f"    {_et_label(moment)} {symbol:6} {direction:4} -> "
            f"{ticker or 'no contract'}",
            flush=True,
        )

        return ticker

    return _select


def _et_label(moment):

    return pd.Timestamp(moment).tz_convert(ET).strftime("%H:%M")


def describe(ticker, spot):
    """DTE, strike and how far from the money -- the defensibility check."""

    spec = parse_occ_ticker(ticker)

    return spec, abs(spec["strike"] - spot) / spot * 100.0


def run_day(trading_day, symbols, cadence, selection_config):

    replay_config = ReplayConfig()

    frames = {}

    for symbol in symbols:

        try:

            frames[symbol] = load_replay_frames(
                symbol, trading_day, lookback_days=replay_config.lookback_days
            )

        except HistoricalDataError as exc:

            print(f"  {symbol}: no frames ({str(exc)[:70]})")

    log = []
    replay_config.contract_selector = make_recording_selector(
        frames, replay_config, selection_config, log
    )

    result = replay_day(
        symbols,
        trading_day,
        scan_grid(trading_day, cadence),
        config=replay_config,
        raw_frames=frames,
    )

    # Positions still open at the close are reported too. Dropping them would
    # flatter the result by silently discarding whatever was going badly enough
    # not to have hit an exit rule yet.
    still_open = result["open"]

    if still_open:

        print(f"  {len(still_open)} still open at the close")

    return result["closed"] + still_open, log


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", required=True, help="comma-separated YYYY-MM-DD"
    )
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--cadence", type=int, default=5)
    parser.add_argument("--max-priced", type=int, default=None)
    parser.add_argument(
        "--out",
        default=None,
        help="write trades and selection attempts here as JSON; a run costs "
        "roughly half an hour a day, so it should not have to be repeated to "
        "ask a second question of it",
    )
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",")]
        if args.symbols
        else list(WATCHLIST)
    )

    selection_config = SelectionConfig()

    if args.max_priced is not None:

        selection_config.max_priced_contracts = args.max_priced

    print(
        f"{len(days)} day(s), {len(symbols)} symbols, {args.cadence}m cadence\n"
    )

    all_trades = []
    all_logs = []

    for day in days:

        print(f"--- {day} ---")

        trades, log = run_day(day, symbols, args.cadence, selection_config)

        all_trades.extend(trades)
        all_logs.extend(log)

        print(f"  {len(trades)} trades, {len(log)} selection attempts")

    print(f"\n{'='*104}")
    print(
        f"{'sym':6}{'scan':18}{'dir':5}{'contract':24}{'dte':>5}{'OTM%':>7}"
        f"{'cost':>8}{'fill':>7}{'exit':>7}{'R':>7}  {'rule':<16}"
    )

    for trade in all_trades:

        if not trade.option_ticker:

            print(
                f"{trade.symbol:6}{trade.scan_id[-8:]:18}{trade.direction:5}"
                f"{'(no contract)':24}"
            )
            continue

        spec, otm = describe(trade.option_ticker, trade.entry_price)
        dte = (spec["expiry"] - trade.entry_time.date()).days
        fill = trade.option_entry_fill or 0

        # An open position has no exit and no R. Printing 0.00 for both would
        # read as a flat closed trade, which is the one thing it is not.
        open_at_close = trade.r_multiple is None

        exit_cell = "-" if open_at_close else f"{trade.option_exit_fill or 0:.2f}"
        r_cell = "-" if open_at_close else f"{trade.r_multiple:.2f}"
        rule_cell = (
            "OPEN AT CLOSE"
            if open_at_close
            else str(trade.exit_rule or trade.exit_reason or "-")
        )

        print(
            f"{trade.symbol:6}{trade.scan_id[-8:]:18}{trade.direction:5}"
            f"{trade.option_ticker:24}{dte:>5}{otm:>7.1f}"
            f"{fill*100:>8.0f}{fill:>7.2f}"
            f"{exit_cell:>7}{r_cell:>7}"
            f"  {rule_cell:<16}"
        )

    _summarise(all_trades, all_logs)

    if args.out:

        _persist(args.out, all_trades, all_logs)


def _persist(path, trades, log):

    destination = pathlib.Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "trades": [
            {
                "symbol": trade.symbol,
                "scan_id": trade.scan_id,
                "direction": trade.direction,
                "entry_type": trade.entry_type,
                "entry_time": str(trade.entry_time),
                "entry_price": trade.entry_price,
                "stop_loss": trade.stop_loss,
                "take_profit": trade.take_profit,
                "option_ticker": trade.option_ticker,
                "option_entry_fill": trade.option_entry_fill,
                "option_exit_fill": trade.option_exit_fill,
                "exit_time": str(trade.exit_time) if trade.exit_time else None,
                "exit_reason": trade.exit_reason,
                "exit_rule": trade.exit_rule,
                "r_multiple": trade.r_multiple,
                "mfe_r": trade.mfe_r,
                "mae_r": trade.mae_r,
                "open_at_close": trade.r_multiple is None,
            }
            for trade in trades
        ],
        "selection_attempts": [
            {
                "symbol": entry.get("symbol"),
                "moment": str(entry.get("moment")),
                "spot": entry.get("spot"),
                "ticker": entry.get("ticker"),
                "error": entry.get("error"),
                # The contract dict carries pandas timestamps and numpy scalars
                # that json cannot encode, and the diagnostics are what a later
                # question actually needs, so only those are kept.
                "diagnostics": _jsonable(entry.get("diagnostics")),
            }
            for entry in log
        ],
    }

    destination.write_text(json.dumps(payload, indent=2, default=str))

    print(f"\nwrote {destination}")


def _jsonable(value):

    if isinstance(value, dict):

        return {str(k): _jsonable(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):

        return [_jsonable(item) for item in value]

    if isinstance(value, (str, bool, type(None))):

        return value

    if isinstance(value, (int, float)):

        return value

    return str(value)


def _summarise(trades, log):

    print(f"\n{'='*104}")

    resolved = [entry for entry in log if entry.get("ticker")]
    failed = [entry for entry in log if not entry.get("ticker")]

    print(f"selection attempts: {len(log)}   resolved: {len(resolved)}")

    if failed:

        reasons = {}

        for entry in failed:

            diagnostics = entry.get("diagnostics") or {}

            reason = (
                "request error"
                if entry.get("error")
                else "no liquid contract"
                if diagnostics.get("no_liquid_contract")
                else "rejected by ranker"
                if diagnostics.get("rejected_by_ranker")
                else "empty chain"
            )
            reasons[reason] = reasons.get(reason, 0) + 1

        print(
            "  unresolved: "
            + ", ".join(f"{k} x{v}" for k, v in sorted(reasons.items()))
        )

        # Which gate actually binds. "No liquid contract" on its own invites
        # the assumption that a small account cannot afford anything, and on
        # the first forward day that was wrong: volume rejected far more
        # candidates than cost did, on every symbol except QQQ. Worth knowing
        # before tuning the wrong threshold.
        codes = {}

        for entry in failed:

            for attempt in (entry.get("diagnostics") or {}).get(
                "liquidity_attempts"
            ) or []:

                code = str(attempt.get("code"))
                codes[code] = codes.get(code, 0) + 1

        if codes:

            total = sum(codes.values())

            print(f"  why candidates were rejected ({total} across the walk):")

            for code, count in sorted(codes.items(), key=lambda kv: -kv[1]):

                print(f"     {code:<28} {count:>5}  {count/total*100:>5.1f}%")

    slots = {}

    for entry in resolved:

        slot = (entry.get("diagnostics") or {}).get("selected_from") or "-"
        slots[slot] = slots.get(slot, 0) + 1

    if slots:

        print(
            "  picked from: "
            + ", ".join(
                f"{k} x{v}"
                for k, v in sorted(slots.items(), key=lambda kv: -kv[1])
            )
        )

    traded = [trade for trade in trades if trade.option_ticker]

    if not traded:

        print("\nno contracts traded")

        return

    dtes = []
    otms = []
    costs = []

    for trade in traded:

        spec, otm = describe(trade.option_ticker, trade.entry_price)
        dtes.append((spec["expiry"] - trade.entry_time.date()).days)
        otms.append(otm)
        costs.append((trade.option_entry_fill or 0) * 100)

    # The defensibility check. A forward run that drifts to near-dated,
    # far-out-of-the-money contracts is failing in the way that looks like bad
    # luck rather than a bad selector, so the distribution is stated outright.
    print(
        f"\ncontracts traded: {len(traded)}"
        f"\n  DTE      min {min(dtes):>3}  median {sorted(dtes)[len(dtes)//2]:>3}"
        f"  max {max(dtes):>3}"
        f"\n  OTM%     min {min(otms):>5.1f}  median "
        f"{sorted(otms)[len(otms)//2]:>5.1f}  max {max(otms):>5.1f}"
        f"\n  cost $   min {min(costs):>5.0f}  median "
        f"{sorted(costs)[len(costs)//2]:>5.0f}  max {max(costs):>5.0f}"
    )

    closed = [t for t in traded if t.r_multiple is not None]

    if not closed:

        return

    rs = [t.r_multiple for t in closed]
    wins = [r for r in rs if r > 0]

    print(
        f"\nclosed: {len(closed)}   wins {len(wins)}   "
        f"mean R {sum(rs)/len(rs):+.2f}   total R {sum(rs):+.2f}"
    )

    # R is measured on the underlying against the stop distance, which is not
    # the money. This system's known failure mode is exactly that gap: stops of
    # a fraction of a percent against option spreads of several percent, so a
    # run can show good R while every contract lost. The premium actually paid
    # and received is the number worth reading, and these fills already cross
    # the spread when use_spread_fills is on.
    priced = [
        t for t in closed if t.option_entry_fill and t.option_exit_fill is not None
    ]

    if not priced:

        return

    pnls = [
        (t.option_exit_fill - t.option_entry_fill) / t.option_entry_fill * 100.0
        for t in priced
    ]
    option_wins = [p for p in pnls if p > 0]

    print(
        f"option P&L on {len(priced)} closed contracts (fills, spread crossed):"
        f"\n  mean {sum(pnls)/len(pnls):+.1f}%   median "
        f"{sorted(pnls)[len(pnls)//2]:+.1f}%   wins {len(option_wins)}"
        f"\n  best {max(pnls):+.1f}%   worst {min(pnls):+.1f}%"
    )

    # One day of one watchlist is a handful of trades, and a couple of them
    # carry the total. Stated here so the summary cannot be read as a result.
    ranked_pnls = sorted(pnls, reverse=True)
    top_two = sum(ranked_pnls[:2])

    print(
        f"  top 2 trades contribute {top_two:+.1f}% of "
        f"{sum(pnls):+.1f}% total"
    )


if __name__ == "__main__":

    main()
