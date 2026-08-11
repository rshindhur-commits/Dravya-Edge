"""S-C -- which underlyings offer a contract this account can actually trade?

Ranks the universe on the joint constraint, because either half alone is
misleading. On 2026-08-10, 244 of 247 candidates that took no contract had one
quoted at 3% spread or better available; what refused it was **price** in 34% of
cases and spread in 3%. Reading spread alone says the chain was fine. Reading
rejection counts says open interest, because far-OTM strikes dominate the
attempts the selector walks past. Neither is the constraint.

The constraint is contracts that are tight **and** affordable at the same time,
and on megacaps those two rarely coincide: near-ATM contracts are liquid and
cost thousands, everything under the cap is far OTM, wide and thin.

So the metric here is the **viable rate**: the share of observed moments where at
least one contract passed both tests together. A symbol whose viable rate is
zero cannot be traded by this account at any threshold setting, and no ranking of
its spreads will say so.

Reads `scanner_snapshot`, which carries the full attempt list and is kept 21
days. Days whose attempts predate the evidence fix of `65361cb` are detected and
excluded rather than silently counted as zero.

    python tools/universe_quality.py
    python tools/universe_quality.py --max-spread 3 --cap 500
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import statistics
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402

from app.db.connection import get_engine  # noqa: E402

CAP_GRID = (500, 750, 1000, 1500, 2000)

# A symbol seen fewer times than this is reported but never ranked; three
# sessions of thin coverage is how a universe decision gets made on noise.
MIN_MOMENTS = 20


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    return None if result != result else result


def load():
    """(trading_day, symbol, attempts) for every candidate that priced a chain."""

    with get_engine().connect() as connection:
        rows = connection.execute(text("""
            SELECT trading_day, symbol, decision_payload->>'Option Liquidity Attempts' raw
            FROM scanner_snapshot
            WHERE decision_payload->>'Option Liquidity Attempts' IS NOT NULL
            ORDER BY trading_day, symbol
        """)).mappings().all()

    out = []

    for row in rows:
        try:
            attempts = json.loads(row["raw"])
        except (ValueError, TypeError):
            continue

        if isinstance(attempts, list) and attempts:
            out.append((row["trading_day"], row["symbol"], attempts))

    return out


def has_evidence(attempts):
    """Did this record survive the 65361cb evidence fix?

    Before it, a rejection recorded its code and not the contract behind it, so
    cost and open interest read as absent. Counting those as "no affordable
    contract existed" would be a measurement artifact indistinguishable from the
    finding this tool exists to make.
    """

    return any(
        _number(a.get("contract_cost")) is not None
        or _number(a.get("open_interest")) is not None
        for a in attempts
    )


def viable(attempts, max_spread, cap):
    """Was there one contract that was tight and affordable at the same time?"""

    for attempt in attempts:
        spread = _number(attempt.get("spread_pct"))
        cost = _number(attempt.get("contract_cost"))

        if spread is None or cost is None:
            continue

        if spread <= max_spread and cost <= cap:
            return True

    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-spread", type=float, default=3.0)
    parser.add_argument("--cap", type=float, default=500.0)
    args = parser.parse_args()

    records = load()

    by_day = collections.defaultdict(list)
    for day, symbol, attempts in records:
        by_day[day].append((symbol, attempts))

    usable_days = sorted(
        day for day, items in by_day.items()
        if any(has_evidence(a) for _s, a in items)
    )
    skipped = sorted(set(by_day) - set(usable_days))

    print(f"scanner_snapshot: {len(records)} candidate-moments carrying attempts")
    print(f"usable sessions ({len(usable_days)}): "
          f"{', '.join(str(d) for d in usable_days)}")
    if skipped:
        print(f"excluded, attempts predate the evidence fix: "
              f"{', '.join(str(d) for d in skipped)}")
    print(f"viable = a contract at <= {args.max_spread}% spread AND <= ${args.cap:.0f}\n")

    per_symbol = collections.defaultdict(
        lambda: {"n": 0, "viable": 0, "spreads": [], "near_miss": collections.Counter(),
                 "tight_costs": []}
    )

    for day, symbol, attempts in records:
        if day not in usable_days or not has_evidence(attempts):
            continue

        stat = per_symbol[symbol]
        stat["n"] += 1

        spreads = [s for s in (_number(a.get("spread_pct")) for a in attempts) if s is not None]
        if spreads:
            stat["spreads"].append(min(spreads))

        if viable(attempts, args.max_spread, args.cap):
            stat["viable"] += 1
            continue

        # Not viable: what was the cheapest contract that WAS tight enough?
        tight = [
            _number(a.get("contract_cost")) for a in attempts
            if (_number(a.get("spread_pct")) or 99) <= args.max_spread
            and _number(a.get("contract_cost")) is not None
        ]
        if tight:
            stat["tight_costs"].append(min(tight))

        refused = [a for a in attempts if not a.get("accepted")
                   and _number(a.get("spread_pct")) is not None]
        if refused:
            best = min(refused, key=lambda a: _number(a.get("spread_pct")))
            stat["near_miss"][best.get("code")] += 1

    ranked = sorted(
        ((s, v) for s, v in per_symbol.items() if v["n"] >= MIN_MOMENTS),
        key=lambda kv: -kv[1]["viable"] / kv[1]["n"],
    )
    thin = [(s, v) for s, v in per_symbol.items() if v["n"] < MIN_MOMENTS]

    print(f"{'sym':<7}{'n':>5}{'viable%':>9}{'best spread':>13}"
          f"{'cheapest tight':>16}   what refuses the best contract")
    for symbol, stat in ranked:
        rate = 100.0 * stat["viable"] / stat["n"]
        med = statistics.median(stat["spreads"]) if stat["spreads"] else None
        tight = statistics.median(stat["tight_costs"]) if stat["tight_costs"] else None
        top = stat["near_miss"].most_common(1)
        print(f"{symbol:<7}{stat['n']:>5}{rate:>8.0f}%"
              f"{(f'{med:.2f}%' if med is not None else '-'):>13}"
              f"{(f'${tight:,.0f}' if tight is not None else '-'):>16}"
              f"   {top[0][0] if top else '-'}")

    if thin:
        print(f"\nnot ranked, fewer than {MIN_MOMENTS} moments: "
              f"{', '.join(f'{s}({v[chr(110)]})' for s, v in sorted(thin))}")

    print(f"\n{'cap sweep -- viable% across the whole universe':<50}")
    for cap in CAP_GRID:
        total = sum(v["n"] for v in per_symbol.values())
        hits = sum(
            1 for day, symbol, attempts in records
            if day in usable_days and has_evidence(attempts)
            and viable(attempts, args.max_spread, cap)
        )
        print(f"  cap ${cap:<6} {100.0 * hits / total if total else 0:>5.1f}% viable")

    print("\nDecision is scheduled for Mon 17 Aug, not now: this reads "
          f"{len(usable_days)} session(s).")
    print("Re-run this command then; the coverage line above is the thing to check first.")


if __name__ == "__main__":
    main()
