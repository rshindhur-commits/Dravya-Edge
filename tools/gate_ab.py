"""Do the proposed contract gates survive sessions they were not fitted to?

Three changes came out of grouping the 291-trade forward archive by each lever.
Two of them can be tested here and one cannot:

    OPTION_MIN_LEVERAGE        testable -- entry_price / option_entry_fill
    OPTION_MIN_CONTRACT_COST   testable -- option_entry_fill * 100
    MIN_STOP_SPREAD_MULTIPLE   NOT testable -- no forward run records the
                               option spread or delta, and the multiple needs
                               both. It stays a proposal until a run carries them.

The thresholds were read off the whole archive, which is the same data any
verdict would come from, so this re-derives them on the training sessions alone
and then applies the training answer -- whatever it turns out to be -- to the
holdout. A filter that only helps on the half it was fitted to has told us
nothing, and that is the outcome this is built to be able to report.

    python tools/gate_ab.py data/forward_runs/phase1_21day_20260803_202603.json
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

from app.research.holdout import load_split  # noqa: E402

BOOTSTRAP_SEED = 20260810
BOOTSTRAP_DRAWS = 10000

LEVERAGE_GRID = [0, 10, 15, 20, 25, 30, 40]
COST_GRID = [0, 100, 200, 300, 400, 500]


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load(path):
    trades = json.loads(pathlib.Path(path).read_text())["trades"]
    rows = []

    for t in trades:
        entry = _f(t.get("entry_price"))
        fill = _f(t.get("option_entry_fill"))
        exit_fill = _f(t.get("option_exit_fill"))

        if not entry or not fill or exit_fill is None or fill <= 0:
            continue

        rows.append({
            "day": str(t.get("entry_time"))[:10],
            "premium": (exit_fill - fill) / fill * 100.0,
            "r": _f(t.get("r_multiple")),
            "leverage": entry / fill,
            "cost": fill * 100.0,
        })

    return rows


def _stats(rows):
    if not rows:
        return {"n": 0, "mean": None, "median": None, "total": None, "ci": (None, None)}

    premium = [r["premium"] for r in rows]
    rng = random.Random(BOOTSTRAP_SEED)
    means = sorted(
        statistics.fmean(rng.choices(premium, k=len(premium)))
        for _ in range(BOOTSTRAP_DRAWS)
    )

    return {
        "n": len(premium),
        "mean": statistics.fmean(premium),
        "median": statistics.median(premium),
        "total": sum(premium),
        "ci": (means[int(BOOTSTRAP_DRAWS * 0.025)], means[int(BOOTSTRAP_DRAWS * 0.975)]),
    }


def _show(label, s):
    if not s["n"]:
        print(f"  {label:<26} n=0")
        return
    lo, hi = s["ci"]
    print(f"  {label:<26} n={s['n']:<5} mean {s['mean']:+7.2f}%  "
          f"median {s['median']:+7.2f}%  total {s['total']:+9.1f}%  "
          f"95% CI [{lo:+.2f}, {hi:+.2f}]")


def sweep(train, holdout, field, grid, name, env_name):
    print(f"\n===== {name} =====")

    base_train = _stats(train)
    base_hold = _stats(holdout)

    print("  -- threshold swept on TRAIN only --")
    best, best_mean = None, None

    for threshold in grid:
        kept = [r for r in train if r[field] >= threshold]
        s = _stats(kept)

        if not s["n"]:
            continue

        marker = ""
        if best_mean is None or s["mean"] > best_mean:
            best, best_mean, marker = threshold, s["mean"], "  <-- best on train"

        print(f"    {env_name} >= {threshold:<5} n={s['n']:<5} "
              f"mean {s['mean']:+7.2f}%  total {s['total']:+8.1f}%{marker}")

    print(f"\n  -- the train answer ({env_name} >= {best}) applied to HOLDOUT --")
    _show("holdout, no filter", base_hold)
    _show(f"holdout, {env_name} >= {best}", _stats([r for r in holdout if r[field] >= best]))

    kept_rows = [r for r in holdout if r[field] >= best]
    cut_rows = [r for r in holdout if r[field] < best]
    kept_hold = _stats(kept_rows)

    if not kept_hold["n"] or not cut_rows or base_hold["mean"] is None:
        print("\n  VERDICT: not enough holdout trades to say.")
        return

    delta = kept_hold["mean"] - base_hold["mean"]
    cut_mean = statistics.fmean(r["premium"] for r in cut_rows)

    print(f"\n  removed {len(cut_rows)} of {base_hold['n']} trades, "
          f"whose own mean was {cut_mean:+.2f}% against {base_hold['mean']:+.2f}% overall")
    print(f"  moves the holdout mean by {delta:+.2f}% per trade")

    # The improvement has to be resampled with the filter applied inside each
    # draw. Comparing two independent intervals asks a different and much
    # weaker question, and a total that "improves" proves nothing at all when
    # the mean is negative: dropping any trade at all improves it.
    rng = random.Random(BOOTSTRAP_SEED)
    deltas = []

    for _ in range(BOOTSTRAP_DRAWS):
        draw = rng.choices(holdout, k=len(holdout))
        kept = [r["premium"] for r in draw if r[field] >= best]

        if not kept:
            continue

        deltas.append(statistics.fmean(kept) - statistics.fmean(r["premium"] for r in draw))

    deltas.sort()
    lo = deltas[int(len(deltas) * 0.025)]
    hi = deltas[int(len(deltas) * 0.975)]
    print(f"  95% CI on that improvement: [{lo:+.2f}, {hi:+.2f}]")

    if lo > 0:
        verdict = "CONFIRMED -- improvement holds out and its interval excludes zero"
    elif delta > 0:
        verdict = (f"NOT DISTINGUISHABLE -- mean improves by {delta:+.2f}% but the "
                   f"interval spans zero; this is the shape of noise")
    else:
        verdict = "REJECTED -- did not survive the holdout"

    print(f"  VERDICT: {verdict}")

    if kept_hold["mean"] is not None and kept_hold["mean"] < 0:
        print(f"  NOTE: the filtered book still loses {kept_hold['mean']:+.2f}% per "
              f"trade. This is a smaller loss, not a profit.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset")
    args = parser.parse_args()

    rows = _load(args.dataset)
    split = load_split()

    if split is None:
        raise SystemExit("No fixed split; run the S05 framework first.")

    train = [r for r in rows if r["day"] in set(split["train"])]
    holdout = [r for r in rows if r["day"] in set(split["holdout"])]

    print(f"{args.dataset}")
    print(f"usable trades {len(rows)}  ->  train {len(train)} / holdout {len(holdout)}")
    print("metric: option premium %, which is the cash. R is reported nowhere here "
          "because it is the term that flatters this book.")

    sweep(train, holdout, "leverage", LEVERAGE_GRID, "Leverage floor", "OPTION_MIN_LEVERAGE")
    sweep(train, holdout, "cost", COST_GRID, "Contract cost floor", "OPTION_MIN_CONTRACT_COST")

    print("\n===== MIN_STOP_SPREAD_MULTIPLE =====")
    print("  Not testable here. No forward run records option spread or delta,")
    print("  and the multiple is (option move at stop / round-trip spread).")
    print("  It remains a proposal derived from arithmetic, not a measured result.")


if __name__ == "__main__":
    main()
