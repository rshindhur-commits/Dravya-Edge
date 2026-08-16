r"""A/B the relative-strength arm: sector benchmark against the hardcoded zero.

    python tools/relative_strength_ab.py
    python tools/relative_strength_ab.py --bars 4 --min-day-rows 50

What is being tested
--------------------
`momentum_strategy.analyze_setup` scores a relative-strength arm worth +1, 0 or
-1 and adds it to the composite setup score. Until 2026-08-16 it compared the
symbol's session move against a hardcoded `benchmark_move = 0`, so it scored
"is this name up more than 0.5% today". `RELATIVE_STRENGTH_BENCHMARK_ENABLED`
switches it to compare against the symbol's sector reference instead.

    OLD   +1 if move > +0.5      -1 if move < -0.5
    NEW   +1 if move > ref+0.5   -1 if move < ref-0.5

Both numbers are archived per snapshot -- `Symbol Move %` and `Sector Reference
Move %` -- so neither arm needs refetching and neither is reconstructed.

Why this is not a full pipeline replay
--------------------------------------
The arm contributes to a *score*, and the score's job is to pick direction. So
this measures the arm directly: group snapshots by what each arm scored, and
compare the underlying's forward return across those groups. An input that helps
direction must show forward return rising with its own value. One that does not
cannot help the score no matter what the rest of the pipeline does with it.

That is a narrower claim than "the switch makes money" and it is the claim the
data can support. A full replay still has to run before the switch is enabled.

Lookahead
---------
Features come from the snapshot taken at time T. The forward return is read from
the day's complete 15m series, which is assembled from the *fullest* snapshot of
that symbol-day and then indexed strictly after T. No row uses its own future to
compute its own feature.

Read-only. Touches the database and nothing else.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app.db.connection import get_engine  # noqa: E402

BAND = 0.5


def arm_score(move, benchmark):
    """The ±1 the relative-strength block contributes. Mirrors analyze_setup."""

    if move is None:
        return 0

    base = 0.0 if benchmark is None else float(benchmark)

    if move > base + BAND:
        return 1

    if move < base - BAND:
        return -1

    return 0


def _series(engine, days):
    """The fullest 15m bar series per symbol-day, as (epoch, close) pairs."""

    rows = engine.execute(text("""
        SELECT DISTINCT ON (trading_day, symbol)
               trading_day::text, symbol, market_payload->'bars_15m'
        FROM scanner_snapshot
        WHERE market_payload IS NOT NULL
          AND trading_day::text = ANY(:days)
        ORDER BY trading_day, symbol,
                 jsonb_array_length(market_payload->'bars_15m') DESC
    """), {"days": days}).fetchall()

    out = {}

    for day, symbol, bars in rows:
        if isinstance(bars, str):
            bars = json.loads(bars)

        if not bars:
            continue

        import pandas as pd

        parsed = []
        for bar in bars:
            stamp = pd.Timestamp(bar["Datetime"])
            parsed.append((stamp.value, float(bar["Close"])))

        out[(day, symbol)] = sorted(parsed)

    return out


def _forward(series, at_ns, bars):
    """Return over `bars` 15m bars after `at_ns`, or to the close if shorter."""

    future = [(ts, close) for ts, close in series if ts > at_ns]

    if len(future) < 2:
        return None

    start = future[0][1]
    end = future[min(bars, len(future) - 1)][1]

    if not start:
        return None

    return (end - start) / start * 100.0


def _mean(values):
    return statistics.fmean(values) if values else float("nan")


def _bootstrap_by_day(rows, arm_key, draws=2000, seed=7):
    """CI on the arm's spread, resampled by DAY rather than by row.

    Rows inside a session are not independent -- one strong afternoon moves
    every symbol at once -- so a row-level CI would be far too tight.
    """

    by_day = defaultdict(list)
    for row in rows:
        by_day[row["day"]].append(row)

    days = list(by_day)
    if len(days) < 3:
        return None

    rng = random.Random(seed)
    spreads = []

    for _ in range(draws):
        sample = []
        for _ in range(len(days)):
            sample.extend(by_day[rng.choice(days)])

        up = [r["fwd"] for r in sample if r[arm_key] == 1]
        down = [r["fwd"] for r in sample if r[arm_key] == -1]

        if up and down:
            spreads.append(_mean(up) - _mean(down))

    if len(spreads) < draws * 0.5:
        return None

    spreads.sort()

    return spreads[int(len(spreads) * 0.025)], spreads[int(len(spreads) * 0.975)]


def _report(name, rows, arm_key):
    groups = {v: [r["fwd"] for r in rows if r[arm_key] == v] for v in (1, 0, -1)}

    print(f"\n  {name}")
    print(f"    {'arm':>6}{'n':>8}{'mean fwd %':>14}{'median':>10}")

    for value in (1, 0, -1):
        block = groups[value]
        median = statistics.median(block) if block else float("nan")
        print(f"    {value:>+6}{len(block):>8}{_mean(block):>14.4f}{median:>10.4f}")

    up, down = groups[1], groups[-1]

    if not up or not down:
        print("    spread: not computable (an arm never fired)")
        return None

    spread = _mean(up) - _mean(down)
    print(f"    spread (+1 minus -1): {spread:+.4f} pp")

    stripped = sorted(up)[:-5] if len(up) > 5 else up
    stripped_down = sorted(down, reverse=True)[:-5] if len(down) > 5 else down
    print(f"    without the 5 best longs / 5 worst shorts: "
          f"{_mean(stripped) - _mean(stripped_down):+.4f} pp")

    ci = _bootstrap_by_day(rows, arm_key)
    if ci:
        print(f"    95% CI on the spread, resampled by day: "
              f"[{ci[0]:+.4f}, {ci[1]:+.4f}]")

    return spread


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", type=int, default=4,
                        help="forward horizon in 15m bars (default 4 = 1 hour)")
    parser.add_argument("--min-day-rows", type=int, default=50,
                        help="skip sessions with fewer usable rows than this")
    args = parser.parse_args()

    engine = get_engine().connect()

    days = [d for (d,) in engine.execute(text("""
        SELECT DISTINCT trading_day::text FROM scanner_snapshot
        WHERE market_payload IS NOT NULL ORDER BY 1
    """)).fetchall()]

    print(f"  sessions with archived bars: {len(days)}  ({days[0]} .. {days[-1]})")

    series = _series(engine, days)
    print(f"  symbol-day bar series assembled: {len(series)}")

    raw = engine.execute(text("""
        SELECT trading_day::text, symbol,
               EXTRACT(EPOCH FROM scan_timestamp) * 1e9,
               decision_payload->>'Symbol Move %',
               decision_payload->>'Sector Reference Move %'
        FROM scanner_snapshot
        WHERE decision_payload IS NOT NULL
          AND decision_payload->>'Symbol Move %' IS NOT NULL
          AND decision_payload->>'Sector Reference Move %' IS NOT NULL
        ORDER BY trading_day, symbol, scan_timestamp
    """)).fetchall()

    print(f"  snapshots carrying both moves: {len(raw)}")

    rows, dropped = [], 0

    for day, symbol, at_ns, move, reference in raw:
        key = (day, symbol)

        if key not in series:
            dropped += 1
            continue

        try:
            move = float(move)
            reference = float(reference)
        except (TypeError, ValueError):
            dropped += 1
            continue

        forward = _forward(series[key], int(at_ns), args.bars)

        if forward is None or math.isnan(forward):
            dropped += 1
            continue

        rows.append({
            "day": day,
            "symbol": symbol,
            "fwd": forward,
            "old": arm_score(move, None),
            "new": arm_score(move, reference),
        })

    counts = defaultdict(int)
    for row in rows:
        counts[row["day"]] += 1

    usable = {d for d, n in counts.items() if n >= args.min_day_rows}
    rows = [r for r in rows if r["day"] in usable]

    print(f"  usable rows: {len(rows)} across {len(usable)} sessions "
          f"({dropped} dropped, no forward bar or unparseable)")

    if not rows:
        print("\n  nothing to measure")
        return

    disagree = [r for r in rows if r["old"] != r["new"]]
    print(f"  the two arms disagree on {len(disagree)} of {len(rows)} "
          f"({len(disagree) / len(rows):.1%})")

    print(f"\n  Forward return of the underlying, {args.bars} bars "
          f"({args.bars * 15} minutes) after the snapshot")

    old_spread = _report("OLD  — benchmark hardcoded to 0", rows, "old")
    new_spread = _report("NEW  — benchmark is the sector reference", rows, "new")

    if disagree:
        _report(f"NEW, on the {len(disagree)} rows where they disagree",
                disagree, "new")
        _report(f"OLD, on those same {len(disagree)} rows", disagree, "old")

    print("\n  " + "=" * 66)

    if old_spread is None or new_spread is None:
        print("  inconclusive: an arm never fired in both directions")
        return

    print(f"  OLD spread {old_spread:+.4f} pp   NEW spread {new_spread:+.4f} pp"
          f"   delta {new_spread - old_spread:+.4f} pp")

    print("\n  Per session, to check the result is not one day:")
    print(f"    {'day':>12}{'n':>7}{'OLD':>10}{'NEW':>10}{'winner':>9}")

    wins = 0
    sessions = 0

    for day in sorted(usable):
        block = [r for r in rows if r["day"] == day]

        def spread_of(key):
            up = [r["fwd"] for r in block if r[key] == 1]
            down = [r["fwd"] for r in block if r[key] == -1]
            return (_mean(up) - _mean(down)) if up and down else None

        old_day, new_day = spread_of("old"), spread_of("new")

        if old_day is None or new_day is None:
            print(f"    {day:>12}{len(block):>7}{'--':>10}{'--':>10}{'':>9}")
            continue

        sessions += 1
        better = new_day > old_day
        wins += better
        print(f"    {day:>12}{len(block):>7}{old_day:>10.3f}{new_day:>10.3f}"
              f"{('NEW' if better else 'OLD'):>9}")

    if sessions:
        print(f"\n  NEW wins {wins} of {sessions} sessions")


if __name__ == "__main__":
    main()
