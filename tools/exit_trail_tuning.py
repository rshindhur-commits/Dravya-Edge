"""Protect the gain without capping the winner.

`giveback_50` armed at a 10% gain looked excellent on the round-trip count and is
wrong. On a real PLTR call it exited at +4.8% out of a trade that went on to
+69.3%, because giving back half of a 16% peak means selling at 8% -- and on an
option, 16% is noise. Minimising round-trips is easy if you exit early enough;
that is not the product.

The operator's requirement, stated exactly: **do not cap the winner** -- the
subscriber decides when to take profit -- but **do signal an exit when the profit
is being lost, or when the position is running into a heavy loss.**

So the rule has to survive noise and still react to a genuine reversal.

## What is varied

    arm_at        how much gain must exist before protection engages at all
    keep          the share of the peak gain the rule tries to hold on to
    trail_atr     an alternative anchored to the UNDERLYING rather than the
                  option, since the underlying is far less noisy -- a 1.5 ATR
                  reversal in the stock is a real turn, while 16% on an option
                  can be one bad print

## How each arm is judged

Round-trip rate alone is a trap, because exiting instantly scores perfectly. Two
figures are reported beside it:

    capture     of the best gain the trade ever offered, the share still
                available at the exit signal -- the anti-cap measure
    big_win     how often the trade was still open at +25% or better

An arm is only interesting if it holds round-trips near zero **while keeping
capture high**. Those pull against each other, and the balance is the answer.

    python tools/exit_trail_tuning.py

Real option bars for the 41 traded contracts. Run outside 09:30-16:00 ET.
"""

import pathlib
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.backtesting.historical_market_data import fetch_bars
from app.db.connection import get_engine

ARMS = [
    ("giveback_50 @10", "give", 10.0, 0.50, None),
    ("giveback_50 @25", "give", 25.0, 0.50, None),
    ("giveback_50 @40", "give", 40.0, 0.50, None),
    ("giveback_33 @25", "give", 25.0, 0.67, None),
    ("giveback_25 @25", "give", 25.0, 0.75, None),
    ("trail 1.5 ATR",   "trail", None, None, 1.5),
    ("trail 2.5 ATR",   "trail", None, None, 2.5),
    ("hold to close",   "none", None, None, None),
]
HARD_STOP_ATR = 1.5


def number(value):
    try:
        result = float(value)
        return None if result != result else result
    except (TypeError, ValueError):
        return None


def main():

    with get_engine().begin() as connection:
        rows = connection.execute(text("""
            SELECT symbol, direction, option_ticker, entry_price, option_entry_mid,
                   option_close_mid, pnl_pct, days_held, opened_at
            FROM paper_trades
            WHERE status='CLOSED' AND option_ticker IS NOT NULL
              AND option_entry_mid > 0
            ORDER BY opened_at
        """)).mappings().all()

    results = {label: [] for label, *_ in ARMS}
    captures = {label: [] for label, *_ in ARMS}
    peaks_all, actuals = [], []

    for record in rows:

        if (number(record["days_held"]) or 1) > 1:
            continue

        paid = number(record["option_entry_mid"])
        day = str(pd.Timestamp(record["opened_at"]).tz_convert("America/New_York").date())
        try:
            opt = fetch_bars(record["option_ticker"], day, day, multiplier=5, timespan="minute")
        except Exception:
            continue
        if opt is None or not len(opt):
            continue
        opt = opt.copy()
        opt.index = pd.to_datetime(opt.index, utc=True).tz_convert("America/New_York")
        opt = opt.between_time("09:30", "16:00")
        opened = pd.Timestamp(record["opened_at"]).tz_convert("America/New_York")
        forward = opt[opt.index >= opened]
        if len(forward) < 6:
            continue

        under = None
        try:
            u = fetch_bars(record["symbol"], day, day)
            u.index = u.index.tz_convert("America/New_York")
            under = u.between_time("09:30", "16:00")
        except Exception:
            pass

        atr = None
        if under is not None and len(under) > 15:
            before = under[under.index <= opened]
            if len(before) > 15:
                close = before["Close"]
                span = pd.concat([
                    before["High"] - before["Low"],
                    (before["High"] - close.shift()).abs(),
                    (before["Low"] - close.shift()).abs(),
                ], axis=1).max(axis=1)
                atr = number(span.ewm(span=14, adjust=False).mean().iloc[-1])

        is_call = str(record["direction"] or "").upper() == "CALL"
        entry_spot = number(record["entry_price"])
        hard = None
        if entry_spot and atr:
            hard = (entry_spot - HARD_STOP_ATR * atr if is_call
                    else entry_spot + HARD_STOP_ATR * atr)

        best_gain = (number(forward["High"].max()) - paid) / paid * 100.0
        peaks_all.append(best_gain)
        got = number(record["option_close_mid"])
        actuals.append(((got - paid) / paid * 100.0) if got else
                       (number(record["pnl_pct"]) or 0.0))

        under_fwd = under[under.index >= opened] if under is not None else None

        for label, kind, arm_at, keep, trail_mult in ARMS:

            run_peak = -100.0
            best_spot = None
            exited = None

            for timestamp, bar in forward.iterrows():

                high, close = number(bar["High"]), number(bar["Close"])
                if high is None or close is None:
                    continue
                run_peak = max(run_peak, (high - paid) / paid * 100.0)
                gain = (close - paid) / paid * 100.0

                # Heavy-loss protection, on every arm.
                if hard is not None and under_fwd is not None:
                    window = under_fwd[under_fwd.index <= timestamp]
                    if len(window):
                        lo, hi = number(window["Low"].iloc[-1]), number(window["High"].iloc[-1])
                        if lo is not None and hi is not None:
                            if (lo <= hard) if is_call else (hi >= hard):
                                exited = gain
                                break

                if kind == "give" and run_peak >= arm_at:
                    if gain <= run_peak * keep:
                        exited = gain
                        break

                elif kind == "trail" and under_fwd is not None and atr:
                    window = under_fwd[under_fwd.index <= timestamp]
                    if len(window):
                        spot_best = (number(window["High"].max()) if is_call
                                     else number(window["Low"].min()))
                        if spot_best is not None:
                            best_spot = spot_best
                            level = (best_spot - trail_mult * atr if is_call
                                     else best_spot + trail_mult * atr)
                            lo, hi = number(window["Low"].iloc[-1]), number(window["High"].iloc[-1])
                            if lo is not None and hi is not None:
                                if (lo <= level) if is_call else (hi >= level):
                                    exited = gain
                                    break

            if exited is None:
                exited = (number(forward["Close"].iloc[-1]) - paid) / paid * 100.0
            results[label].append(exited)
            if best_gain > 0:
                captures[label].append(max(0.0, exited) / best_gain * 100.0)

    n = len(peaks_all)
    print(f"\n  single-day trades with option bars : {n}")
    print(f"  every arm carries the same 1.5 ATR heavy-loss stop\n")
    if n < 15:
        print("  too few; stopping.\n")
        return

    def stats(values, caps):
        green = [i for i, p in enumerate(peaks_all) if p >= 10.0]
        trip = (sum(1 for i in green if values[i] <= 0) / len(green) * 100) if green else 0.0
        big = sum(1 for i, p in enumerate(peaks_all) if p >= 25.0 and values[i] >= 25.0)
        avail = sum(1 for p in peaks_all if p >= 25.0)
        return (st.mean(values), sum(values), trip,
                st.mean(caps) if caps else 0.0,
                (big / avail * 100) if avail else 0.0)

    print(f"  {'rule':<18}{'mean':>9}{'total':>10}{'ROUNDTRIP':>11}"
          f"{'capture':>10}{'big win kept':>14}")
    print(f"  {'':-<74}")

    m, t, tr, cap, big = stats(actuals, [max(0.0, a) / p * 100 for a, p in
                                         zip(actuals, peaks_all) if p > 0])
    print(f"  {'ACTUAL (app)':<18}{m:>+8.2f}%{t:>+9.1f}%{tr:>10.0f}%{cap:>9.0f}%{big:>13.0f}%")

    for label, *_ in ARMS:
        m, t, tr, cap, big = stats(results[label], captures[label])
        print(f"  {label:<18}{m:>+8.2f}%{t:>+9.1f}%{tr:>10.0f}%{cap:>9.0f}%{big:>13.0f}%")

    print(f"\n  ROUNDTRIP  of trades ever up 10%, the share ending at or below zero")
    print(f"  capture    share of the best gain still on the table at the exit")
    print(f"  big win    of trades that reached +25%, how often the arm was still")
    print(f"             holding at +25% or better -- the anti-cap measure")
    print(f"\n  Exiting instantly scores a perfect round-trip and a terrible")
    print(f"  capture. The arm worth having holds both.\n")


if __name__ == "__main__":
    main()
