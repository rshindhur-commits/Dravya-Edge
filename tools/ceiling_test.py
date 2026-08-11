"""S-B -- is there ANY exit that makes this profitable?

Gate 1 of docs/OPTIONS_QUALITY_PLAN.md, and it can answer no.

Every archived trade is replayed against exit policies it never had, including
one that is impossible: selling at the best price that ever existed. If a book
with perfect foresight still loses after costs, then no entry rule, exit rule or
threshold rescues it, and options are the wrong instrument for this signal at
this horizon.

R is converted to cash by fitting realised premium against realised R on the
same trades:

    premium% = a x R + b

`b` is the toll -- what a trade costs before the underlying moves at all -- and
it is why R and cash disagree in sign so routinely on this book. The fit is
reported with its correlation so a weak one is visible rather than assumed.

Ordering. The archive records MFE and MAE but not which came first. Where a
policy's target and its stop were both reached, **the stop is taken**. Intrabar
order is unknowable and assuming otherwise manufactures exactly the edge being
looked for.

    python tools/ceiling_test.py data/forward_runs/phase1_21day_20260803_202603.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import statistics
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BOOTSTRAP_SEED = 20260810
BOOTSTRAP_DRAWS = 10000


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load(path):
    rows = []

    for t in json.loads(pathlib.Path(path).read_text())["trades"]:
        mfe, mae, r = _f(t.get("mfe_r")), _f(t.get("mae_r")), _f(t.get("r_multiple"))
        entry, exit_ = _f(t.get("option_entry_fill")), _f(t.get("option_exit_fill"))

        if None in (mfe, mae, r) or not entry or exit_ is None or entry <= 0:
            continue

        rows.append({
            "mfe": mfe, "mae": mae, "r": r,
            "premium": (exit_ - entry) / entry * 100.0,
            "symbol": t.get("symbol"),
        })

    return rows


def fit(rows):
    """premium% = a*R + b, least squares, with the correlation behind it."""

    xs = [r["r"] for r in rows]
    ys = [r["premium"] for r in rows]
    n = len(xs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    a = sxy / sxx
    b = my - a * mx
    corr = sxy / (sxx ** 0.5 * syy ** 0.5)

    return a, b, corr, n


def ci(values):
    rng = random.Random(BOOTSTRAP_SEED)
    means = sorted(
        statistics.fmean(rng.choices(values, k=len(values)))
        for _ in range(BOOTSTRAP_DRAWS)
    )
    return means[int(BOOTSTRAP_DRAWS * 0.025)], means[int(BOOTSTRAP_DRAWS * 0.975)]


def report(label, r_values, a, b, note=""):
    premium = [a * r + b for r in r_values]
    lo, hi = ci(premium)
    wins = sum(1 for p in premium if p > 0)
    mean_r = statistics.fmean(r_values)

    print(f"  {label:<34}{mean_r:>+8.3f}R{statistics.fmean(premium):>+9.2f}%"
          f"{statistics.median(premium):>+9.2f}%   [{lo:+.2f}, {hi:+.2f}]"
          f"{100.0 * wins / len(premium):>7.0f}%  {note}")

    return statistics.fmean(premium), lo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset")
    parser.add_argument("--stop", type=float, default=1.0,
                        help="stop in R for the fixed-target policies")
    args = parser.parse_args()

    rows = load(args.dataset)
    a, b, corr, n = fit(rows)

    print(f"{args.dataset}: {n} trades\n")
    print(f"premium% = {a:.3f} x R {b:+.3f}     corr {corr:+.3f}")
    print(f"  the intercept is the toll: {b:+.2f}% of premium before the underlying moves")
    print(f"  a 1R move is worth {a:.1f}% of premium, so break-even needs "
          f"{-b / a:.3f}R\n")

    print(f"  {'policy':<34}{'mean R':>9}{'mean %':>10}{'median':>10}"
          f"{'95% CI':>18}{'win%':>7}")

    actual_mean = statistics.fmean(r["premium"] for r in rows)
    actual_lo, actual_hi = ci([r["premium"] for r in rows])
    print(f"  {'actual (what happened)':<34}"
          f"{statistics.fmean(r['r'] for r in rows):>+8.3f}R"
          f"{actual_mean:>+9.2f}%"
          f"{statistics.median(r['premium'] for r in rows):>+9.2f}%"
          f"   [{actual_lo:+.2f}, {actual_hi:+.2f}]"
          f"{100.0 * sum(1 for r in rows if r['premium'] > 0) / len(rows):>7.0f}%")

    # The ceiling. Impossible by construction -- it sells at the peak.
    ceiling_mean, ceiling_lo = report(
        "CEILING: exit at MFE", [r["mfe"] for r in rows], a, b, "<- perfect foresight",
    )

    # Fractions of the peak: what a very good, still-unreachable exit would give.
    for frac in (0.75, 0.50):
        report(f"exit at {frac:.0%} of MFE", [r["mfe"] * frac for r in rows], a, b)

    # Fixed targets, which are implementable. Stop wins any tie.
    print()
    for target in (0.5, 1.0, 1.5, 2.0):
        outcomes = []
        for r in rows:
            if r["mae"] >= args.stop:
                outcomes.append(-args.stop)
            elif r["mfe"] >= target:
                outcomes.append(target)
            else:
                outcomes.append(r["r"])
        report(f"target {target}R / stop {args.stop}R", outcomes, a, b)

    print()
    print("=" * 78)
    if ceiling_lo > 0:
        print("GATE 1: PASS -- a perfect-exit book profits after costs.")
        print(f"  ceiling {ceiling_mean:+.2f}% vs actual {actual_mean:+.2f}% of premium.")
        print(f"  The {ceiling_mean - actual_mean:.2f} point gap is what entry and exit")
        print("  work could in principle address. It is an upper bound and not a target:")
        print("  no exit sells at the peak.")
    else:
        print("GATE 1: FAIL -- the best exit that ever existed still loses after costs.")
        print("  No entry rule, exit rule or threshold changes this. Options are the")
        print("  wrong instrument for this signal at this horizon.")
    print("=" * 78)


if __name__ == "__main__":
    main()
