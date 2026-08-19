"""Do the symbols priced out by the contract cost cap actually carry a signal?

    python tools/cost_blocked_signal_quality.py
    python tools/cost_blocked_signal_quality.py --horizon 90

`per_symbol_cost_caps()` already exists and already carries AVGO, SMH and GOOGL,
admitted on exactly this evidence: they were **unreachable rather than
unprofitable**, at +1.098R and +1.070R on the underlying. Nothing about that
argument is specific to those three, and `tools/option_rejection_report.py` names
others every session -- MRVL at $1,200, CRWD at $1,240, ORCL at $1,150 against a
$1,000 cap on 2026-08-18.

This is the test those three had to pass, run for every symbol.

## What is measured, and what is deliberately not

The **underlying**, not the option. A cost-blocked candidate has no fill, no
spread and no premium series, so any option-level number would be modelled twice
over. The underlying move is recorded fact, and it is the thing the cap is
choosing not to buy.

Progress is in R on the candidate's own recorded stop, so a $2 move on ORCL and a
$2 move on SMH are comparable. `best_r` is the best the move ever reached inside
the horizon -- the opportunity -- and `end_r` is where it finished. Both are
reported because a signal that spikes and gives it all back is not worth paying
more to reach, and `best_r` alone would hide that.

## The counter-evidence this must be read against

601 trades measured earlier showed the loss running at a near-constant share of
deployed capital: **the cap changes the stake, not the rate.** So a good `best_r`
here is necessary and not sufficient. It says the signal is real; it does not say
the option would have converted it, and the round trip on these names is exactly
what the cap was protecting against.

Read only as: *is this symbol unreachable, or is it unprofitable?* If unreachable
and the signal beats what the app currently trades, it earns a per-symbol cap and
then has to prove itself live like anything else.

Archive only. No network, no writes.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import statistics
import sys
from collections import defaultdict

BOOTSTRAP_SEED = 20260818
BOOTSTRAP_DRAWS = 5000

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env", override=True)

from sqlalchemy import text  # noqa: E402

from app.db.connection import get_engine  # noqa: E402

def _attempts(snapshot):
    """The per-contract verdicts the selector recorded for this scan."""

    raw = snapshot.get("Option Liquidity Attempts")

    if not raw:
        return []

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []

    return raw if isinstance(raw, list) else []


def _f(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    return None if result != result else result


def _payload(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}

    return value or {}


def _load(days):
    """Every archived candidate with usable geometry, tagged blocked or not."""

    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT trading_day, payload,
                       scan_timestamp AT TIME ZONE 'America/New_York' AS et
                FROM scanner_snapshot
                WHERE trading_day > current_date - :days
                ORDER BY scan_timestamp
                """
            ),
            {"days": days},
        ).mappings().all()

    candidates = []
    series = defaultdict(list)

    for row in rows:
        snapshot = _payload(row["payload"])
        symbol = snapshot.get("Symbol") or snapshot.get("symbol")
        price = _f(snapshot.get("Price"))

        if not symbol or price is None:
            continue

        series[(row["trading_day"], symbol)].append((row["et"], price))

        entry = _f(snapshot.get("Candidate Entry Price"))
        stop = _f(snapshot.get("Candidate Stop Price"))
        direction = str(snapshot.get("Candidate Direction") or "").upper()

        if None in (entry, stop) or entry == stop or direction not in {"CALL", "PUT"}:
            continue

        # The row-level reason reports the FIRST failure, and the filter checks
        # open interest before cost, so a symbol whose only real problem is price
        # is labelled LOW_OPEN_INTEREST and would be missed entirely here. The
        # per-contract attempt list is the only place the cost verdict survives.
        cheapest = None

        for attempt in _attempts(snapshot):

            if str(attempt.get("code")) != "OPTION_TOO_EXPENSIVE":
                continue

            cost = _f(attempt.get("contract_cost"))

            if cost is not None and (cheapest is None or cost < cheapest):
                cheapest = cost

        candidates.append({
            "day": row["trading_day"],
            "et": row["et"],
            "symbol": symbol,
            "short": direction == "PUT",
            "entry": entry,
            "risk": abs(entry - stop),
            "cost_blocked": cheapest is not None and not _f(
                snapshot.get("Option Entry Fill")
            ),
            "cost": cheapest,
        })

    return candidates, series


def _forward(candidate, series, horizon_minutes):
    """(best_r, end_r) over the horizon, or None when the day runs out."""

    prices = series.get((candidate["day"], candidate["symbol"]))

    if not prices:
        return None

    ahead = [
        price for when, price in prices
        if 0 < (when - candidate["et"]).total_seconds() <= horizon_minutes * 60
    ]

    if not ahead:
        return None

    def progress(price):
        if candidate["short"]:
            return (candidate["entry"] - price) / candidate["risk"]

        return (price - candidate["entry"]) / candidate["risk"]

    return max(progress(p) for p in ahead), progress(ahead[-1])


def _bootstrap(values):
    """95% interval on the mean.

    A median that looks good on 20 candidates beside an interval straddling zero
    is not evidence. Every lever in this project adopted on a point estimate has
    come back null on more data.
    """

    if len(values) < 3:
        return None, None

    rng = random.Random(BOOTSTRAP_SEED)
    means = []

    for _ in range(BOOTSTRAP_DRAWS):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(statistics.mean(sample))

    means.sort()
    return means[int(0.025 * BOOTSTRAP_DRAWS)], means[int(0.975 * BOOTSTRAP_DRAWS)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--horizon", type=int, default=60,
                        help="minutes of underlying to look forward")
    parser.add_argument("--min-n", type=int, default=8,
                        help="symbols with fewer blocked candidates are hidden")
    args = parser.parse_args()

    candidates, series = _load(args.days)
    print("candidates with geometry: {0}   cost-blocked: {1}".format(
        len(candidates), sum(1 for c in candidates if c["cost_blocked"])
    ))
    print("horizon: {0} minutes of the underlying\n".format(args.horizon))

    blocked = defaultdict(list)
    reached = defaultdict(list)
    costs = defaultdict(list)

    for candidate in candidates:
        forward = _forward(candidate, series, args.horizon)

        if forward is None:
            continue

        target = blocked if candidate["cost_blocked"] else reached
        target[candidate["symbol"]].append(forward)

        if candidate["cost_blocked"] and candidate["cost"]:
            costs[candidate["symbol"]].append(candidate["cost"])

    def summarise(bucket):
        best = [b for b, _ in bucket]
        end = [e for _, e in bucket]
        return statistics.median(best), statistics.median(end), len(bucket)

    all_reached = [f for values in reached.values() for f in values]

    if all_reached:
        b, e, n = summarise(all_reached)
        print("BASELINE -- candidates the app could already afford")
        print("  n={0}   median best {1:+.2f}R   median close {2:+.2f}R\n".format(n, b, e))

    print("COST-BLOCKED, by symbol   (>= {0} candidates)".format(args.min_n))
    print("  {0:<7}{1:>6}{2:>13}{3:>13}{4:>13}{5:>10}".format(
        "symbol", "n", "median best", "median close", "hit >=1R %", "med cost"
    ))

    rankable = []

    for symbol, bucket in blocked.items():
        if len(bucket) < args.min_n:
            continue

        best, end, n = summarise(bucket)
        hit = sum(1 for b, _ in bucket if b >= 1.0) / n * 100
        cost = statistics.median(costs[symbol]) if costs.get(symbol) else None
        rankable.append((best, symbol, n, end, hit, cost))

    for best, symbol, n, end, hit, cost in sorted(rankable, reverse=True):
        print("  {0:<7}{1:>6}{2:>+13.2f}{3:>+13.2f}{4:>13.0f}{5:>10}".format(
            symbol, n, best, end, hit,
            "${0:.0f}".format(cost) if cost else "-"
        ))

    # The close is what a held position actually gives back, so the interval
    # goes on that and not on the peak. A symbol with a wonderful peak whose
    # close straddles zero is a symbol that spikes and hands it straight back,
    # and paying more to reach it buys the spike and the giveback together.
    print("")
    print("ROBUSTNESS on the closing R -- mean, 95% interval, and the mean")
    print("with its best 5 candidates removed")
    print("  {0:<7}{1:>6}{2:>10}{3:>22}{4:>14}".format(
        "symbol", "n", "mean", "95% CI", "less top 5"
    ))

    for _best, symbol, _n, _end, _hit, _cost in sorted(rankable, reverse=True):
        ends = sorted(end for _peak, end in blocked[symbol])
        low, high = _bootstrap(ends)
        trimmed = ends[:-5] if len(ends) > 5 else []
        print("  {0:<7}{1:>6}{2:>+10.2f}{3:>22}{4:>14}".format(
            symbol, len(ends), statistics.mean(ends),
            "[{0:+.2f}, {1:+.2f}]".format(low, high) if low is not None else "-",
            "{0:+.2f}".format(statistics.mean(trimmed)) if trimmed else "-",
        ))

    print("\nA symbol earns a per-symbol cap only if its median best clears the")
    print("baseline, its close does not give the whole move back, AND its")
    print("interval on the close excludes zero. The cap changes the stake, not")
    print("the win rate -- see the 601-trade result.")


if __name__ == "__main__":
    main()
