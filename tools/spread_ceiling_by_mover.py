"""Does the spread ceiling filter out the biggest movers?

`spread_ceiling_ab.py` compares ceilings across every candidate and finds 3 best
on every column. That is an average, and an average hides exactly the objection
worth taking seriously: **a wide spread may be the price of admission to a name
that actually travels.** On 2026-08-14, NBIS ran 9.3% from the prior close, its
four signals were refused for spreads around 6%, and one of them would have paid
+31.9%. If that generalises, the ceiling is not filtering cost -- it is filtering
opportunity, and the global A/B cannot see the difference.

## The test

Candidates are bucketed by **how far the underlying actually travelled that
session**, measured as the day's high-low range as a percent of the entry price.
That is the honest proxy for "big mover" available at the candidate level. Within
each bucket, the same ceilings are compared on the same candidates.

The hypothesis makes a specific, falsifiable prediction: **in the top bucket, a
looser ceiling should win.** If it loses there too, wide spreads are a cost the
mover does not repay, and the objection is answered on its own terms rather than
by appeal to the average.

The day's range is known only after the fact, so this is not a tradeable filter
and is not proposed as one. It is a diagnostic: it asks whether the movers *were*
being excluded, which is a question about the past and legitimately answered with
hindsight.

## Reading it

`n` is trades, not candidates. A ceiling that buys nothing in a bucket reports a
blank -- that is the filter doing its work and it is the whole point of the
comparison. `-top5` strips the five best trades in the cell, because a bucket
selected for large moves is exactly where a couple of lottery tickets can carry
a mean.

    python tools/spread_ceiling_by_mover.py

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

# Range of the session as a percent of entry price. The top band is where a
# 9%-range name like NBIS lands; the bottom is an ordinary megacap session.
BANDS = [(0.0, 2.0), (2.0, 3.5), (3.5, 5.0), (5.0, 100.0)]


def band_of(range_pct):
    for low, high in BANDS:
        if low <= range_pct < high:
            return (low, high)
    return None


def label(band):
    low, high = band
    return f"{low:.1f}-{high:.1f}%" if high < 100 else f"{low:.1f}%+"


def main():

    random.seed(83)
    rows = load()

    print(f"\n  {len(rows)} candidates with a recorded chain")
    print("  bucketed by the session's high-low range as a % of entry price")
    print("  every gate but the ceiling held fixed\n", flush=True)

    # results[band][ceiling] -> list of returns;  seen[band] -> candidate count
    results = defaultdict(lambda: defaultdict(list))
    spreads = defaultdict(lambda: defaultdict(list))
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

        # The whole session, not just the forward part: "was this a big mover"
        # is a property of the day, and a signal at 15:00 on a day that already
        # ran 9% belongs in the same bucket as one at 10:00.
        session_range = (
            float(frame["High"].max()) - float(frame["Low"].min())
        ) / entry * 100.0
        band = band_of(session_range)

        if band is None:
            continue

        seen[band] += 1

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

    print(f"\n  {'session range':16}{'ceiling':>9}{'n':>7}{'fill%':>8}"
          f"{'mean':>9}{'-top5':>9}{'total':>11}{'win':>7}{'spread':>9}")
    print(f"  {'':-<85}")

    for band in BANDS:

        candidates = seen[band]

        if not candidates:
            continue

        for ceiling in CEILINGS:

            values = results[band][ceiling]

            if len(values) < 15:
                print(f"  {label(band) if ceiling == CEILINGS[0] else '':16}"
                      f"{ceiling:>9.1f}{len(values):>7}"
                      f"{len(values)/candidates*100:>7.0f}%{'  too few':>9}")
                continue

            strip = st.mean(sorted(values)[:-5])
            wins = sum(1 for v in values if v > 0) / len(values) * 100

            print(f"  {label(band) if ceiling == CEILINGS[0] else '':16}"
                  f"{ceiling:>9.1f}{len(values):>7}"
                  f"{len(values)/candidates*100:>7.0f}%"
                  f"{st.mean(values):>+8.2f}%{strip:>+8.2f}%"
                  f"{sum(values):>+10.1f}%{wins:>6.0f}%"
                  f"{st.median(spreads[band][ceiling]):>8.2f}%")

        print(f"  {'':16}{f'({candidates} candidates)':>9}")

    print("\n  The hypothesis predicts a looser ceiling wins in the bottom band.")
    print("  If it loses there too, wide spreads are a cost the mover does not repay.\n")


if __name__ == "__main__":
    main()
