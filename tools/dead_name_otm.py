"""Can the nine untradeable names be traded far OTM without losing money?

MU, AMAT, ARM, PANW, AMD, AVGO, SMH, META and GOOGL produce 945 candidates over
21 sessions and zero contracts, because nothing on their chains is inside a 3%
spread and the $100-1000 cap at the same time. The tight contracts cost
thousands; the affordable ones are far out of the money, thin and wide.

The operator's position is that these are the market's real movers and worth
carrying **at break-even** -- profit is not required, only that they do not lose.
That is a lower bar than anything else in this document has had to clear, and it
deserves a direct test rather than an appeal to the gates.

## What is varied

Only the two gates that stand between these names and an affordable contract:
the spread ceiling, and the open-interest/volume floors. Cost stays inside the
subscriber band ($100-1000) throughout, because an unaffordable contract is not
the thing being asked about. `OPTION_MIN_DTE` is enforced -- omitting it is what
produced a +758% 0-DTE artifact once already.

For each cell the contract chosen is the **cheapest** affordable one that passes,
not the tightest, because "far OTM and affordable" is the hypothesis under test.

## The bar

Break-even, on `mean` **and** on `-top5`. A cell whose mean is positive only
because of five trades is not a break-even strategy, it is a lottery, and these
names would be carried for many months.

    python tools/dead_name_otm.py
    python tools/dead_name_otm.py --symbols AMD,META

Archive only, no network beyond the cached bars.
"""

import argparse
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
    MAX_COST,
    MAX_DTE,
    MIN_COST,
    MIN_DTE,
    STOP_ATR,
    load,
    prepare,
    walk,
)

DEAD = ["MU", "AMAT", "ARM", "PANW", "AMD", "AVGO", "SMH", "META", "GOOGL"]

CEILINGS = [3.0, 4.0, 6.0, 10.0, 99.0]
# (open interest, volume) -- the shipped floors, then progressively relaxed.
LIQUIDITY = [(500, 100), (100, 25), (0, 0)]


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=None)
    parser.add_argument(
        "--max-cost",
        type=float,
        default=MAX_COST,
        help="raise above the subscriber band to separate 'unaffordable' from "
        "'unprofitable'. If the tight, expensive contracts also lose, the names "
        "are bad rather than merely out of reach.",
    )
    parser.add_argument(
        "--pick",
        choices=("cheapest", "tightest"),
        default="cheapest",
        help="cheapest tests the far-OTM hypothesis; tightest tests whether the "
        "quality contracts on these names pay at any price",
    )
    args = parser.parse_args()

    max_cost = args.max_cost
    pick_tightest = args.pick == "tightest"

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",")]
        if args.symbols
        else DEAD
    )

    random.seed(83)
    rows = [r for r in load() if r["symbol"] in symbols]

    print(f"\n  {', '.join(symbols)}")
    print(f"  {len(rows)} candidates with a recorded chain")
    print(f"  cheapest affordable contract, ${MIN_COST:.0f}-{MAX_COST:.0f}, "
          f"DTE {MIN_DTE}-{MAX_DTE}\n", flush=True)

    results = defaultdict(list)
    otm = defaultdict(list)
    spreads = defaultdict(list)
    dollars = defaultdict(list)
    tenors = defaultdict(list)
    _frames = {}

    for index, record in enumerate(rows):

        if index % 200 == 0:
            print(f"    ... {index}/{len(rows)}", flush=True)

        payload = record["p"] or {}
        entry = number(payload.get("Candidate Entry Price"))
        direction = str(payload.get("Candidate Direction") or "").upper()

        if entry is None or entry <= 0:
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

        is_call = direction == "CALL"
        hard = entry - STOP_ATR * atr if is_call else entry + STOP_ATR * atr

        for ceiling in CEILINGS:
            for min_oi, min_volume in LIQUIDITY:

                chosen = None
                chosen_key = None

                for raw in chain:

                    contract = usable(raw)

                    if contract is None:
                        continue

                    cost = number(raw.get("contract_cost"))

                    if cost is None or not (MIN_COST <= cost <= max_cost):
                        continue
                    if contract["oi"] < min_oi or contract["volume"] < min_volume:
                        continue
                    if not (MIN_DTE <= contract["dte"] <= MAX_DTE):
                        continue
                    if contract["spread_pct"] > ceiling:
                        continue
                    rank = contract["spread_pct"] if pick_tightest else cost
                    if chosen_key is None or rank < chosen_key:
                        chosen, chosen_key = contract, rank

                if chosen is None:
                    continue

                value = walk(None, forward, chosen, entry, hard, is_call)

                if value is None:
                    continue

                key = (ceiling, min_oi, min_volume)
                results[key].append(value)
                spreads[key].append(chosen["spread_pct"])
                dollars[key].append(chosen["ask"] * 100.0)
                tenors[key].append(chosen["dte"])
                otm[key].append(
                    (chosen["strike"] - entry) / entry * 100.0
                    * (1 if is_call else -1)
                )

    print(f"\n  {'ceiling':>9}{'OI':>7}{'vol':>6}{'n':>7}{'mean':>9}{'-top5':>9}"
          f"{'median':>9}{'total':>11}{'win':>6}{'spread':>8}{'OTM%':>8}")
    print(f"  {'':-<104}")

    passed = []

    for ceiling in CEILINGS:
        for min_oi, min_volume in LIQUIDITY:

            key = (ceiling, min_oi, min_volume)
            values = results[key]
            name = "none" if ceiling >= 99 else f"{ceiling:.0f}"

            if len(values) < 20:
                print(f"  {name:>9}{min_oi:>7}{min_volume:>6}"
                      f"{len(values):>7}   too few")
                continue

            strip = st.mean(sorted(values)[:-5])
            wins = sum(1 for v in values if v > 0) / len(values) * 100

            if st.mean(values) >= 0 and strip >= 0:
                passed.append((key, st.mean(values), strip, len(values)))

            print(f"  {name:>9}{min_oi:>7}{min_volume:>6}{len(values):>7}"
                  f"{st.mean(values):>+8.2f}%{strip:>+8.2f}%"
                  f"{st.median(values):>+8.2f}%{sum(values):>+10.1f}%"
                  f"{wins:>5.0f}%{st.median(spreads[key]):>7.2f}%"
                  f"{st.median(otm[key]):>+7.1f}%"
                  f"{st.median(dollars[key]):>7.0f}$"
                  f"{st.median(tenors[key]):>5.0f}")

    print()

    if passed:
        print("  CELLS CLEARING BREAK-EVEN on mean and on the top-5 strip:")
        for (ceiling, oi, volume), mean, strip, n in passed:
            print(f"    ceiling {ceiling:g}, OI>={oi}, vol>={volume}: "
                  f"{n} trades, mean {mean:+.2f}%, stripped {strip:+.2f}%")
    else:
        print("  NO CELL clears break-even on both mean and the top-5 strip.")
        print("  Every way of reaching an affordable contract on these names")
        print("  loses money, so there is nothing here to carry at par.")

    print()


if __name__ == "__main__":
    main()
