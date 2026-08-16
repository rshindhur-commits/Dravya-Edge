"""If the direction is called correctly, does a further-OTM contract pay?

The operator's standard, stated twice: cheaper, further-out-of-the-money
contracts are acceptable **provided the entry and exit direction is right**.
`dead_name_otm.py` answered the unconditional version -- across every candidate,
far OTM loses -- but that mixes the trades the app called correctly with the
ones it did not, and the objection is precisely about the first group.

So this splits by what the underlying actually did before it did anything else:

    RIGHT   the underlying reached its target before its stop
    WRONG   the stop came first

and prices every moneyness band inside each. A bar touching both scores as the
stop; intrabar order is unknowable at 5m and assuming otherwise manufactures the
edge being looked for.

## What this can and cannot say

**It cannot be traded.** Knowing which trades will be right is the whole problem.
Conditioning on the outcome is lookahead and the RIGHT column is not achievable.

**It answers the design question, which is not the same thing.** If far OTM pays
handsomely when the call is right and loses only a little more when it is wrong,
then the contract choice is worth revisiting the moment entry quality improves.
If it loses money *even on the trades that were called correctly*, it is dead
regardless of how good the signal ever gets -- and that is a permanent answer,
not one contingent on the current entry rules.

The second is what to look for, because this project's entry edge is thin and
uniformly spread (section 5.9), so any plan that needs better entries first is a
plan for later.

    python tools/moneyness_by_outcome.py
    python tools/moneyness_by_outcome.py --symbols AVGO,SMH,GOOGL

Archive only, no network beyond the cached bars.
"""

import argparse
import json
import pathlib
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

# Signed so that positive is always *out* of the money, whichever way the trade
# leans. A call struck above spot and a put struck below it are both OTM.
BANDS = [
    (-100.0, -1.0, "ITM"),
    (-1.0, 1.0, "ATM"),
    (1.0, 3.0, "OTM 1-3%"),
    (3.0, 6.0, "OTM 3-6%"),
    (6.0, 100.0, "OTM 6%+"),
]

MAX_SPREAD = 6.0
MAX_COST = 2500.0


def band_of(value):
    for low, high, label in BANDS:
        if low <= value < high:
            return label
    return None


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default=None)
    args = parser.parse_args()

    wanted = (
        {s.strip().upper() for s in args.symbols.split(",")}
        if args.symbols
        else None
    )

    rows = load()

    if wanted:
        rows = [r for r in rows if r["symbol"] in wanted]

    print(f"\n  {len(rows)} candidates"
          f"{' -- ' + ', '.join(sorted(wanted)) if wanted else ''}")
    print(f"  every contract on each chain inside ${MIN_COST:.0f}-{MAX_COST:.0f}, "
          f"spread<={MAX_SPREAD:.0f}%, OI>={MIN_OI}, vol>={MIN_VOLUME}, "
          f"DTE {MIN_DTE}-{MAX_DTE}\n", flush=True)

    results = defaultdict(list)
    costs = defaultdict(list)
    outcomes = {"RIGHT": 0, "WRONG": 0}
    _frames = {}

    for index, record in enumerate(rows):

        if index % 300 == 0:
            print(f"    ... {index}/{len(rows)}", flush=True)

        payload = record["p"] or {}
        entry = number(payload.get("Candidate Entry Price"))
        stop = number(payload.get("Candidate Stop Price"))
        target = number(payload.get("Candidate Target Price"))
        direction = str(payload.get("Candidate Direction") or "").upper()

        if None in (entry, stop) or entry <= 0 or entry == stop:
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
        risk = abs(entry - stop)

        if target is None:
            target = entry + 2 * risk if is_call else entry - 2 * risk

        # The directional verdict, on the underlying and nothing else.
        verdict = None

        for _, bar in forward.iterrows():

            low, high = number(bar["Low"]), number(bar["High"])

            if None in (low, high):
                continue

            if (low <= stop) if is_call else (high >= stop):
                verdict = "WRONG"
                break

            if (high >= target) if is_call else (low <= target):
                verdict = "RIGHT"
                break

        if verdict is None:
            continue

        outcomes[verdict] += 1
        hard = entry - STOP_ATR * atr if is_call else entry + STOP_ATR * atr

        # Every qualifying contract, not one pick: the question is what the
        # bands do, so each band needs its own population.
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
            if contract["spread_pct"] > MAX_SPREAD:
                continue

            moneyness = (contract["strike"] - entry) / entry * 100.0
            band = band_of(moneyness if is_call else -moneyness)

            if band is None:
                continue

            value = walk(None, forward, contract, entry, hard, is_call)

            if value is None:
                continue

            results[(verdict, band)].append(value)
            costs[(verdict, band)].append(cost)

    print(f"\n  directional verdict on the underlying: "
          f"{outcomes['RIGHT']} right, {outcomes['WRONG']} wrong "
          f"({outcomes['RIGHT']/max(sum(outcomes.values()), 1)*100:.0f}% right)\n")

    print(f"  {'verdict':9}{'band':11}{'n':>7}{'mean':>9}{'-top5':>9}"
          f"{'median':>9}{'win':>7}{'med cost':>10}")
    print(f"  {'':-<71}")

    for verdict in ("RIGHT", "WRONG"):

        for _, _, band in BANDS:

            values = results[(verdict, band)]

            if len(values) < 15:
                print(f"  {verdict if band == 'ITM' else '':9}{band:11}"
                      f"{len(values):>7}   too few")
                continue

            strip = st.mean(sorted(values)[:-5])
            wins = sum(1 for v in values if v > 0) / len(values) * 100

            print(f"  {verdict if band == 'ITM' else '':9}{band:11}{len(values):>7}"
                  f"{st.mean(values):>+8.2f}%{strip:>+8.2f}%"
                  f"{st.median(values):>+8.2f}%{wins:>6.0f}%"
                  f"{st.median(costs[(verdict, band)]):>9.0f}$")

        print()

    # The number the whole question reduces to. Every band pays when the call
    # is right and loses when it is wrong, so what separates them is not whether
    # they work but **how often the direction has to be right** for the band to
    # break even. That is comparable across bands in a way the means are not,
    # and it can be read against the accuracy the app actually achieves.
    actual = outcomes["RIGHT"] / max(sum(outcomes.values()), 1) * 100

    print(f"  {'band':11}{'up':>9}{'down':>9}{'ratio':>8}"
          f"{'break-even accuracy':>22}{'med cost':>10}")
    print(f"  {'':-<70}")

    for _, _, band in BANDS:

        up = results[("RIGHT", band)]
        down = results[("WRONG", band)]

        if len(up) < 15 or len(down) < 15:
            print(f"  {band:11}   too few")
            continue

        gain, loss = st.mean(up), abs(st.mean(down))
        needed = loss / (gain + loss) * 100
        verdict = "reached" if actual >= needed else f"{needed - actual:.0f} pts short"

        print(f"  {band:11}{gain:>+8.2f}%{-loss:>+8.2f}%{gain/loss:>8.2f}"
              f"{needed:>19.1f}%  {verdict:<16}"
              f"{st.median(costs[('RIGHT', band)]):>7.0f}$")

    print(f"\n  The app is directionally right {actual:.0f}% of the time.")
    print("  Every band pays when the call is right, so none of them is broken --")
    print("  what separates them is how much accuracy each one needs. A further-OTM")
    print("  contract is cheaper and needs MORE accuracy, not less, because its")
    print("  upside shrinks faster than its downside does.\n")


if __name__ == "__main__":
    main()
