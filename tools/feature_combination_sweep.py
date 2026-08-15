"""Do features predict outcome *together*, when none predicts alone?

`tools/feature_sweep.py` tested 56 recorded features one at a time and nothing
survived a corrected threshold with its sign intact. That rules out a strong
single-feature predictor and says nothing about combinations -- a rule of the
shape "this setup, but only when volatility is high" is invisible to a
one-at-a-time sweep, and that shape is what the operator was reaching for when
they asked about sector-specific rules.

Two searches, each with one honest holdout, because the failure mode here is not
subtlety -- it is that a flexible enough search will fit 155 rows perfectly and
predict nothing.

  MODEL      L2-regularised logistic regression over every feature at once.
             The penalty is chosen by 5-fold cross-validation *inside the
             discovery half*, so the holdout is touched exactly once. Reported
             as AUC: 0.50 is a coin flip, and the gap between discovery AUC and
             holdout AUC is the overfitting made visible.

  PAIR RULE  every pair of features, split at their discovery medians into four
             quadrants; the best quadrant on discovery is then scored on the
             holdout. This is the literal form of "rule A works only when B
             holds". The number of pairs is large, so the corrected bar is
             applied and stated.

A null from both means: nothing in what the app records separates winners from
losers, alone or in pairs, at this sample size. It does not mean no such thing
exists -- 310 resolved candidates is small, and the honest response to a null is
either more sessions or a measurement the app does not currently take.

    python tools/feature_combination_sweep.py

Reads Postgres only. No bars, no quotes, no network.
"""

import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import numpy as np
from dotenv import load_dotenv

load_dotenv()

from tools.feature_sweep import load, number, usable

MIN_COVERAGE = 0.7
RIDGE_GRID = (0.01, 0.1, 1.0, 10.0, 100.0)


def build_matrix(rows):
    """Feature matrix, outcome vector, day labels. Missing values -> column mean."""

    names = []
    counts = {}

    for row in rows:
        for name, raw in (row["features"] or {}).items():
            if usable(name) and number(raw) is not None:
                counts[name] = counts.get(name, 0) + 1

    names = sorted(n for n, c in counts.items() if c >= len(rows) * MIN_COVERAGE)

    X = np.full((len(rows), len(names)), np.nan)
    y = np.zeros(len(rows))
    days = []

    for i, row in enumerate(rows):
        payload = row["features"] or {}
        for j, name in enumerate(names):
            value = number(payload.get(name))
            if value is not None:
                X[i, j] = value
        y[i] = 1.0 if row["target_first"] else 0.0
        days.append(str(row["trading_day"]))

    # Constant columns carry no information and make the fit ill-conditioned.
    keep = [j for j in range(X.shape[1]) if np.nanstd(X[:, j]) > 1e-9]
    X, names = X[:, keep], [names[j] for j in keep]

    means = np.nanmean(X, axis=0)
    X = np.where(np.isnan(X), means, X)

    return X, y, np.array(days), names


def fit(X, y, ridge, steps=3000, lr=0.15):
    """L2-regularised logistic regression by gradient descent."""

    w = np.zeros(X.shape[1] + 1)
    Xb = np.hstack([np.ones((X.shape[0], 1)), X])

    for _ in range(steps):
        p = 1.0 / (1.0 + np.exp(-np.clip(Xb @ w, -30, 30)))
        grad = Xb.T @ (p - y) / len(y)
        grad[1:] += ridge * w[1:] / len(y)
        w -= lr * grad

    return w


def predict(w, X):
    Xb = np.hstack([np.ones((X.shape[0], 1)), X])
    return 1.0 / (1.0 + np.exp(-np.clip(Xb @ w, -30, 30)))


def auc(y, scores):
    """Rank-based AUC. 0.5 is a coin flip."""

    pos, neg = scores[y == 1], scores[y == 0]

    if not len(pos) or not len(neg):
        return None

    order = np.argsort(scores)
    ranks = np.empty(len(scores))
    ranks[order] = np.arange(1, len(scores) + 1)

    return (ranks[y == 1].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def standardise(train, *others):
    mu, sd = train.mean(axis=0), train.std(axis=0)
    sd[sd < 1e-9] = 1.0
    return [(m - mu) / sd for m in (train, *others)]


def main():

    rows = load()
    X, y, days, names = build_matrix(rows)

    split = sorted(set(days))[len(set(days)) // 2]
    A, B = days < split, days >= split

    print(f"\n  resolved candidates : {len(y)}   winners {int(y.sum())}"
          f"  losers {int((1 - y).sum())}")
    print(f"  features            : {len(names)}")
    print(f"  discovery / holdout : {A.sum()} / {B.sum()}  (split at {split})")
    print(f"  base rate           : {y[A].mean():.3f} discovery, {y[B].mean():.3f} holdout\n")

    Xa, Xb = standardise(X[A], X[B])
    ya, yb = y[A], y[B]

    # ---- MODEL -------------------------------------------------------------
    # The penalty is chosen inside the discovery half only. Touching the holdout
    # to pick it would make the holdout number meaningless.
    folds = np.arange(len(ya)) % 5
    best_ridge, best_cv = None, -1.0

    for ridge in RIDGE_GRID:
        scores = []
        for f in range(5):
            tr, te = folds != f, folds == f
            if ya[te].sum() in (0, te.sum()):
                continue
            w = fit(Xa[tr], ya[tr], ridge)
            a = auc(ya[te], predict(w, Xa[te]))
            if a is not None:
                scores.append(a)
        cv = float(np.mean(scores)) if scores else -1.0
        print(f"    ridge {ridge:>7}: cross-validated AUC {cv:.3f}")
        if cv > best_cv:
            best_ridge, best_cv = ridge, cv

    w = fit(Xa, ya, best_ridge)
    auc_a, auc_b = auc(ya, predict(w, Xa)), auc(yb, predict(w, Xb))

    print(f"\n  MODEL (ridge {best_ridge})")
    print(f"    discovery AUC {auc_a:.3f}   holdout AUC {auc_b:.3f}")
    print(f"    -> {'predicts out of sample' if auc_b > 0.60 else 'no better than a coin flip out of sample'}")

    # ---- PAIR RULE ---------------------------------------------------------
    n_pairs = len(names) * (len(names) - 1) // 2
    alpha = 0.05 / max(1, n_pairs * 4)

    print(f"\n  PAIR RULE   {n_pairs} pairs x 4 quadrants,"
          f" corrected alpha {alpha:.2e}")

    med_a = np.median(X[A], axis=0)
    best = None

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            for hi_i in (True, False):
                for hi_j in (True, False):

                    sel_a = ((X[A][:, i] >= med_a[i]) == hi_i) & \
                            ((X[A][:, j] >= med_a[j]) == hi_j)

                    if sel_a.sum() < 20:
                        continue

                    rate = ya[sel_a].mean()
                    base = ya.mean()
                    n = sel_a.sum()
                    se = math.sqrt(max(base * (1 - base), 1e-9) / n)
                    z = (rate - base) / se if se > 0 else 0.0
                    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))

                    if best is None or p < best["p"]:
                        sel_b = ((X[B][:, i] >= med_a[i]) == hi_i) & \
                                ((X[B][:, j] >= med_a[j]) == hi_j)
                        best = {
                            "p": p, "z": z, "n": int(n), "rate": rate,
                            "i": names[i], "j": names[j],
                            "hi_i": hi_i, "hi_j": hi_j,
                            "hold_n": int(sel_b.sum()),
                            "hold_rate": yb[sel_b].mean() if sel_b.sum() else None,
                        }

    if best:
        print(f"    best quadrant: {best['i']} {'high' if best['hi_i'] else 'low'}"
              f"  AND  {best['j']} {'high' if best['hi_j'] else 'low'}")
        print(f"      discovery  n={best['n']:>3}  win rate {best['rate']:.3f}"
              f"  vs base {ya.mean():.3f}   z={best['z']:+.2f}  p={best['p']:.5f}")
        hold = "-" if best["hold_rate"] is None else f"{best['hold_rate']:.3f}"
        print(f"      holdout    n={best['hold_n']:>3}  win rate {hold}"
              f"  vs base {yb.mean():.3f}")
        print(f"      clears corrected bar? "
              f"{'YES' if best['p'] < alpha else 'NO'}")

    print()
    verdict_model = auc_b is not None and auc_b > 0.60
    verdict_pair = best and best["p"] < alpha

    if verdict_model or verdict_pair:
        print("  Something survived. Treat it as a candidate, not a rule, until")
        print("  it is re-tested on sessions recorded after today.\n")
    else:
        print("  Neither search found a combination that predicts out of sample.")
        print("  At 310 resolved candidates this rules out strong structure, not")
        print("  weak structure. The honest next step is a measurement the app")
        print("  does not currently take -- not another pass over these columns.\n")


if __name__ == "__main__":
    main()
