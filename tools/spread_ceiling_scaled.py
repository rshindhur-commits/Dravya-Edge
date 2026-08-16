"""Can the app know, at scan time, when a wide spread is worth paying?

`spread_ceiling_by_mover.py` found that the right ceiling depends on how far the
underlying travels: on sessions ranging over 5% a ceiling of 6 is the only
setting whose mean survives the top-5 strip, while on 2-3.5% sessions that same
ceiling loses thirteen times more than a ceiling of 3.

**That result is built on hindsight.** The session's range is known at the bell,
not at the signal, so it cannot filter anything. This asks the only question that
makes the finding usable: does a quantity available *at the moment of the scan*
sort candidates the same way?

The candidate proxy is **ATR as a percent of price**, measured on the bars
already closed before the signal. It is the app's own volatility estimate, it is
computed on every scan today, and it is what a volatility-scaled ceiling would
have to key off.

## What would falsify this

If the ATR bands do not reproduce the pattern -- loose winning at the top, tight
winning in the middle -- then session range was a property discoverable only
afterwards and no scan-time rule can capture it. The honest outcome is then to
leave the ceiling alone, and this file records that it was checked.

The bands are chosen to hold roughly the shape of the range bands rather than to
maximise anything, and the split is reported so the choice can be audited.

    python tools/spread_ceiling_scaled.py

Archive only, no network beyond the cached bars.
"""

import json
import pathlib
import random
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from entry_quality import bars, number, usable
from spread_ceiling_ab import (
    CEILINGS,
    MAX_COST,
    MAX_DTE,
    MIN_COST,
    MIN_DTE,
    MIN_OI,
    MIN_VOLUME,
    STOP_ATR,
    load,
    prepare,
    walk,
)

# ATR as a percent of price, on bars closed before the signal.
BANDS = [(0.0, 0.25), (0.25, 0.40), (0.40, 0.60), (0.60, 100.0)]


def band_of(value):
    for low, high in BANDS:
        if low <= value < high:
            return (low, high)
    return None


def label(band):
    low, high = band
    return f"ATR {low:.2f}-{high:.2f}%" if high < 100 else f"ATR {low:.2f}%+"


def main():

    random.seed(83)
    rows = load()

    print(f"\n  {len(rows)} candidates with a recorded chain")
    print("  bucketed by ATR% at the signal -- known at scan time, unlike range\n",
          flush=True)

    results = defaultdict(lambda: defaultdict(list))
    spreads = defaultdict(lambda: defaultdict(list))
    ranges = defaultdict(list)
    seen = defaultdict(int)
    _frames = {}

    for index, record in enumerate(rows):

        if index % 400 == 0:
            print(f"    ... {index}/{len(rows)}", flush=True)

        payload = record["p"] or {}
        entry = number(payload.get("Candidate Entry Price"))
        stop = number(payload.get("Candidate Stop Price"))
        direction = str(payload.get("Candidate Direction") or "").upper()

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
            _frames[(symbol, day)] = (
                prepare(frame) if frame is not None and len(frame) >= 25 else None
            )

        frame = _frames[(symbol, day)]

        if frame is None:
            continue

        try:
            at = pd.Timestamp(record["scan_timestamp"])
            at = (
                at.tz_localize("America/New_York")
                if at.tzinfo is None
                else at.tz_convert("America/New_York")
            )
        except Exception:
            continue

        forward = frame[frame.index > at]
        before = frame[frame.index <= at]

        if len(forward) < 5 or not len(before):
            continue

        atr = number(before["atr"].iloc[-1])

        if not atr or atr <= 0:
            continue

        band = band_of(atr / entry * 100.0)

        if band is None:
            continue

        seen[band] += 1
        ranges[band].append(
            (float(frame["High"].max()) - float(frame["Low"].min())) / entry * 100.0
        )

        is_call = direction == "CALL"
        hard = entry - STOP_ATR * atr if is_call else entry + STOP_ATR * atr

        for ceiling in CEILINGS:

            best = None

            for raw in chain:
                contract = usable(raw)
                if contract is None:
                    continue
                cost = number(raw.get("contract_cost"))
                if cost is None or not (MIN_COST <= cost <= MAX_COST):
                    continue
                if contract["oi"] < MIN_OI or contract["volume"] < MIN_VOLUME:
                    continue
                if not (MIN_DTE <= contract["dte"] <= MAX_DTE):
                    continue
                if contract["spread_pct"] > ceiling:
                    continue
                if best is None or contract["spread_pct"] < best["spread_pct"]:
                    best = contract

            if best is None:
                continue

            value = walk(None, forward, best, entry, hard, is_call)

            if value is None:
                continue

            results[band][ceiling].append(value)
            spreads[band][ceiling].append(best["spread_pct"])

    print(f"\n  {'band':17}{'ceiling':>9}{'n':>7}{'mean':>9}{'-top5':>9}"
          f"{'total':>11}{'win':>7}{'spread':>9}")
    print(f"  {'':-<78}")

    for band in BANDS:

        if not seen[band]:
            continue

        for ceiling in CEILINGS:

            values = results[band][ceiling]
            name = label(band) if ceiling == CEILINGS[0] else ""

            if len(values) < 15:
                print(f"  {name:17}{ceiling:>9.1f}{len(values):>7}{'  too few':>9}")
                continue

            strip = st.mean(sorted(values)[:-5])
            wins = sum(1 for v in values if v > 0) / len(values) * 100

            print(f"  {name:17}{ceiling:>9.1f}{len(values):>7}"
                  f"{st.mean(values):>+8.2f}%{strip:>+8.2f}%"
                  f"{sum(values):>+10.1f}%{wins:>6.0f}%"
                  f"{st.median(spreads[band][ceiling]):>8.2f}%")

        # Does the scan-time band actually track the thing it is standing in
        # for? A band whose median session range matches its neighbours is not
        # sorting movers from non-movers, whatever the P&L column says.
        print(f"  {'':17}{f'({seen[band]} cands':>9}, median session range "
              f"{st.median(ranges[band]):.2f}%)")

    print("\n  For this to be usable the top band must prefer a loose ceiling and")
    print("  the middle bands a tight one, as the hindsight split did.\n")


if __name__ == "__main__":
    main()
