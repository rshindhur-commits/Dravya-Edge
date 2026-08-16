"""How often was a good contract sitting in the chain, and passed over?

MU on 2026-08-14 called the morning drop correctly -- bearish trend, bearish
volume, volatility expanding -- then rejected its own chosen contract at a 10.67%
spread while the same payload recorded `Short DTE Spread % 2.37` at quality 80.
A tight, near-dated contract was measured and not bought.

AMD the same day looks different. Its eighteen sub-3% contracts were all 2027 and
2028 expiries, rejected on cost or volume. Nothing affordable was missed there --
the cost envelope was the binding constraint, not the ranker.

Those two cases imply opposite fixes, so this counts which one is typical.

## The question, precisely

For every scan where the app **refused to trade on option grounds**, did the
chain contain a contract that was simultaneously:

    tight        spread at or under the gate's own ceiling
    affordable   at or under the contract cost cap
    liquid       open interest and volume above the floors that already exist

If that combination is common, the ranker is picking badly while a usable
contract sits beside it, and preferring it is a small targeted change. If it is
rare, the ranker is not the problem and the cost envelope is -- and changing the
ranking would only reach for LEAPS the cap rejects, which is §14's measured
result and would make the book worse rather than better.

Deliberately counted at the app's **own** thresholds rather than at ones chosen
here, so the answer describes the deployed configuration.

    python tools/missed_affordable_contracts.py

Archive only, no network.
"""

import json
import pathlib
import statistics as st
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.db.connection import get_engine

MAX_SPREAD = 3.0
MAX_COST = 1500.0
MIN_OI = 500
MIN_VOLUME = 50


def number(value):
    try:
        result = float(value)
        return None if result != result else result
    except (TypeError, ValueError):
        return None


def main():

    with get_engine().begin() as connection:
        rows = connection.execute(text("""
            SELECT symbol, trading_day,
                   decision_payload->>'Option Liquidity Attempts' AS chain,
                   decision_payload->>'Rejected Trade Reason' AS reason,
                   decision_payload->>'Option Spread %' AS chosen_spread,
                   decision_payload->>'Candidate Direction' AS direction
            FROM scanner_snapshot
            WHERE jsonb_typeof(decision_payload->'Option Liquidity Attempts') = 'string'
              AND decision_payload->>'Candidate Direction' IN ('CALL','PUT')
            ORDER BY scan_timestamp
        """)).mappings().all()

    print(f"\n  scans with a chain and a direction : {len(rows)}")
    print(f"  usable = spread <= {MAX_SPREAD}%, cost <= ${MAX_COST:.0f}, "
          f"OI >= {MIN_OI}, volume >= {MIN_VOLUME}\n")

    refused_on_options = 0
    had_usable = 0
    accepted_anyway = 0
    missed_by_symbol = Counter()
    missed_spreads, chosen_spreads = [], []
    why_not = Counter()

    for record in rows:

        try:
            chain = json.loads(record["chain"] or "[]")
        except Exception:
            continue
        if not chain:
            continue

        accepted = [c for c in chain if c.get("accepted")]
        if accepted:
            accepted_anyway += 1
            continue

        # The app bought nothing on this scan. Was anything usable available?
        refused_on_options += 1

        usable = []
        for contract in chain:
            spread = number(contract.get("spread_pct"))
            cost = number(contract.get("contract_cost"))
            oi = number(contract.get("open_interest"))
            volume = number(contract.get("volume"))

            if spread is None or spread <= 0:
                continue
            if str(contract.get("quote_status") or "QUOTE_OK") != "QUOTE_OK":
                continue

            if spread > MAX_SPREAD:
                why_not["spread too wide"] += 1
                continue
            if cost is None or cost > MAX_COST:
                why_not["over the cost cap"] += 1
                continue
            if oi is None or oi < MIN_OI:
                why_not["open interest too low"] += 1
                continue
            if volume is None or volume < MIN_VOLUME:
                why_not["volume too low"] += 1
                continue

            usable.append((spread, contract))

        if usable:
            had_usable += 1
            usable.sort(key=lambda pair: pair[0])
            missed_spreads.append(usable[0][0])
            missed_by_symbol[record["symbol"]] += 1
            picked = number(record["chosen_spread"])
            if picked:
                chosen_spreads.append(picked)

    print(f"  scans where the app bought something   : {accepted_anyway}")
    print(f"  scans where it bought nothing          : {refused_on_options}")

    if not refused_on_options:
        print("\n  nothing refused; stopping.\n")
        return

    share = had_usable / refused_on_options * 100
    print(f"  ... and a USABLE contract was available: {had_usable}"
          f"  ({share:.1f}%)\n")

    if missed_spreads:
        print(f"  the missed contract's spread: median {st.median(missed_spreads):.2f}%"
              f"   best {min(missed_spreads):.2f}%")
    if chosen_spreads:
        print(f"  the spread the app reported : median {st.median(chosen_spreads):.2f}%")

    print(f"\n  why every other contract failed (counted across all refused scans):")
    total = sum(why_not.values()) or 1
    for label, count in why_not.most_common():
        print(f"    {label:<26}{count:>9}  {count / total * 100:>5.1f}%")

    if missed_by_symbol:
        print(f"\n  misses by symbol:")
        for symbol, count in missed_by_symbol.most_common(12):
            print(f"    {symbol:<8}{count:>6}")

    print(f"\n  If the share is high, a usable contract was there and the ranker")
    print(f"  walked past it -- preferring it is a small, targeted change.")
    print(f"  If it is low, the cost envelope is the binding constraint and")
    print(f"  re-ranking would only reach for contracts the cap rejects.\n")


if __name__ == "__main__":
    main()
