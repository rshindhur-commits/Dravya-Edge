"""What the option would have returned on candidates we never traded.

`resolve_candidate_outcomes.py` answers whether a refused candidate reached its
target, but it answers it on the **underlying**. Nothing about buying the option
is in that number, and the option is where this strategy loses: 291 archived
trades returned −3.15% of premium while mean R sat at roughly zero.

So the toll has been a single fitted constant -- 0.321R, from Gate 1's regression
of premium% on R across those trades -- and every conclusion drawn since leans on
it. One number, fitted once, on the trades that happened to be taken. This
measures it per candidate instead, over the pool that was refused.

Fills are honest: buying lifts the ask, selling hits the bid, via
`historical_options.fill_price`. Pricing the round trip off mids is the single
easiest way to make an options backtest read better than the account ever will.

    python tools/replay_option_leg.py --day 2026-08-11
    python tools/replay_option_leg.py --day 2026-08-11 --limit 5

Quota is the reason this is not wired into the scan. `build_historical_chain`
prices a whole reconstructed chain per candidate -- roughly 150 requests -- and
option quotes are not cached, so a full session is thousands of requests. It runs
**post-market only**, where it competes with nothing.
"""

import argparse
import collections
import pathlib
import statistics
import sys
import time

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.backtesting.contract_selector import select_contract  # noqa: E402
from app.backtesting.historical_market_data import fetch_bars  # noqa: E402
from app.backtesting.historical_options import (  # noqa: E402
    fill_price,
    quote_at,
    round_trip_cost_pct,
)
from tools.resolve_candidate_outcomes import (  # noqa: E402
    LONG,
    archived_days,
    load_candidates,
    resolve,
)


def price_leg(candidate, verdict, resolved_at):
    """Buy the option at the decision, sell it at the resolution.

    Returns a dict, or a skip reason. A candidate whose chain yields no contract
    is "no trade", never a fallback strike -- the same rule live follows.
    """

    direction = "CALL" if candidate["direction"] in LONG else "PUT"

    ticker, _contract, diagnostics = select_contract(
        candidate["symbol"],
        direction,
        candidate["decided_at"],
        candidate["entry"],
    )

    if not ticker:

        return {"skip": "NO_CONTRACT", "chain_size": diagnostics.get("chain_size", 0)}

    entry_quote = quote_at(ticker, candidate["decided_at"])
    exit_quote = quote_at(ticker, resolved_at)

    if not entry_quote or not exit_quote:

        return {"skip": "NO_QUOTE"}

    paid = fill_price(entry_quote, "BUY")
    got = fill_price(exit_quote, "SELL")

    if not paid:

        return {"skip": "NO_FILL"}

    return {
        "ticker": ticker,
        "verdict": verdict,
        "paid": paid,
        "got": got,
        "option_return_pct": (got - paid) / paid * 100.0,
        "entry_spread_pct": round_trip_cost_pct(entry_quote),
        "contract_cost": paid * 100.0,
    }


def run_day(day, limit=None):

    candidates = load_candidates(day)
    frames = {}
    priced = []
    skips = collections.Counter()
    started = time.time()

    for candidate in candidates:

        symbol = candidate["symbol"]

        if symbol not in frames:

            frames[symbol] = fetch_bars(
                symbol, day, day, multiplier=5, timespan="minute"
            )

        verdict, resolved_at = resolve(candidate, frames[symbol])

        if verdict not in {"TARGET_FIRST", "STOP_FIRST"}:

            skips["UNRESOLVED"] += 1
            continue

        try:
            leg = price_leg(candidate, verdict, resolved_at)
        except Exception as exc:  # noqa: BLE001
            skips[f"ERROR:{type(exc).__name__}"] += 1
            continue

        if leg.get("skip"):

            skips[leg["skip"]] += 1
            continue

        leg["symbol"] = symbol
        priced.append(leg)

        if limit and len(priced) >= limit:

            break

    return priced, skips, time.time() - started


def report(day, priced, skips, elapsed):

    print(f"\n{day}: priced {len(priced)} option legs in {elapsed:.0f}s")

    if skips:

        print("   skipped: " + "  ".join(f"{k}={v}" for k, v in skips.most_common()))

    if not priced:

        return

    returns = [leg["option_return_pct"] for leg in priced]
    spreads = [leg["entry_spread_pct"] for leg in priced if leg["entry_spread_pct"]]
    winners = [leg for leg in priced if leg["verdict"] == "TARGET_FIRST"]
    losers = [leg for leg in priced if leg["verdict"] == "STOP_FIRST"]

    print(f"   mean option return   {statistics.mean(returns):+.2f}%")
    print(f"   median               {statistics.median(returns):+.2f}%")

    if spreads:

        print(f"   mean entry spread    {statistics.mean(spreads):.2f}%")

    # The comparison the underlying replay cannot make: a candidate can reach its
    # target and still lose money once the spread is paid at both ends.
    for label, group in (("target-first", winners), ("stop-first", losers)):

        if not group:

            continue

        values = [leg["option_return_pct"] for leg in group]
        positive = sum(1 for value in values if value > 0)
        print(
            f"   {label:<13} n={len(group):<4} "
            f"mean {statistics.mean(values):+7.2f}%   "
            f"actually profitable: {positive}/{len(group)}"
        )


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", help="trading day, YYYY-MM-DD")
    parser.add_argument("--all", action="store_true", help="every archived day")
    parser.add_argument("--limit", type=int, help="stop after N priced legs per day")
    args = parser.parse_args()

    if not args.day and not args.all:

        parser.error("pass --day or --all")

    days = archived_days() if args.all else [args.day]
    total = []

    for day in days:

        priced, skips, elapsed = run_day(day, limit=args.limit)
        report(day, priced, skips, elapsed)
        total.extend(priced)

    if len(days) > 1 and total:

        returns = [leg["option_return_pct"] for leg in total]
        print(f"\nacross {len(days)} sessions: {len(total)} legs, "
              f"mean {statistics.mean(returns):+.2f}%, "
              f"median {statistics.median(returns):+.2f}%")


if __name__ == "__main__":
    main()
