"""Do we select for direction when we should be selecting for movement?

§5.11 found the app's entries reach +10% on the option **less often than a random
moment** -- 15.7% against 20.7%. That is not an absent signal, it is a negative
one: switching the entry logic off would do better than leaving it on.

The explanation this tests. The filter stack -- EMA cross, MACD confirmation,
volume, setup score, timing gate, avoid-chasing, minimum RR -- all pulls in one
direction. Together it selects **clean, orderly, calm** setups. Calm is the worst
possible state for a bought option, which needs the underlying to travel. The
filters may be selecting precisely the moments where the option cannot pay.

## The question every previous test got wrong

Everything so far asked **"which candidates win"** and found nothing. That is the
wrong question for this instrument. A 50/50 trade that moves hard is a good option
trade; a 60/40 trade that barely budges is a bad one. Direction and movement are
different properties and only one of them has been tested.

`atr_pct` was measured in §15 as a predictor of *winning* and failed. It has never
been measured as a predictor of **reaching +10%**, which is the outcome the
subscriber actually needs.

## What is measured

For each candidate, conditions at the moment of the signal, then the option's
best sellable gain before its stop. Candidates are split into quintiles by each
condition and judged on **the share reaching +10%**, not on mean return.

    atr_pct        realised volatility, ATR as a percent of price
    rvol           recent volume against the session average
    range_today    session range so far, in ATR units
    ext_ema9       distance from EMA9 in ATR units -- the "clean setup" measure
    minute         minutes since the open
    iv             the chain's own implied volatility

If movement is the missing lever, the top volatility quintile should clear +10%
far more often than the bottom, **and beat the 20.7% random baseline**. Beating
the app's own 15.7% is not enough; the bar is random, because random is what the
current logic already loses to.

    python tools/entry_movement.py

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

from entry_quality import bars, load, number, peak_before_stop, usable
from exit_patience import enrich

TARGET = 10.0
QUINTILES = 5
RANDOM_BASELINE = 20.7      # §5.11, matched-horizon random entry


def main():

    random.seed(79)
    rows = load()
    records = []
    _enriched = {}

    for record in rows:

        p = record["p"] or {}
        entry = number(p.get("Candidate Entry Price"))
        stop = number(p.get("Candidate Stop Price"))
        direction = str(p.get("Candidate Direction") or "").upper()
        if None in (entry, stop) or direction not in {"CALL", "PUT"} or entry <= 0:
            continue
        risk = abs(entry - stop)
        if risk <= 0:
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

        history = frame[frame.index <= at]
        forward = frame[frame.index > at]
        if len(history) < 12 or len(forward) < 5:
            continue

        last = history.iloc[-1]
        atr = number(last["atr"])
        close = number(last["Close"])
        ema9 = number(last["ema9"])
        if not atr or atr <= 0 or not close or ema9 is None:
            continue

        is_call = direction == "CALL"
        peak, _stopped = peak_before_stop(forward, contract, entry, stop, is_call)
        if peak is None:
            continue

        recent = history["Volume"].tail(6).mean()
        average = history["Volume"].mean() or 1.0
        session_range = number(history["High"].max()) - number(history["Low"].min())

        records.append({
            "day": day,
            "hit": peak >= TARGET,
            "atr_pct": atr / close * 100.0,
            "rvol": recent / average if average else 1.0,
            "range_today": session_range / atr if atr else 0.0,
            "ext_ema9": abs(close - ema9) / atr,
            "minute": (history.index[-1] - history.index[0]).total_seconds() / 60.0,
            "iv": contract["iv"],
        })

    print(f"\n  candidates : {len(records)}")
    if len(records) < 300:
        print("  too few; stopping.\n")
        return

    overall = sum(1 for r in records if r["hit"]) / len(records) * 100
    print(f"  reach +{TARGET:.0f}% overall : {overall:.1f}%")
    print(f"  random baseline      : {RANDOM_BASELINE:.1f}%  (5.11, matched horizon)")
    print(f"\n  The bar is the random baseline, not the app's own rate. A quintile")
    print(f"  only matters if it clears {RANDOM_BASELINE:.1f}%.\n")

    names = ["atr_pct", "rvol", "range_today", "ext_ema9", "minute", "iv"]

    def quintiles(sample, name):
        ordered = sorted(sample, key=lambda r: r[name])
        size = len(ordered) // QUINTILES
        rates = []
        for q in range(QUINTILES):
            chunk = (ordered[q * size:(q + 1) * size] if q < QUINTILES - 1
                     else ordered[q * size:])
            if not chunk:
                return None
            rates.append(sum(1 for r in chunk if r["hit"]) / len(chunk) * 100)
        return rates

    days = sorted({r["day"] for r in records})
    cut = days[len(days) // 2]
    disc = [r for r in records if r["day"] < cut]
    hold = [r for r in records if r["day"] >= cut]
    print(f"  days {len(days)}   discovery {len(disc)} (< {cut})   holdout {len(hold)}\n")

    for title, sample in (("ALL", records), (f"DISCOVERY < {cut}", disc),
                          (f"HOLDOUT >= {cut}", hold)):
        print(f"  {title}")
        print(f"  {'condition':<14}{'Q1 (low)':>11}{'Q2':>9}{'Q3':>9}{'Q4':>9}"
              f"{'Q5 (high)':>11}{'spread':>9}   beats random")
        print(f"  {'':-<93}")
        for name in names:
            rates = quintiles(sample, name)
            if rates is None:
                continue
            best = max(rates)
            flag = f"Q{rates.index(best) + 1} at {best:.1f}%" if best > RANDOM_BASELINE else ""
            cells = "".join(f"{r:>10.1f}%" for r in rates)
            print(f"  {name:<14}{cells}{best - min(rates):>8.1f}   {flag}")
        print()

    print("\n  If movement is the missing lever, a volatility quintile should clear")
    print("  the random baseline. If none does, then selecting for movement fails")
    print("  the same way selecting for direction did, and the entry cannot be")
    print("  rescued by choosing among these candidates at all.\n")


if __name__ == "__main__":
    main()
