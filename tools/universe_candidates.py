"""Would adding high-movement names give this account anything it can trade?

The operator's question, after NBIS ran 9.3% on 2026-08-14 while the app watched
26 megacaps: **should the universe hold names that actually move?**

NBIS is the reason to be careful. It moved, and its chain was still useless to
this account -- median contract $2,270 against a $1,000 cap, median spread 6.4%.
The property that makes a name travel is high implied volatility, and high IV
raises premium and widens quotes at the same time. So movement and tradeability
pull against each other, and a universe chosen on movement alone can add nothing
but refusals.

This is the cheap screen that runs before any of that costs option quotes. For
each candidate it reports what a chain would have to overcome:

    range%      median daily high-low as a percent of close -- the movement
    price       share price, which sets the floor on what a near-ATM option costs
    atr%        14-day ATR, the volatility the option is priced off

**Selection rule, stated before the data was pulled** so it cannot be tuned to a
result: liquid US-listed optionable names outside the current watchlist, drawn
from the high-beta pockets a retail options subscriber actually trades -- AI
infrastructure, crypto proxies, quantum, nuclear, fintech and high-growth
software. NBIS and MSTR are in the list because the operator raised them, and
they are marked, because a name chosen after its move is evidence of nothing.

Underlying bars only. No option quotes, no chain walk -- this exists to decide
which names are worth spending quota on.

    python tools/universe_candidates.py
    python tools/universe_candidates.py --days 30

Needs POLYGON_API_KEY.
"""

import argparse
import pathlib
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import warnings

from dotenv import load_dotenv

warnings.filterwarnings("ignore")
load_dotenv()

from app.config.watchlist import WATCHLIST
from app.indicators.technical_indicators import get_polygon_data

# Raised by the operator after the fact. Kept separate from the rule-chosen set
# so no conclusion rests on names picked because they already moved.
RAISED = ["NBIS", "MSTR"]

CANDIDATES = [
    # AI infrastructure and semis
    "VRT", "ANET", "DELL", "CRDO", "ALAB",
    # crypto proxies
    "COIN", "MARA", "RIOT", "CLSK",
    # quantum and nuclear
    "IONQ", "RGTI", "OKLO", "SMR",
    # fintech and consumer high-beta
    "HOOD", "SOFI", "AFRM", "RBLX", "DKNG",
    # high-growth software
    "APP", "NET", "DDOG", "SNOW",
]

# A near-ATM option runs roughly 2-5% of the share price for a few weeks of
# tenor. Above this the cheapest tradeable contract tends to breach the cap, and
# what is left under it is far OTM, thin and wide -- which is exactly what NBIS
# looked like.
CAP = 1000.0
PRICE_WARNING = 250.0


def stats(symbol, days):

    frame = get_polygon_data(symbol, 1, "day", days)

    if frame is None or frame.empty or len(frame) < 10:
        return None

    frame = frame.tail(days)
    ranges = ((frame["High"] - frame["Low"]) / frame["Close"] * 100.0).dropna()

    if ranges.empty:
        return None

    close = float(frame["Close"].iloc[-1])

    return {
        "price": close,
        "range": float(ranges.median()),
        "big_days": float((ranges >= 5.0).mean() * 100.0),
        "dollar_vol": float((frame["Close"] * frame["Volume"]).median()) / 1e6,
    }


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    print(f"\n  median daily range and price over the last {args.days} sessions")
    print(f"  a near-ATM contract costs roughly 2-5% of share price;"
          f" the cap is ${CAP:.0f}\n", flush=True)

    baseline = []

    for symbol in sorted(WATCHLIST):
        row = stats(symbol, args.days)
        if row:
            baseline.append(row)

    median_range = st.median([r["range"] for r in baseline])
    median_price = st.median([r["price"] for r in baseline])

    print(f"  current watchlist ({len(baseline)} names): "
          f"median range {median_range:.2f}%, median price ${median_price:.0f}\n")

    rows = []

    # A name already in the universe cannot be an argument for widening it. The
    # first run of this listed MRVL, which is on the watchlist and ranked sixth
    # on movement -- so the guard is here rather than in the literal above.
    already = sorted(set(CANDIDATES + RAISED) & set(WATCHLIST))

    if already:
        print(f"  skipping {', '.join(already)} -- already in the watchlist"
              f" (the universe already holds movers)\n")

    for symbol in CANDIDATES + RAISED:

        if symbol in WATCHLIST:
            continue

        row = stats(symbol, args.days)

        if row:
            row["symbol"] = symbol
            row["raised"] = symbol in RAISED
            rows.append(row)

    rows.sort(key=lambda r: -r["range"])

    print(f"  {'symbol':8}{'range%':>8}{'>=5% days':>11}{'price':>9}"
          f"{'$vol M':>9}   verdict")
    print(f"  {'':-<70}")

    for row in rows:

        # Two independent ways to be useless: not moving, or moving but pricing
        # every near-ATM contract out of reach.
        if row["range"] < median_range:
            verdict = "no better than what we hold"
        elif row["price"] > PRICE_WARNING:
            verdict = f"moves, but ${row['price']:.0f} -- cost risk"
        elif row["dollar_vol"] < 100:
            verdict = "thin -- option chain likely unusable"
        else:
            verdict = "worth pricing"

        mark = " *" if row["raised"] else ""

        print(f"  {row['symbol'] + mark:8}{row['range']:>8.2f}"
              f"{row['big_days']:>10.0f}%{row['price']:>9.0f}"
              f"{row['dollar_vol']:>9.0f}   {verdict}")

    print("\n  * raised by the operator after the move, not rule-selected")
    print("  'worth pricing' is a shortlist for the chain screen, not a "
          "recommendation\n")


if __name__ == "__main__":
    main()
