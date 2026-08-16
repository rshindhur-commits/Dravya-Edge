"""Does holding through the noise beat bailing on the first wiggle?

The operator's complaint, and it has never been tested: the app exits on small
changes instead of letting a trend develop. The live book agrees -- exits fire on
`MACD bearish crossover` and `EMA9 invalidation` with a **median hold of ten
minutes**, on contracts with weeks of life left.

What was tested before is a different thing. §11 held for a fixed number of days
with **no exit rule at all**, which answers "does time help" rather than "does a
patient exit rule help". A blind multi-day hold and a rule that ignores wiggles
but still cuts a real reversal are not the same product.

## The rules compared

Every arm enters at the same signal, on the same contract, and differs only in
what ends the trade.

    ema9         close crosses back through EMA9 -- approximates the app today
    ema20        the same idea, but a slower average that ignores small pokes
    hard_stop    no indicator exit at all; 1.5 ATR against, else the close
    trail_1atr   give back 1 ATR from the best price reached
    trail_2atr   give back 2 ATR -- deliberately loose
    close        hold to the session close, 1.5 ATR hard stop underneath

`trail_2atr` and `close` are the operator's description made literal: sit through
noise, leave only on a real reversal or a real loss.

## What is reported

Mean and median option return, but also **median hold** and the share of trades
that ever reach a decent gain. A patient rule should show a longer hold and fewer
tiny exits. If it also shows a worse mean, the noise being exited was real
information and the current behaviour is right.

Every arm is priced honestly: bought at the ask, sold at the bid, with the spread
from the contract's own recorded quote.

    python tools/exit_patience.py

Archive only, no network beyond the cached bars.
"""

import pathlib
import random
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import json
from collections import defaultdict

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from entry_quality import bars, bs, load, number, usable

RULES = ["ema9", "ema20", "hard_stop", "trail_1atr", "trail_2atr", "close"]
HARD_STOP_ATR = 1.5
BOOTSTRAP = 2000


def enrich(frame):
    out = frame.copy()
    close = out["Close"]
    out["ema9"] = close.ewm(span=9, adjust=False).mean()
    out["ema20"] = close.ewm(span=20, adjust=False).mean()
    span = pd.concat([
        out["High"] - out["Low"],
        (out["High"] - close.shift()).abs(),
        (out["Low"] - close.shift()).abs(),
    ], axis=1).max(axis=1)
    out["atr"] = span.ewm(span=14, adjust=False).mean()
    return out


def run_rule(forward, rule, entry, atr, is_call):
    """Exit price and minutes held under one rule."""

    hard = entry - HARD_STOP_ATR * atr if is_call else entry + HARD_STOP_ATR * atr
    best = entry
    start = forward.index[0]

    for timestamp, bar in forward.iterrows():

        high, low = number(bar["High"]), number(bar["Low"])
        close = number(bar["Close"])
        if high is None or low is None or close is None:
            continue
        minutes = (timestamp - start).total_seconds() / 60.0

        # A real loss ends every arm, so no rule is allowed to win by simply
        # refusing to ever cut a trade.
        if (low <= hard) if is_call else (high >= hard):
            return hard, minutes

        if rule in ("ema9", "ema20"):
            reference = number(bar[rule])
            if reference is not None:
                if (close < reference) if is_call else (close > reference):
                    return close, minutes

        elif rule in ("trail_1atr", "trail_2atr"):
            multiple = 1.0 if rule == "trail_1atr" else 2.0
            best = max(best, high) if is_call else min(best, low)
            level = best - multiple * atr if is_call else best + multiple * atr
            if (low <= level) if is_call else (high >= level):
                return level, minutes

    last = forward.index[-1]
    return (number(forward["Close"].iloc[-1]),
            (last - start).total_seconds() / 60.0)


def price(contract, entry, exit_price, minutes, is_call):
    years_in = max(contract["dte"], 0.5) / 365.0
    years_out = max(contract["dte"] - minutes / 390.0, 0.2) / 365.0
    theo_in = bs(entry, contract["strike"], years_in, contract["iv"], is_call)
    theo_out = bs(exit_price, contract["strike"], years_out, contract["iv"], is_call)
    if theo_in <= 0.05:
        return None
    exit_mid = contract["mid"] * (theo_out / theo_in)
    paid = contract["ask"]
    got = exit_mid * (1 - contract["spread_pct"] / 200.0)
    return (got - paid) / paid * 100.0


def block_ci(by_day, level=0.95):
    days = list(by_day)
    if len(days) < 5:
        return None, None
    means = []
    for _ in range(BOOTSTRAP):
        pool = [v for d in (random.choice(days) for _ in days) for v in by_day[d]]
        if pool:
            means.append(st.mean(pool))
    if not means:
        return None, None
    means.sort()
    return means[int((1 - level) / 2 * len(means))], means[int((1 + level) / 2 * len(means)) - 1]


def main():

    random.seed(71)
    rows = load()

    returns = {r: defaultdict(list) for r in RULES}
    holds = {r: [] for r in RULES}
    used = 0
    _enriched = {}

    for record in rows:

        p = record["p"] or {}
        entry = number(p.get("Candidate Entry Price"))
        direction = str(p.get("Candidate Direction") or "").upper()
        if entry is None or entry <= 0 or direction not in {"CALL", "PUT"}:
            continue

        try:
            chain = json.loads(record["chain"] or "[]")
        except Exception:
            continue
        pool = [c for c in (usable(k) for k in chain) if c]
        liquid = [c for c in pool if c["oi"] >= 500 and c["volume"] >= 50]
        if not liquid:
            continue
        contract = min(liquid, key=lambda c: c["spread_pct"])

        symbol, day = record["symbol"], str(record["trading_day"])
        if (symbol, day) not in _enriched:
            frame = bars(symbol, day)
            _enriched[(symbol, day)] = (enrich(frame) if frame is not None
                                        and len(frame) >= 25 else None)
        frame = _enriched[(symbol, day)]
        if frame is None:
            continue

        try:
            at = pd.Timestamp(record["scan_timestamp"])
            at = (at.tz_localize("America/New_York") if at.tzinfo is None
                  else at.tz_convert("America/New_York"))
        except Exception:
            continue

        forward = frame[frame.index > at]
        if len(forward) < 6:
            continue

        atr = number(frame[frame.index <= at]["atr"].iloc[-1]) if len(
            frame[frame.index <= at]) else None
        if not atr or atr <= 0:
            continue

        is_call = direction == "CALL"
        used += 1

        for rule in RULES:
            exit_price, minutes = run_rule(forward, rule, entry, atr, is_call)
            if exit_price is None or exit_price <= 0:
                continue
            ret = price(contract, entry, exit_price, minutes, is_call)
            if ret is None:
                continue
            returns[rule][day].append(ret)
            holds[rule].append(minutes)

    print(f"\n  candidates : {used}")
    print(f"  every arm enters identically; only the exit rule differs")
    print(f"  a 1.5 ATR hard stop ends every arm, so patience cannot win by")
    print(f"  simply never cutting a loser\n")

    if used < 100:
        print("  too few; stopping.\n")
        return

    print(f"  {'rule':<12}{'n':>7}{'mean':>10}{'95% CI (by day)':>19}"
          f"{'median':>10}{'win':>7}{'hold':>9}{'>=+10%':>9}{'<=-25%':>9}")
    print(f"  {'':-<92}")

    for rule in RULES:
        values = [v for d in returns[rule] for v in returns[rule][d]]
        if len(values) < 50:
            continue
        lo, hi = block_ci(returns[rule])
        ci = f"[{lo:+.2f}, {hi:+.2f}]" if lo is not None else "--"
        wins = sum(1 for v in values if v > 0) / len(values) * 100
        big = sum(1 for v in values if v >= 10.0) / len(values) * 100
        bad = sum(1 for v in values if v <= -25.0) / len(values) * 100
        print(f"  {rule:<12}{len(values):>7}{st.mean(values):>+9.2f}%{ci:>19}"
              f"{st.median(values):>+9.2f}%{wins:>6.0f}%"
              f"{st.mean(holds[rule]):>8.0f}m{big:>8.0f}%{bad:>8.0f}%")

    print("\n  hold is the average minutes in the trade. `>=+10%` is how often the")
    print("  trade ended at a gain worth taking; `<=-25%` is how often patience")
    print("  cost real money. A patient rule earns its place only if it lifts the")
    print("  first without lifting the second as much.\n")


if __name__ == "__main__":
    main()
