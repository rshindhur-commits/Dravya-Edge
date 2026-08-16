"""Is the 3% spread ceiling worth what it costs in signals?

Two measurements point opposite ways and only one can win.

Spread predicts outcomes -- it is the most reliable relationship found in this
project, and `prefer_tightest_qualified` rests entirely on it. But the ceiling is
also what starves the funnel: relaxing `OPTION_MAX_SPREAD_PCT` from 3 to 6 takes
chains that can produce a contract from 379 to 765 of 2,169, while open interest,
volume, minimum cost and quote freshness together buy under 30. **The spread
ceiling is the funnel**, and no other gate is close.

So the question is whether the extra signals are worth trading. A looser ceiling
admits contracts that cost roughly twice as much to enter and exit; a tighter one
leaves 92% of candidates unserved. Neither is obviously right and guessing on a
gate this load-bearing is how the last three days were spent.

## Method

For every ceiling, each candidate buys the tightest contract that passes *every*
other gate unchanged, then is walked to an exit under the rules shipped
2026-08-16: 1.5 ATR hard stop, a volume-flush reversal armed at +10%, a breakeven
floor at +10% and a half-give-back trail at +25%.

Pricing is anchored to the recorded quote -- entry at the ask, Black-Scholes used
only for the *ratio* between exit and entry theoretical value, exit sold at the
bid. A modelled entry is never compared against a modelled exit.

## Reading it

`total` is what the whole book earns and is the number that matters for a service
sending every signal it finds. `mean` is per trade. **A looser ceiling should win
on total and lose on mean** -- more trades, each worth less. The question is
whether the extra volume pays for the worse per-trade economics, and the top-5
strip decides whether any apparent win is real or three lucky trades.

    python tools/spread_ceiling_ab.py

Archive only, no network beyond the cached bars.
"""

import math
import pathlib
import random
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import json

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.db.connection import get_engine
from entry_quality import bars, bs, number, usable

CEILINGS = [2.0, 3.0, 4.0, 6.0, 10.0]
MIN_OI, MIN_VOLUME, MIN_COST, MAX_COST, MAX_DTE = 500, 100, 100, 1000, 30
ARM, KEEP, BE, FLUSH_ARM, FLUSH_MULT, STOP_ATR = 25.0, 0.5, 10.0, 10.0, 1.5, 1.5
BOOTSTRAP = 2000


def load():
    with get_engine().begin() as connection:
        return connection.execute(text("""
            SELECT DISTINCT ON (s.symbol, s.trading_day, s.scan_timestamp)
                   s.symbol, s.trading_day, s.scan_timestamp,
                   s.decision_payload->>'Option Liquidity Attempts' AS chain,
                   s.decision_payload AS p
            FROM scanner_snapshot s
            WHERE jsonb_typeof(s.decision_payload->'Option Liquidity Attempts')='string'
              AND s.decision_payload->>'Candidate Entry Price' IS NOT NULL
              AND s.decision_payload->>'Candidate Stop Price' IS NOT NULL
              AND s.decision_payload->>'Candidate Direction' IN ('CALL','PUT')
            ORDER BY s.symbol, s.trading_day, s.scan_timestamp
        """)).mappings().all()


def prepare(frame):
    out = frame.copy()
    close = out["Close"]
    span = pd.concat([
        out["High"] - out["Low"],
        (out["High"] - close.shift()).abs(),
        (out["Low"] - close.shift()).abs(),
    ], axis=1).max(axis=1)
    out["atr"] = span.ewm(span=14, adjust=False).mean()
    out["avgvol"] = out["Volume"].rolling(20, min_periods=5).mean()
    return out


def choose(chain, ceiling):
    """The tightest contract passing every gate but the ceiling under test."""

    best = None
    for raw in chain:
        contract = usable(raw)
        if contract is None:
            continue
        cost = number(raw.get("contract_cost"))
        dte = contract["dte"]
        if cost is None or not (MIN_COST <= cost <= MAX_COST):
            continue
        if contract["oi"] < MIN_OI or contract["volume"] < MIN_VOLUME:
            continue
        if dte > MAX_DTE or contract["spread_pct"] > ceiling:
            continue
        if best is None or contract["spread_pct"] < best["spread_pct"]:
            best = contract
    return best


def walk(option_forward, under_forward, contract, entry, hard, is_call):
    """Percent return on premium under the rules shipped 2026-08-16."""

    years_in = max(contract["dte"], 0.5) / 365.0
    theo_in = bs(entry, contract["strike"], years_in, contract["iv"], is_call)
    if theo_in <= 0.05:
        return None

    paid = contract["ask"]
    half = contract["spread_pct"] / 200.0
    peak = -100.0
    start = under_forward.index[0]

    for timestamp, bar in under_forward.iterrows():

        high, low = number(bar["High"]), number(bar["Low"])
        close = number(bar["Close"])
        if None in (high, low, close):
            continue

        minutes = (timestamp - start).total_seconds() / 60.0
        years_out = max(contract["dte"] - minutes / 390.0, 0.2) / 365.0

        def premium(spot):
            theo = bs(spot, contract["strike"], years_out, contract["iv"], is_call)
            return contract["mid"] * (theo / theo_in)

        if (low <= hard) if is_call else (high >= hard):
            return (premium(hard) * (1 - half) - paid) / paid * 100.0

        favourable = high if is_call else low
        peak = max(peak, (premium(favourable) * (1 - half) - paid) / paid * 100.0)
        gain = (premium(close) * (1 - half) - paid) / paid * 100.0

        atr, average = number(bar["atr"]), number(bar["avgvol"])
        volume = number(bar["Volume"])
        if None not in (atr, average, volume) and atr > 0 and average > 0:
            against = (close < number(bar["Open"])) if is_call else (close > number(bar["Open"]))
            if (peak >= FLUSH_ARM and against and (high - low) > atr
                    and volume > FLUSH_MULT * average):
                return gain

        floor = peak * KEEP if peak >= ARM else (0.0 if peak >= BE else None)
        if floor is not None and gain <= floor:
            return gain

    last = number(under_forward["Close"].iloc[-1])
    return (premium(last) * (1 - half) - paid) / paid * 100.0


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

    random.seed(83)
    rows = load()
    print(f"\n  {len(rows)} candidates with a recorded chain", flush=True)
    print(f"  every gate but the ceiling held fixed: OI>={MIN_OI}, vol>={MIN_VOLUME}, "
          f"${MIN_COST}-{MAX_COST}, DTE<={MAX_DTE}", flush=True)
    print(f"  exits: 1.5 ATR stop, flush armed +{FLUSH_ARM:.0f}%, "
          f"floor {BE:.0f}/{ARM:.0f}\n", flush=True)

    results = {c: defaultdict(list) for c in CEILINGS}
    spreads = {c: [] for c in CEILINGS}
    _frames = {}

    for index, record in enumerate(rows):

        if index % 400 == 0:
            print(f"    ... {index}/{len(rows)}", flush=True)

        p = record["p"] or {}
        entry = number(p.get("Candidate Entry Price"))
        stop = number(p.get("Candidate Stop Price"))
        direction = str(p.get("Candidate Direction") or "").upper()
        if None in (entry, stop) or entry <= 0:
            continue

        try:
            chain = json.loads(record["chain"] or "[]")
        except Exception:
            continue
        if not chain:
            continue

        symbol, day = record["symbol"], str(record["trading_day"])
        if (symbol, day) not in _frames:
            frame = bars(symbol, day)
            _frames[(symbol, day)] = (prepare(frame) if frame is not None
                                      and len(frame) >= 25 else None)
        frame = _frames[(symbol, day)]
        if frame is None:
            continue

        try:
            at = pd.Timestamp(record["scan_timestamp"])
            at = (at.tz_localize("America/New_York") if at.tzinfo is None
                  else at.tz_convert("America/New_York"))
        except Exception:
            continue

        forward = frame[frame.index > at]
        before = frame[frame.index <= at]
        if len(forward) < 5 or not len(before):
            continue

        atr = number(before["atr"].iloc[-1])
        if not atr or atr <= 0:
            continue

        is_call = direction == "CALL"
        hard = entry - STOP_ATR * atr if is_call else entry + STOP_ATR * atr

        for ceiling in CEILINGS:
            contract = choose(chain, ceiling)
            if contract is None:
                continue
            value = walk(None, forward, contract, entry, hard, is_call)
            if value is None:
                continue
            results[ceiling][day].append(value)
            spreads[ceiling].append(contract["spread_pct"])

    print(f"\n  {'ceiling':<10}{'trades':>8}{'days':>6}{'mean':>10}"
          f"{'-top5':>9}{'total':>11}{'win':>7}{'med spread':>12}"
          f"{'95% CI (by day)':>20}")
    print(f"  {'':-<95}")

    for ceiling in CEILINGS:
        values = [v for d in results[ceiling] for v in results[ceiling][d]]
        if len(values) < 40:
            print(f"  {ceiling:<10.1f}{len(values):>8}   too few")
            continue
        lo, hi = block_ci(results[ceiling])
        strip = st.mean(sorted(values)[:-5])
        wins = sum(1 for v in values if v > 0) / len(values) * 100
        ci = f"[{lo:+.2f}, {hi:+.2f}]" if lo is not None else "--"
        print(f"  {ceiling:<10.1f}{len(values):>8}{len(results[ceiling]):>6}"
              f"{st.mean(values):>+9.2f}%{strip:>+8.2f}%{sum(values):>+10.1f}%"
              f"{wins:>6.0f}%{st.median(spreads[ceiling]):>11.2f}%{ci:>20}")

    print("\n  A looser ceiling should win on total and lose on mean. Whether the")
    print("  extra volume pays is the question; the top-5 strip decides whether")
    print("  any apparent win is real or three lucky trades.\n")


if __name__ == "__main__":
    main()
