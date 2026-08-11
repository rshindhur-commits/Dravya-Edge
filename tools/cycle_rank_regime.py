"""S08 and S09 -- the two remaining edge-research cycles, on the fixed split.

Both ask whether information the app currently ignores separates the winners
from the losers. Every rule today is single-symbol and points-in-time:

    S08  cross-sectional relative strength. At any moment ten-odd candidates
         are live and the app judges each alone. Rank them against each other
         and take longs only from the strong end, shorts only from the weak end.

    S09  regime conditioning. `f_market_regime` is derived from the symbol's own
         bars, so it says nothing about the market. SPY and QQQ are in the
         universe, so a real market state is available at every moment and has
         never been used.

Both are fitted on the training sessions alone and applied unchanged to the
holdout, and the improvement is resampled with the filter applied inside each
draw. A one-sided book is reported as drift rather than edge -- the 234-bar
horizon on this dataset pays longs +1.48% and shorts -0.15%, which is the market
over the window and confirmed as an edge by any test that does not look.

    python tools/cycle_rank_regime.py research/candidates_21day.json
    python tools/cycle_rank_regime.py research/candidates_21day.json --record
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import random
import statistics
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.research.holdout import (  # noqa: E402
    BREAK_EVEN_CAPTURED_PCT,
    load_split,
    record_comparison,
)

BOOTSTRAP_SEED = 20260810
BOOTSTRAP_DRAWS = 10000

# 234 bars is roughly three sessions and 285 of 291 archived trades are closed
# at the bell, so it is excluded rather than reported: an arm cannot be
# confirmed on a horizon the strategy cannot hold.
HORIZONS = ("12", "39", "78")

RANK_GRID = [0.0, 0.2, 0.3, 0.4, 0.5, 0.6]
BREADTH_GRID = [0.0, 0.1, 0.2, 0.3, 0.4]


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def annotate(rows):
    """Add the two things the app never computes: a rank, and a market state."""

    by_moment = collections.defaultdict(list)
    for r in rows:
        by_moment[r["moment"]].append(r)

    for moment, group in by_moment.items():
        moves = [(_f(r.get("f15_SYMBOL_MOVE_PCT")), r) for r in group]
        ranked = sorted((m, r) for m, r in moves if m is not None)
        n = len(ranked)

        for index, (_move, r) in enumerate(ranked):
            # 0 = weakest of everything live at this moment, 1 = strongest.
            r["_rank"] = index / (n - 1) if n > 1 else 0.5

        for r in group:
            r.setdefault("_rank", 0.5)

        # Market state from the index members rather than from the candidate's
        # own bars, which is the whole point of S09. Falls back to breadth
        # across the universe when neither index is live at this moment.
        index_moves = [
            _f(r.get("f15_SYMBOL_MOVE_PCT"))
            for r in group
            if r.get("symbol") in {"SPY", "QQQ"}
        ]
        index_moves = [m for m in index_moves if m is not None]

        if index_moves:
            market = statistics.fmean(index_moves)
        else:
            live = [m for m, _ in moves if m is not None]
            market = statistics.fmean(live) if live else 0.0

        for r in group:
            r["_market"] = market

    return rows


def captured(rows, horizon):
    return [
        float(r[f"label_fwd_{horizon}"])
        for r in rows
        if r.get(f"label_fwd_{horizon}") is not None
    ]


def drift(rows, horizon):
    longs = [float(r[f"label_fwd_{horizon}"]) for r in rows if not r.get("is_short")]
    shorts = [float(r[f"label_fwd_{horizon}"]) for r in rows if r.get("is_short")]

    if not longs or not shorts:
        return None, None, False

    lm, sm = statistics.fmean(longs), statistics.fmean(shorts)
    return lm, sm, (lm > 0) != (sm > 0)


def paired_delta_ci(rows, keep, horizon):
    """CI on (filtered mean - control mean), resampled together."""

    rng = random.Random(BOOTSTRAP_SEED)
    deltas = []

    for _ in range(BOOTSTRAP_DRAWS):
        draw = rng.choices(rows, k=len(rows))
        base = captured(draw, horizon)
        kept = captured([r for r in draw if keep(r)], horizon)

        if not base or not kept:
            continue

        deltas.append(statistics.fmean(kept) - statistics.fmean(base))

    if not deltas:
        return None, None

    deltas.sort()
    return deltas[int(len(deltas) * 0.025)], deltas[int(len(deltas) * 0.975)]


def run_cycle(name, description, train, holdout, grid, keep_for, env_hint):
    print(f"\n{'=' * 78}\n{name} -- {description}\n{'=' * 78}")
    verdicts = {}

    for horizon in HORIZONS:
        base_train = captured(train, horizon)
        base_hold = captured(holdout, horizon)

        if not base_train or not base_hold:
            continue

        print(f"\n--- horizon {horizon} bars ---")
        print(f"  control (current rules): train n={len(base_train)} "
              f"{statistics.fmean(base_train):+.4f}%   "
              f"holdout n={len(base_hold)} {statistics.fmean(base_hold):+.4f}%")

        best, best_mean = None, None
        print(f"  {env_hint} swept on TRAIN only:")

        for threshold in grid:
            keep = keep_for(threshold)
            kept = captured([r for r in train if keep(r)], horizon)

            if len(kept) < 30:
                continue

            mean = statistics.fmean(kept)
            flag = ""
            if best_mean is None or mean > best_mean:
                best, best_mean, flag = threshold, mean, "  <-- best"
            print(f"    {threshold:<5} n={len(kept):<5} {mean:+.4f}%{flag}")

        if best is None:
            print("  no threshold left enough trades to judge")
            continue

        keep = keep_for(best)
        kept_hold = captured([r for r in holdout if keep(r)], horizon)

        if len(kept_hold) < 20:
            print(f"  train picked {best}, but only {len(kept_hold)} holdout trades survive it")
            verdicts[horizon] = "TOO_FEW_HOLDOUT"
            continue

        control_mean = statistics.fmean(base_hold)
        arm_mean = statistics.fmean(kept_hold)
        lo, hi = paired_delta_ci(holdout, keep, horizon)
        lm, sm, is_drift = drift([r for r in holdout if keep(r)], horizon)

        print(f"\n  train answer ({best}) applied to HOLDOUT:")
        print(f"    control  n={len(base_hold):<5} {control_mean:+.4f}%")
        print(f"    arm      n={len(kept_hold):<5} {arm_mean:+.4f}%   "
              f"delta {arm_mean - control_mean:+.4f}%")
        if lo is not None:
            print(f"    95% CI on the delta [{lo:+.4f}, {hi:+.4f}]")
        if lm is not None:
            print(f"    longs {lm:+.4f}%  shorts {sm:+.4f}%")

        if best == grid[0] and grid[0] == 0.0:
            # The sweep preferred no filter at all. Saying anything about the
            # arm here would be describing the control under another name.
            verdict = "TRAIN_CHOSE_NO_FILTER"
        elif lo is None:
            verdict = "NO_INTERVAL"
        elif is_drift and arm_mean > 0:
            verdict = "DRIFT_NOT_EDGE"
        elif lo > 0 and arm_mean >= BREAK_EVEN_CAPTURED_PCT:
            verdict = "CONFIRMED"
        elif lo > 0:
            verdict = "BETTER_BUT_BELOW_BAR"
        else:
            verdict = "NOT_DISTINGUISHABLE"

        verdicts[horizon] = verdict
        print(f"    VERDICT: {verdict}"
              + (f"  (bar is {BREAK_EVEN_CAPTURED_PCT:+.3f}%)" if "BAR" in verdict or verdict == "CONFIRMED" else ""))

    return verdicts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset")
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()

    rows = annotate(json.loads(pathlib.Path(args.dataset).read_text()))
    split = load_split()

    if split is None:
        raise SystemExit("No fixed split; run the S05 framework first.")

    # Both cycles refine what the current rules already admit, so the control is
    # the current rules and not the whole candidate pool.
    allowed = [r for r in rows if r.get("control_allowed")]
    train = [r for r in allowed if r["day"] in set(split["train"])]
    holdout = [r for r in allowed if r["day"] in set(split["holdout"])]

    print(f"{args.dataset}: {len(rows)} rows, {len(allowed)} admitted by current rules")
    print(f"train {len(train)} / holdout {len(holdout)}   "
          f"bar {BREAK_EVEN_CAPTURED_PCT:+.3f}% captured move")

    def rank_keep(edge):
        def keep(r):
            if edge <= 0:
                return True
            return (r["_rank"] >= 1 - edge) if not r.get("is_short") else (r["_rank"] <= edge)
        return keep

    def regime_keep(threshold):
        def keep(r):
            if threshold <= 0:
                return True
            market = r.get("_market", 0.0)
            return market >= -threshold if not r.get("is_short") else market <= threshold
        return keep

    s08 = run_cycle(
        "S08", "cross-sectional relative strength (rank within the moment)",
        train, holdout, RANK_GRID, rank_keep, "top/bottom fraction",
    )
    s09 = run_cycle(
        "S09", "regime conditioning on SPY/QQQ (direction must agree with the market)",
        train, holdout, BREADTH_GRID, regime_keep, "market move tolerance %",
    )

    if args.record:
        n = record_comparison("S08 cross-sectional rank", detail={"verdicts": s08})
        n = record_comparison("S09 regime conditioning", detail={"verdicts": s09})
        print(f"\nrecorded; {n} arms run to date")
    else:
        print("\nnot recorded (pass --record)")


if __name__ == "__main__":
    main()
