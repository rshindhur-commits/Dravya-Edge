"""Which watchlist names can this account trade at all, and which gate stops it?

`universe_quality.py` ranks symbols on a joint viable rate. This answers the
follow-up it raises: when a symbol's rate is zero, *why*, and is it fixable by a
setting or not fixable at all.

The archive's `code` field cannot answer that. It records the **first** gate a
contract failed, so whichever runs first absorbs the blame -- the short-circuit
trap that made open interest look like the constraint when removing it entirely
bought 17 chains of 2,169. Here every gate is evaluated independently over every
contract on every chain, counting how many pass it **alone**, plus the one
combination that actually matters:

    spread <= ceiling  AND  cost inside the cap, at the same time, on one contract

That pair is the binding constraint on megacaps and it is not visible in either
column separately. Near-ATM contracts are tight and cost thousands; everything
under the cap is far OTM, thin and wide. A symbol can look healthy on spread and
healthy on cost while **no single contract** is both.

Measured 2026-08-16 over three sessions: AMD, ARM, MU, PANW and AMAT produced
263 candidates and **zero** contracts, and across 16,439 of their contracts not
one was simultaneously inside a 3% spread and the $100-1000 band. Their median
contract runs $2,035-$5,585. That is structural -- no threshold reachable from
the subscriber bands makes those names tradeable.

    python tools/universe_viability.py
    python tools/universe_viability.py --days 5 --max-spread 3 --cap 1000

Reads `scanner_snapshot`, which is kept 21 days. No network.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.db.connection import get_engine

MIN_OI, MIN_VOLUME, MIN_DTE, MAX_DTE, MIN_COST = 500, 100, 5, 30, 100


def number(value):
    try:
        result = float(value)
        return None if result != result else result
    except (TypeError, ValueError):
        return None


def load(days):
    with get_engine().begin() as connection:
        return connection.execute(text("""
            SELECT DISTINCT ON (s.symbol, s.trading_day, s.scan_timestamp)
                   s.symbol, s.trading_day,
                   s.decision_payload->>'Option Liquidity Attempts' AS chain
            FROM scanner_snapshot s
            WHERE jsonb_typeof(s.decision_payload->'Option Liquidity Attempts')='string'
              AND s.decision_payload->>'Candidate Direction' IN ('CALL','PUT')
              AND s.trading_day >= (
                  SELECT MAX(trading_day) - make_interval(days => :days)
                  FROM scanner_snapshot
              )
            ORDER BY s.symbol, s.trading_day, s.scan_timestamp
        """), {"days": days}).mappings().all()


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--max-spread", type=float, default=3.0)
    parser.add_argument("--cap", type=float, default=1000.0)
    args = parser.parse_args()

    rows = load(args.days)

    gates = defaultdict(lambda: defaultdict(int))
    costs = defaultdict(list)
    candidates = defaultdict(lambda: [0, 0])

    for record in rows:

        try:
            chain = json.loads(record["chain"] or "[]")
        except Exception:
            continue

        if not chain:
            continue

        symbol = record["symbol"]
        candidates[symbol][0] += 1
        viable = False

        for raw in chain:

            spread = number(raw.get("spread_pct"))
            cost = number(raw.get("contract_cost"))
            oi = number(raw.get("open_interest")) or 0
            volume = number(raw.get("volume")) or 0
            dte = number(raw.get("dte"))

            if None in (spread, cost, dte):
                continue

            tight = spread <= args.max_spread
            affordable = MIN_COST <= cost <= args.cap

            gates[symbol]["seen"] += 1
            gates[symbol]["spread"] += tight
            gates[symbol]["cost"] += affordable
            gates[symbol]["oi"] += oi >= MIN_OI
            gates[symbol]["volume"] += volume >= MIN_VOLUME
            gates[symbol]["both"] += tight and affordable
            costs[symbol].append(cost)

            if (tight and affordable and oi >= MIN_OI
                    and volume >= MIN_VOLUME and MIN_DTE <= dte <= MAX_DTE):
                viable = True

        candidates[symbol][1] += viable

    print(f"\n  last {args.days} sessions, spread<={args.max_spread}, "
          f"${MIN_COST:.0f}-{args.cap:.0f}, OI>={MIN_OI}, vol>={MIN_VOLUME},"
          f" DTE {MIN_DTE}-{MAX_DTE}")
    print("  each gate tested ALONE -- no short-circuit\n")

    print(f"  {'symbol':8}{'cands':>7}{'viable':>8}{'rate':>7}"
          f"{'contracts':>11}{'tight':>8}{'cheap':>8}{'BOTH':>7}   median cost")
    print(f"  {'':-<83}")

    order = sorted(
        candidates,
        key=lambda s: -(candidates[s][1] / max(candidates[s][0], 1)),
    )

    dead = []

    for symbol in order:

        seen, viable = candidates[symbol]
        g = gates[symbol]

        if not g["seen"]:
            continue

        n = g["seen"]

        print(f"  {symbol:8}{seen:>7}{viable:>8}{viable/seen*100:>6.0f}%"
              f"{n:>11}{g['spread']/n*100:>7.0f}%{g['cost']/n*100:>7.0f}%"
              f"{g['both']/n*100:>6.0f}%   ${st.median(costs[symbol]):>7.0f}")

        if not viable:
            dead.append(symbol)

    if dead:
        wasted = sum(candidates[s][0] for s in dead)
        total = sum(v[0] for v in candidates.values())
        print(f"\n  NEVER TRADEABLE: {', '.join(dead)}")
        print(f"  {wasted} of {total} candidates ({wasted/total*100:.0f}%) "
              f"came from names that produced nothing.")
        print("  A BOTH column of 0% is structural: no contract on the chain is")
        print("  tight and affordable at once, so no threshold fixes it.\n")


if __name__ == "__main__":
    main()
