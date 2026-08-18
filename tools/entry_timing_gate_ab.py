"""Does the entry-timing ceiling help, once you price what it does *next*?

    python tools/entry_timing_gate_ab.py
    python tools/entry_timing_gate_ab.py --max-score 55 --min-rr 2.0

`ENTRY_TIMING_GATE_ENABLED` went live in Render for the first time on
2026-08-17. `ENTRY_TIMING_TOO_EARLY` appears on exactly one day in the whole
16-day archive, which is that one.

## Why the original study cannot answer this

The finding behind the gate is real and was measured on 22,954 resolved
candidates: a *low* entry-timing score wins more, and it survives splitting by
regime and by RR band. But it scored each candidate **once, where it stood**, and
concluded "prefer low-score candidates."

Enabling it as a live ceiling is a different operation. The app does not skip the
name. It re-evaluates the same name every scan and enters it later, when the
score has drifted under the bar. So the question the study never asked is:

    when the ceiling refuses a candidate, what happens to that same name later,
    and is the later entry better or worse than the one refused?

## Why the answer is not just "a worse price"

PLTR on 2026-08-17 is the worked example. Refused at 10:48 ET at 174.75 with
RR 2.06; taken at 11:22 at 175.69. Ninety-four cents. But the exit engine moves
the stop to breakeven once a trade reaches `EXIT_BREAKEVEN_TRIGGER_R` (1.0R), and
the stock's actual high was 175.93:

    from 174.74 that run is +1.37R -> breakeven arms -> exit -0.02R
    from 175.68 the same run is +0.20R -> never arms  -> exit -1.10R

Same signal, same peak, same exit minute, and the whole loss sits in the gap. A
comparison that stops at entry price misses this entirely, so this tool replays
both arms through the real stop-and-breakeven rules.

## Method

The archive predates the gate, so every recorded entry is an unfiltered one.
Entries whose timing score was **at or above** the ceiling are exactly the ones
the gate would now refuse:

    arm A (gate off)  the entry as it happened
    arm B (gate on)   the next scan of that same symbol and day that would
                      qualify with a score under the ceiling; no trade if none

Both arms are then walked forward over the archived 5-minute price series under
`EXIT_BREAKEVEN_TRIGGER_R` and the recorded stop, and scored in R.

## What this cannot see

Intrabar highs and lows -- the series is scan prices, so a stop or trigger
touched and reversed between scans is invisible. It reads the same series for
both arms, so the comparison is fair even where the level is not exact.

Option P&L. R is on the underlying. R has flattered this book before, so the
premium column is reported beside it wherever the archive carries a fill.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import statistics
import sys
from collections import defaultdict

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env", override=True)

from sqlalchemy import text  # noqa: E402

from app.db.connection import get_engine  # noqa: E402

BOOTSTRAP_SEED = 20260817
BOOTSTRAP_DRAWS = 10000

ENTRY_STATUSES = {"ENTER", "ENTER_PAPER"}


def _f(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result


def _load_archive(days):
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT trading_day, symbol, payload,
                       created_at AT TIME ZONE 'America/New_York' AS et
                FROM scanner_snapshot
                WHERE created_at > now() - make_interval(days => :days)
                ORDER BY trading_day, symbol, created_at
                """
            ),
            {"days": days},
        ).mappings().all()

    series = defaultdict(list)

    for row in rows:
        payload = row["payload"]

        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue

        if isinstance(payload, dict):
            series[(row["trading_day"], row["symbol"])].append((row["et"], payload))

    return series


def _walk(series, start_index, entry, stop, direction, trigger_r):
    """Replay one entry forward under the recorded stop and the breakeven move.

    `EXIT_BREAKEVEN_ON_PEAK` is off by default, so the trigger is judged on the
    scan's own price rather than the running peak -- a trade that touches 1R
    between scans and closes back under it does not move its stop. Modelled the
    way it runs, not the way it reads.
    """

    risk = abs(entry - stop)

    if risk <= 0:
        return None

    is_short = str(direction).upper() == "PUT"
    current_stop = stop
    armed = False
    peak_r = 0.0

    for _et, payload in series[start_index:]:
        price = _f(payload.get("Price"))

        if price is None:
            continue

        progress = (entry - price) / risk if is_short else (price - entry) / risk
        peak_r = max(peak_r, progress)

        if not armed and trigger_r > 0 and progress >= trigger_r:
            armed = True
            current_stop = entry

        hit = price >= current_stop if is_short else price <= current_stop

        if hit:
            realised = (entry - price) / risk if is_short else (price - entry) / risk
            return {"r": realised, "peak_r": peak_r, "armed": armed, "exit": "STOP"}

    # Ran to the end of the day's archive without stopping. Scored at the last
    # print, which is what a forced close at the bell would have taken.
    last = None

    for _et, payload in reversed(series[start_index:]):
        last = _f(payload.get("Price"))
        if last is not None:
            break

    if last is None:
        return None

    realised = (entry - last) / risk if is_short else (last - entry) / risk
    return {"r": realised, "peak_r": peak_r, "armed": armed, "exit": "EOD"}


def _bootstrap_ci(values, draws=BOOTSTRAP_DRAWS):
    if len(values) < 2:
        return None, None

    rng = random.Random(BOOTSTRAP_SEED)
    means = []

    for _ in range(draws):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(statistics.mean(sample))

    means.sort()
    return means[int(0.025 * draws)], means[int(0.975 * draws)]


def _summarise(name, values):
    if not values:
        print(f"  {name}: no trades")
        return

    mean = statistics.mean(values)
    low, high = _bootstrap_ci(values)
    without_top5 = sorted(values)[: max(0, len(values) - 5)]
    wins = sum(1 for v in values if v > 0)

    print(f"  {name}:")
    print(f"    n {len(values)}   total {sum(values):+.2f}R   mean {mean:+.3f}R")

    if low is not None:
        print(f"    95% CI [{low:+.3f}, {high:+.3f}]")

    if without_top5:
        print(f"    mean without best 5 {statistics.mean(without_top5):+.3f}R")

    print(f"    win rate {wins / len(values) * 100:.0f}%")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--max-score", type=float, default=55.0,
                        help="ENTRY_TIMING_MAX_SCORE ceiling under test")
    parser.add_argument("--min-rr", type=float, default=2.0)
    parser.add_argument("--trigger-r", type=float, default=1.0,
                        help="EXIT_BREAKEVEN_TRIGGER_R")
    args = parser.parse_args()

    series = _load_archive(args.days)
    print(f"symbol-days in archive: {len(series)}")

    arm_a, arm_b = [], []
    refused_no_reentry = 0
    paired = []

    for key, rows in series.items():
        for index, (_et, payload) in enumerate(rows):

            action = str(payload.get("Action Status") or "").upper()

            if action not in ENTRY_STATUSES:
                continue

            score = _f(payload.get("Entry Timing Score"))
            entry = _f(payload.get("Candidate Entry Price"))
            stop = _f(payload.get("Candidate Stop Price"))
            direction = payload.get("Candidate Direction")

            if None in (score, entry, stop):
                continue

            # Only entries the gate would newly refuse are informative. One below
            # the ceiling is taken by both arms and cancels out of the comparison.
            if score < args.max_score:
                continue

            taken = _walk(rows, index, entry, stop, direction, args.trigger_r)

            if taken is None:
                continue

            # Arm B: the next scan of this same name that clears the ceiling.
            replacement = None

            for later, (_lt, later_payload) in enumerate(rows[index + 1:], start=index + 1):
                later_score = _f(later_payload.get("Entry Timing Score"))
                later_rr = _f(later_payload.get("Risk Reward"))
                later_entry = _f(later_payload.get("Candidate Entry Price"))
                later_stop = _f(later_payload.get("Candidate Stop Price"))

                if None in (later_score, later_rr, later_entry, later_stop):
                    continue

                if later_score < args.max_score and later_rr >= args.min_rr:
                    replacement = _walk(
                        rows, later, later_entry, later_stop,
                        later_payload.get("Candidate Direction"), args.trigger_r,
                    )
                    break

            arm_a.append(taken["r"])

            if replacement is None:
                refused_no_reentry += 1
            else:
                arm_b.append(replacement["r"])
                paired.append((key, taken, replacement))

            break  # one trade per symbol-day, as the book runs

    print(f"\nentries the ceiling would refuse: {len(arm_a)}")
    print(f"  of those, no qualifying re-entry the same day: {refused_no_reentry}")
    print(f"  re-entered later at a lower score: {len(arm_b)}")

    print("\nARM A -- gate OFF (the entry as it happened)")
    _summarise("all refused-by-gate entries", arm_a)

    print("\nARM B -- gate ON (the later entry it would take instead)")
    _summarise("later re-entries only", arm_b)

    if paired:
        matched_a = [t["r"] for _k, t, _r in paired]
        matched_b = [r["r"] for _k, _t, r in paired]

        print("\nMATCHED PAIRS ONLY -- same symbol-day in both arms")
        _summarise("A (as taken)", matched_a)
        _summarise("B (gate's later entry)", matched_b)

        deltas = [b - a for a, b in zip(matched_a, matched_b)]
        low, high = _bootstrap_ci(deltas)
        print(f"\n  delta (B - A): mean {statistics.mean(deltas):+.3f}R", end="")

        if low is not None:
            print(f"   95% CI [{low:+.3f}, {high:+.3f}]", end="")

        print()
        print(f"  gate made it worse on {sum(1 for d in deltas if d < 0)}/{len(deltas)}")

        armed_a = sum(1 for _k, t, _r in paired if t["armed"])
        armed_b = sum(1 for _k, _t, r in paired if r["armed"])
        print(f"\n  breakeven armed -- A {armed_a}/{len(paired)}   B {armed_b}/{len(paired)}")
        print("  (this is the channel PLTR 2026-08-17 failed through: the later")
        print("   entry sits too high for the same move to reach the trigger)")

    print("\nVERDICT INPUTS")
    print("  Judge B against A, not against zero. If the CI on the delta spans")
    print("  zero the gate has not earned a live slot, and the default is off,")
    print("  which is what the code ships.")


if __name__ == "__main__":
    main()
