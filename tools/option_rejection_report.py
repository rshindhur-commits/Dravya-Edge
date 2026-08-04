"""What the option liquidity walk rejected on a trading day, and at what number.

The question this exists to answer is the one 2026-08-03 could not: when a
contract is refused, was the threshold set too high, or was the selector
reaching for a strike that deserved refusing?

That day recorded 18,026 attempts across 242 rejected rows and not one carried
an open interest, a cost or a delta -- `_select_liquid_option_from_bundle` built
each attempt from a fixed seven keys and dropped the evidence
`evaluate_option_liquidity` had attached. With that fixed, every attempt names
its contract and the threshold it was measured against, so the counterfactuals
below are reads rather than estimates.

Two cautions the 2026-08-03 analysis had to learn the hard way:

* **`Option Rejection Reason` is the last attempt's reason, not the cause.** It
  said "Low open interest, 162" while 209 of 242 rows had hit
  OPTION_TOO_EXPENSIVE along the way. Always count per attempt.
* **Rows are not opportunities.** 2,990 rows is 26 symbols x 115 scans; a symbol
  blocked all day counts 115 times. The symbol columns are the honest ones.

    python tools/option_rejection_report.py --day 2026-08-04

Reads the database. Days before the evidence fix land in the `unknown` column
rather than being silently counted as passing.
"""

import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.db.connection import get_engine

COST_CAPS = (500, 750, 1000, 1200, 1500, 2000)
OI_FLOORS = (100, 250, 500, 750, 1000)


def number(value):

    try:

        if value is None or str(value).strip().lower() in {"", "nan", "none"}:

            return None

        return float(value)

    except (TypeError, ValueError):

        return None


def attempts_of(payload):

    raw = payload.get("Option Liquidity Attempts")

    if not raw:

        return []

    if isinstance(raw, str):

        try:

            raw = json.loads(raw)

        except json.JSONDecodeError:

            return []

    return raw if isinstance(raw, list) else []


def load(day):

    with get_engine().begin() as connection:

        return [
            row["decision_payload"] or {}
            for row in connection.execute(text("""
                SELECT decision_payload FROM scanner_snapshot
                WHERE trading_day = CAST(:day AS DATE)
                ORDER BY scan_id, symbol
            """), {"day": day}).mappings()
        ]


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", required=True, help="trading day, YYYY-MM-DD")
    args = parser.parse_args()

    rows = load(args.day)

    if not rows:

        print(f"no scanner_snapshot rows for {args.day}")
        return

    rejected = [row for row in rows if row.get("Option Rejection Reason")]
    symbols = {str(row.get("Symbol")) for row in rows}

    print(f"{args.day}: {len(rows)} rows, {len(symbols)} symbols, "
          f"{len(rejected)} rejected\n")

    # ------------------------------------------------------------ per attempt
    per_attempt = collections.Counter()
    rows_hit = collections.Counter()
    symbols_hit = collections.defaultdict(set)
    evidence_seen = 0
    total_attempts = 0

    for row in rejected:

        symbol = str(row.get("Symbol"))
        codes = []

        for attempt in attempts_of(row):

            total_attempts += 1
            code = str(attempt.get("code") or "?")
            codes.append(code)

            if attempt.get("open_interest") is not None:

                evidence_seen += 1

        per_attempt.update(codes)

        for code in set(codes):

            rows_hit[code] += 1
            symbols_hit[code].add(symbol)

    print(f"== {total_attempts} attempts, "
          f"{evidence_seen} carrying contract evidence "
          f"({evidence_seen / max(total_attempts, 1):.0%}) ==")

    if evidence_seen == 0:

        print("   this day predates the evidence fix -- "
              "the counterfactuals below cannot be computed\n")

    print(f"\n   {'code':34}{'attempts':>10}{'rows':>7}{'symbols':>9}")

    for code, count in per_attempt.most_common(12):

        print(f"   {code[:32]:34}{count:>10}{rows_hit[code]:>7}"
              f"{len(symbols_hit[code]):>9}")

    if evidence_seen == 0:

        return

    # ------------------------------------------------------- counterfactuals
    # A contract only becomes tradeable if it clears every bar at once, so these
    # count contracts that fail *this* test and nothing else.
    too_expensive = []
    low_oi = []

    for row in rejected:

        symbol = str(row.get("Symbol"))

        for attempt in attempts_of(row):

            code = str(attempt.get("code") or "")
            cost = number(attempt.get("contract_cost"))
            open_interest = number(attempt.get("open_interest"))

            if code == "OPTION_TOO_EXPENSIVE" and cost is not None:

                too_expensive.append((symbol, cost))

            if code == "LOW_OPEN_INTEREST" and open_interest is not None:

                low_oi.append((symbol, open_interest))

    if too_expensive:

        print(f"\n== {len(too_expensive)} contracts refused on cost ==")
        print(f"   {'cap':>8}{'contracts':>12}{'symbols':>10}")

        for cap in COST_CAPS:

            fits = [(s, c) for s, c in too_expensive if c <= cap]
            print(f"   {cap:>8}{len(fits):>12}{len({s for s, _ in fits}):>10}")

    if low_oi:

        print(f"\n== {len(low_oi)} contracts refused on open interest ==")
        print(f"   {'floor':>8}{'contracts':>12}{'symbols':>10}")

        for floor in OI_FLOORS:

            fits = [(s, oi) for s, oi in low_oi if oi >= floor]
            print(f"   {floor:>8}{len(fits):>12}{len({s for s, _ in fits}):>10}")

    # ------------------------------------------- what was blocked by cost only
    # The useful ceiling on the cost cap: past the point where the cheaper
    # contracts fail the quality bar anyway, raising it buys risk and nothing
    # else. 2026-08-03 put that ceiling near $1,500.
    print("\n== cheapest contract per symbol that failed ONLY on cost ==")
    cheapest = {}

    for row in rejected:

        symbol = str(row.get("Symbol"))

        for attempt in attempts_of(row):

            if str(attempt.get("code")) != "OPTION_TOO_EXPENSIVE":

                continue

            cost = number(attempt.get("contract_cost"))

            if cost is None:

                continue

            if symbol not in cheapest or cost < cheapest[symbol][0]:

                cheapest[symbol] = (cost, attempt.get("ticker"),
                                    number(attempt.get("delta")))

    print(f"   {'sym':8}{'cheapest':>10}{'delta':>8}  ticker")

    for symbol, (cost, ticker, delta) in sorted(
        cheapest.items(), key=lambda item: item[1][0]
    ):

        print(f"   {symbol:8}{cost:>10.0f}"
              f"{('-' if delta is None else f'{delta:.2f}'):>8}  {ticker or '-'}")


if __name__ == "__main__":

    main()
