"""Test every recorded feature against outcome at once, instead of by hand.

On 2026-08-15 four features were tested one at a time -- sector, stop size
against the day's range, stop against the prior range, and entry placement in the
range. Three were null and the fourth was lookahead. Each took twenty minutes and
each carried the same flaw: picking a feature, testing it, and stopping when one
looks good is not a search, it is a licence to find noise.

The app records 322 fields per candidate at decision time, 138 of them numeric.
Testing all of them with a corrected threshold is *more* rigorous than testing
four without one, because the bar rises to match the number of attempts.

Method, in order:

  1  every candidate whose outcome resolved -- reached its target first, or its
     stop first -- joined to the features recorded when the decision was made
  2  split by date: the earlier half discovers, the later half confirms
  3  for each feature, Welch's t between the two outcome groups
  4  Bonferroni: alpha 0.05 divided by the number of features tested
  5  a survivor must clear the corrected bar on the discovery half AND keep the
     same sign on the holdout half

Known limit, stated because it bounds what a null result means. The resolved
sample is ~310 candidates, not the 3,239 rows in `candidate_evidence` -- most
never resolved. At that size a corrected sweep can only detect a strong effect,
so "nothing survives" means "no strong single-feature predictor", not "no
predictor". A weak but real signal would be missed here and would need either
more sessions or a model over several features at once.

    python tools/feature_sweep.py
    python tools/feature_sweep.py --min-coverage 0.8

Reads Postgres only. No bars, no option quotes, no network.
"""

import argparse
import math
import pathlib
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.db.connection import get_engine

# Fields that leak the answer or identify the row rather than describing it.
# `Candidate Entry Price` is the price itself, which correlates with the symbol
# and nothing else; the RR fields are the gate's own arithmetic on the geometry.
EXCLUDED_PREFIXES = (
    "ENTRY_GATE_", "CHAIN_NEAR_MISS_", "Bars In Trade",
)
EXCLUDED_EXACT = {
    "Candidate Entry Price", "Candidate Stop Price", "Candidate Target Price",
    "Candidate Trigger", "Decision Candle Close", "Decision Candle High",
    "Decision Candle Low", "Decision Candle Open", "ENTRY_CLOSE",
    "Current Capital",
}


def usable(name):
    if name in EXCLUDED_EXACT:
        return False
    return not any(name.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def number(value):
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        result = float(value)
        return None if result != result or math.isinf(result) else result
    except (TypeError, ValueError):
        return None


def load():
    """Resolved candidates, each with the features recorded at decision time."""

    with get_engine().begin() as connection:

        return connection.execute(text("""
            SELECT DISTINCT ON (e.candidate_id)
                   e.candidate_id, e.trading_day, e.symbol,
                   e.target_first, e.stop_first,
                   s.decision_payload AS features
            FROM candidate_evidence e
            JOIN scanner_snapshot s
              ON s.trading_day = e.trading_day AND s.symbol = e.symbol
            WHERE (e.target_first OR e.stop_first)
              AND s.decision_payload->>'Candidate Entry Price' IS NOT NULL
            ORDER BY e.candidate_id, s.scan_timestamp
        """)).mappings().all()


def welch(a, b):
    """Welch's t and its two-sided p, without scipy."""

    if len(a) < 8 or len(b) < 8:
        return None, None

    va, vb = st.pvariance(a), st.pvariance(b)
    na, nb = len(a), len(b)

    se = math.sqrt(va / na + vb / nb)
    if se <= 0:
        return None, None

    t = (st.mean(a) - st.mean(b)) / se

    # Normal approximation for the p-value. At n>=100 per group the difference
    # from the exact t distribution is far smaller than the Bonferroni margin.
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2))))

    return t, p


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--min-coverage", type=float, default=0.7,
                        help="skip features present on fewer than this share of rows")
    args = parser.parse_args()

    rows = load()

    if len(rows) < 40:
        print(f"\n  only {len(rows)} resolved candidates joined; too few to sweep.\n")
        return

    days = sorted({str(r["trading_day"]) for r in rows})
    split = days[len(days) // 2]

    print(f"\n  resolved candidates : {len(rows)}")
    print(f"  sessions            : {len(days)}  ({days[0]} .. {days[-1]})")
    print(f"  discovery / holdout : before {split} / from {split}")

    # Collect every numeric feature.
    values = defaultdict(list)
    for row in rows:
        payload = row["features"] or {}
        won = bool(row["target_first"])
        half = "A" if str(row["trading_day"]) < split else "B"
        for name, raw in payload.items():
            if not usable(name):
                continue
            value = number(raw)
            if value is not None:
                values[name].append((half, won, value))

    tested = {
        name: series for name, series in values.items()
        if len(series) >= len(rows) * args.min_coverage
        and len({v for _h, _w, v in series}) > 3          # not a constant
    }

    alpha = 0.05 / max(1, len(tested))

    print(f"  numeric features    : {len(values)}")
    print(f"  tested (>= {args.min_coverage:.0%} coverage, not constant) : {len(tested)}")
    print(f"  Bonferroni alpha    : {alpha:.6f}\n")

    results = []

    for name, series in tested.items():

        a_win = [v for h, w, v in series if h == "A" and w]
        a_lose = [v for h, w, v in series if h == "A" and not w]
        b_win = [v for h, w, v in series if h == "B" and w]
        b_lose = [v for h, w, v in series if h == "B" and not w]

        t_a, p_a = welch(a_win, a_lose)
        t_b, _p_b = welch(b_win, b_lose)

        if t_a is None:
            continue

        results.append({
            "name": name, "t_disc": t_a, "p_disc": p_a, "t_hold": t_b,
            "n": len(series),
            "same_sign": (t_b is not None and (t_a > 0) == (t_b > 0)),
        })

    results.sort(key=lambda r: r["p_disc"])

    print(f"  {'feature':<38}{'n':>5}{'t disc':>9}{'p disc':>11}{'t hold':>9}  verdict")
    print(f"  {'-' * 84}")

    survivors = 0

    for r in results[:25]:

        clears = r["p_disc"] < alpha
        verdict = (
            "SURVIVES" if clears and r["same_sign"]
            else "flips on holdout" if clears
            else ""
        )
        survivors += 1 if verdict == "SURVIVES" else 0

        hold = "    -" if r["t_hold"] is None else f"{r['t_hold']:+.2f}"

        print(f"  {r['name'][:38]:<38}{r['n']:>5}{r['t_disc']:>+9.2f}"
              f"{r['p_disc']:>11.5f}{hold:>9}  {verdict}")

    print()
    if survivors:
        print(f"  {survivors} feature(s) cleared the corrected bar and held their sign.\n")
    else:
        print("  Nothing cleared the corrected bar on the discovery half while")
        print("  keeping its sign on the holdout half.\n")
        print("  Read that as: no STRONG single-feature predictor exists in what the")
        print("  app records. At this sample a weak one would not be detectable, so")
        print("  it does not rule out a multi-feature model or more sessions.\n")


if __name__ == "__main__":
    main()
