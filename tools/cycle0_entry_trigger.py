"""S08a, Cycle 0 -- does removing the entry trigger beat keeping it?

The question asked ahead of every feature in Phase 1, because it is a deletion
rather than an addition and the evidence for it arrived from two directions:

* entry timing scored *worse than random* on 2026-08-09, by 0.12-0.31 points,
  20/20 draws at every horizon, train and holdout agreeing within 0.06;
* 44% of the 291-trade forward archive never goes green by even 0.05R, and on
  2026-08-10 the trigger declined CRWD (+5.0%) 42 times and PANW (+5.8%) 43
  times across their entire moves.

Arms, both keeping the direction call and differing only in the trigger:

    control   rows the entry trigger admits          (control_allowed = True)
    cycle0    every row carrying a direction         (trigger removed)

Judged by `app/research/holdout.judge` against the +0.155% break-even, fitted on
the training sessions and confirmed on the holdout, which is fixed and is never
moved by this script.

    python tools/cycle0_entry_trigger.py research/candidates_21day.json
    python tools/cycle0_entry_trigger.py research/candidates_21day.json --record
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

from app.research.holdout import (  # noqa: E402
    BREAK_EVEN_CAPTURED_PCT,
    judge,
    load_split,
    partition,
    record_comparison,
)

HORIZONS = ("12", "39", "78", "234")

# Fixed so a rerun reproduces the interval rather than a new one each time.
BOOTSTRAP_SEED = 20260810
BOOTSTRAP_DRAWS = 10000


def _mean_ci(values, draws=BOOTSTRAP_DRAWS):
    """Mean with a 95% bootstrap interval, and the mean without the top 5.

    The trimmed figure sits beside every headline because the withdrawn
    "+14.43% captured" claim of 2026-08-09 was 5 trades of 331; a mean that
    collapses when its best five rows are removed is not an edge.
    """

    if not values:
        return None, (None, None), None

    rng = random.Random(BOOTSTRAP_SEED)
    n = len(values)
    means = sorted(
        statistics.fmean(rng.choices(values, k=n)) for _ in range(draws)
    )
    lo = means[int(draws * 0.025)]
    hi = means[int(draws * 0.975)]
    trimmed = sorted(values)[:-5] if n > 5 else []

    return (
        statistics.fmean(values),
        (lo, hi),
        statistics.fmean(trimmed) if trimmed else None,
    )


def _captured(rows, horizon):
    return [
        float(r[f"label_fwd_{horizon}"])
        for r in rows
        if r.get(f"label_fwd_{horizon}") is not None
    ]


def _drift_check(rows, horizon):
    """Is an arm's return an edge, or is it the market moving?

    `judge` tests a mean against the break-even bar and its interval against
    zero. Neither notices that the mean came entirely from one side of the book.
    On this dataset the 234-bar holdout pays longs +1.48% and shorts -0.15%,
    and the training half pays the mirror image -- the sign tracks the market
    over the window, not the signal -- so an arm long enough to accumulate beta
    is CONFIRMED for holding stock through a rally.

    An edge earns on both sides. Opposite signs mean the direction call is
    picking up drift, and the verdict has to say so.

    Returns (long_mean, short_mean, is_drift).
    """

    longs = [
        float(r[f"label_fwd_{horizon}"])
        for r in rows
        if not r.get("is_short") and r.get(f"label_fwd_{horizon}") is not None
    ]
    shorts = [
        float(r[f"label_fwd_{horizon}"])
        for r in rows
        if r.get("is_short") and r.get(f"label_fwd_{horizon}") is not None
    ]

    if not longs or not shorts:
        return None, None, False

    long_mean = statistics.fmean(longs)
    short_mean = statistics.fmean(shorts)

    return long_mean, short_mean, (long_mean > 0) != (short_mean > 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset")
    parser.add_argument(
        "--record",
        action="store_true",
        help="append this arm to research/comparisons.jsonl",
    )
    args = parser.parse_args()

    rows = json.loads(pathlib.Path(args.dataset).read_text())
    split = load_split()

    if split is None:
        raise SystemExit(
            "No fixed split. Run the S05 framework first -- this script must "
            "never fix a split of its own."
        )

    train_rows, holdout_rows = partition(rows, session_key="day")

    print(f"dataset {args.dataset}: {len(rows)} rows, "
          f"{len(set(r['day'] for r in rows))} sessions")
    print(f"split   train {len(split['train'])} sessions / "
          f"holdout {len(split['holdout'])} sessions "
          f"(fixed {split['fixed_at'][:10]})")
    print(f"bar     {BREAK_EVEN_CAPTURED_PCT:+.3f}% captured move per trade\n")

    arms = [
        ("control  (trigger kept)", lambda r: bool(r.get("control_allowed"))),
        ("cycle0   (trigger removed)", lambda r: True),
    ]

    verdicts = {}

    for horizon in HORIZONS:
        print(f"===== horizon {horizon} bars =====")
        print(f"  {'arm':<28}{'n_train':>8}{'train%':>9}"
              f"{'n_hold':>8}{'hold%':>9}{'hold 95% CI':>24}{'no-top5':>10}")

        summary = {}

        for name, keep in arms:
            tr = _captured([r for r in train_rows if keep(r)], horizon)
            ho = _captured([r for r in holdout_rows if keep(r)], horizon)
            tr_mean, _, _ = _mean_ci(tr)
            ho_mean, ho_ci, ho_trim = _mean_ci(ho)

            summary[name] = (tr_mean, ho_mean, ho_ci)

            ci = f"[{ho_ci[0]:+.4f}, {ho_ci[1]:+.4f}]" if ho_ci[0] is not None else "-"
            trim = f"{ho_trim:+.4f}" if ho_trim is not None else "-"
            print(f"  {name:<28}{len(tr):>8}{tr_mean:>+9.4f}"
                  f"{len(ho):>8}{ho_mean:>+9.4f}{ci:>24}{trim:>10}")

        print()
        for name, keep in arms:
            tr_mean, ho_mean, ho_ci = summary[name]
            verdict, why = judge(tr_mean, ho_mean, ho_ci)

            long_mean, short_mean, is_drift = _drift_check(
                [r for r in holdout_rows if keep(r)], horizon
            )

            if verdict == "CONFIRMED" and is_drift:
                verdict = "DRIFT_NOT_EDGE"
                why = (
                    f"holdout longs {long_mean:+.4f}% and shorts "
                    f"{short_mean:+.4f}% have opposite signs -- the return is "
                    f"the market over the window, not the signal"
                )

            verdicts[(horizon, name)] = verdict
            print(f"  {name:<28} {verdict:<14} {why}")

        control = summary["control  (trigger kept)"]
        cycle0 = summary["cycle0   (trigger removed)"]
        delta_train = cycle0[0] - control[0]
        delta_hold = cycle0[1] - control[1]
        print(f"\n  removing the trigger moves the mean by "
              f"{delta_train:+.4f}% on train, {delta_hold:+.4f}% on holdout")
        print(f"  -> {'REMOVING IS BETTER' if delta_hold > 0 else 'KEEPING IS BETTER'} "
              f"on the holdout at this horizon\n")

    if args.record:
        count = record_comparison(
            "S08a cycle0: entry trigger removed vs kept",
            detail={
                "dataset": args.dataset,
                "rows": len(rows),
                "verdicts": {f"{h}|{n}": v for (h, n), v in verdicts.items()},
            },
        )
        print(f"recorded to the comparison ledger; {count} arms run to date")
    else:
        print("not recorded (pass --record to append to the ledger)")


if __name__ == "__main__":
    main()
