"""The out-of-sample discipline, as code rather than as a habit.

Every holdout in this project so far has been applied by hand, in conversation.
It worked -- it killed three findings that looked real -- but a habit does not
survive a context window, and roughly ten arms have now been run against the
same 21 sessions. Without an enforced split and a count of how many things have
been tried, the eleventh finding is uninterpretable no matter how good it looks.

Three things are enforced here:

1. **The split is fixed and stored.** Written once to research/splits.json and
   read thereafter. A split recomputed per run can drift as sessions accumulate,
   and a split chosen after seeing the result is not a split.

2. **Comparisons are counted.** Every arm evaluated appends to a ledger. At ten
   comparisons the best of ten looks about 1.8 standard errors better than
   average by chance alone; the reader is told the count so a t of 2 is read
   against the right yardstick.

3. **A result is not a finding until the holdout agrees.** ``judge`` returns
   CONFIRMED only when train and holdout point the same way *and* the holdout
   clears the bar on its own. Everything else is TRAIN_ONLY or REJECTED, and the
   words are deliberately unequal.

The split is by *session*, never by trade. Trades inside one session share a
market, and splitting them would leak the answer across the boundary.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

RESEARCH_DIR = pathlib.Path(__file__).resolve().parents[2] / "research"
SPLIT_FILE = RESEARCH_DIR / "splits.json"
LEDGER_FILE = RESEARCH_DIR / "comparisons.jsonl"

# Fraction of sessions held out. A third of 21 sessions is 7, which at ~35
# trades a session is enough to see a break-even edge (SE ~0.06% against a
# +0.155% bar) without starving the training half.
DEFAULT_HOLDOUT_FRACTION = 1.0 / 3.0

# The bar every arm is judged against: captured underlying move per trade needed
# to break even at OPTION_MAX_SPREAD_PCT=2, on an intraday hold. Stated here so
# an arm cannot quietly be judged against a friendlier number.
BREAK_EVEN_CAPTURED_PCT = 0.155


def _now():

    return datetime.now(timezone.utc).isoformat()


def load_split():
    """The stored split, or None if none has been fixed yet."""

    if not SPLIT_FILE.exists():

        return None

    return json.loads(SPLIT_FILE.read_text())


def fix_split(sessions, holdout_fraction=DEFAULT_HOLDOUT_FRACTION, force=False):
    """Fix the train/holdout split once, and refuse to move it thereafter.

    The holdout is the *most recent* sessions rather than a random sample. A
    random split lets a strategy be tuned on sessions that sit between holdout
    sessions, which is a mild form of trading with tomorrow's newspaper; taking
    the tail answers the question actually being asked, which is whether the
    thing works on days it has never seen.

    New sessions accumulate into the holdout rather than being reshuffled in,
    so re-running with a longer session list extends the holdout and leaves the
    training half exactly as it was.
    """

    existing = load_split()

    if existing is not None and not force:

        known = set(existing["train"]) | set(existing["holdout"])
        fresh = [s for s in sorted(sessions) if s not in known]

        if fresh:

            existing["holdout"] = sorted(set(existing["holdout"]) | set(fresh))
            existing["extended_at"] = _now()
            _write_split(existing)

        return existing

    ordered = sorted(sessions)
    cut = len(ordered) - max(1, int(round(len(ordered) * holdout_fraction)))

    split = {
        "fixed_at": _now(),
        "holdout_fraction": holdout_fraction,
        "train": ordered[:cut],
        "holdout": ordered[cut:],
        "note": (
            "Holdout is the most recent sessions. New sessions extend the "
            "holdout; the train half never changes."
        ),
    }

    _write_split(split)

    return split


def _write_split(split):

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    SPLIT_FILE.write_text(json.dumps(split, indent=2))


def partition(rows, session_key="day"):
    """Split rows into (train, holdout) by their session."""

    split = load_split()

    if split is None:

        raise RuntimeError(
            "No split has been fixed. Call fix_split(sessions) once, before "
            "evaluating anything, so it cannot be chosen after seeing a result."
        )

    train_days = set(split["train"])
    holdout_days = set(split["holdout"])

    train = [r for r in rows if str(r.get(session_key)) in train_days]
    holdout = [r for r in rows if str(r.get(session_key)) in holdout_days]

    return train, holdout


def record_comparison(name, detail=None):
    """Append an arm to the ledger and return how many have now been run."""

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    entry = {"at": _now(), "name": name, "detail": detail or {}}

    with LEDGER_FILE.open("a", encoding="utf-8") as handle:

        handle.write(json.dumps(entry) + "\n")

    return comparison_count()


def comparison_count():

    if not LEDGER_FILE.exists():

        return 0

    return sum(
        1 for line in LEDGER_FILE.read_text(encoding="utf-8").splitlines() if line.strip()
    )


def judge(train_mean, holdout_mean, holdout_ci, bar=BREAK_EVEN_CAPTURED_PCT):
    """CONFIRMED / TRAIN_ONLY / REJECTED, and why.

    ``holdout_ci`` is the (low, high) bootstrap interval on the holdout mean.
    Confirmation needs three things and not two: the training half found
    something, the holdout agrees in direction, and the holdout clears the bar
    with an interval that excludes zero. Dropping the last of those is how a
    result that is merely *not refuted* gets reported as a result.
    """

    low, _high = holdout_ci

    if train_mean < bar:

        return "REJECTED", (
            f"train {train_mean:+.4f}% did not reach the {bar:+.3f}% bar"
        )

    if holdout_mean < bar:

        return "TRAIN_ONLY", (
            f"train {train_mean:+.4f}% reached the bar, holdout "
            f"{holdout_mean:+.4f}% did not -- this is the shape of an overfit"
        )

    if low <= 0:

        return "TRAIN_ONLY", (
            f"holdout {holdout_mean:+.4f}% cleared the bar but its interval "
            f"includes zero (low {low:+.4f}%) -- not distinguishable from nothing"
        )

    return "CONFIRMED", (
        f"train {train_mean:+.4f}% and holdout {holdout_mean:+.4f}% both clear "
        f"{bar:+.3f}%, holdout interval excludes zero"
    )
