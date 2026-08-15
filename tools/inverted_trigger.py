"""If the generator picks the wrong moment, does the opposite moment pay?

The archive says entry timing is **worse than a random minute** by 8-20 standard
deviations, in both holdout halves. A rule that is merely uninformative scores
zero against random; one that scores reliably *below* it is carrying information
and applying it backwards.

That distinction decides how the generator gets rebuilt. If inverting the
direction pays, the setups are finding real moments and reading them upside down,
and the fix is a sign. If inverting pays nothing, they are finding nothing, and
the fix is a different generator entirely.

Cheap, because it needs no new candidates. Every resolved candidate already
carries its entry, stop and target; the inverted trade is the same entry with the
geometry mirrored -- a long's stop below becomes a short's stop above, at the
same distance -- walked forward on the same bars.

Scored the same way the rest of the project scores a counterfactual: whichever
level is touched first ends the trade, and a bar touching both counts as the
stop, because intrabar order is unknowable at this resolution and resolving it
favourably manufactures the edge being measured.

    python tools/inverted_trigger.py

Cached bars only. No option quotes, no network beyond the bar cache.
"""

import math
import pathlib
import random
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.backtesting.historical_market_data import fetch_bars
from app.db.connection import get_engine

_bars = {}


def bars(symbol, day):
    key = (symbol, day)
    if key not in _bars:
        try:
            frame = fetch_bars(symbol, day, day)
            frame.index = frame.index.tz_convert("America/New_York")
            _bars[key] = frame.between_time("09:30", "16:00")
        except Exception:
            _bars[key] = None
    return _bars[key]


def number(value):
    try:
        result = float(value)
        return None if result != result else result
    except (TypeError, ValueError):
        return None


def walk(frame, entry, stop, target, is_short):
    """R for a trade taken at `entry`, walked bar by bar. None if unresolvable."""

    risk = abs(entry - stop)
    if risk <= 0 or not len(frame):
        return None

    for _ts, bar in frame.iterrows():

        hit_stop = bar["High"] >= stop if is_short else bar["Low"] <= stop
        hit_target = bar["Low"] <= target if is_short else bar["High"] >= target

        # The stop wins an ambiguous bar, in both arms equally.
        if hit_stop:
            return -1.0
        if hit_target:
            return abs(target - entry) / risk

    close = float(frame["Close"].iloc[-1])
    return ((entry - close) if is_short else (close - entry)) / risk


def load():

    with get_engine().begin() as connection:

        return connection.execute(text("""
            SELECT DISTINCT ON (s.symbol, s.trading_day, s.scan_timestamp)
                   s.symbol, s.trading_day, s.scan_timestamp,
                   s.decision_payload AS p
            FROM scanner_snapshot s
            WHERE s.decision_payload->>'Candidate Entry Price' IS NOT NULL
              AND s.decision_payload->>'Candidate Stop Price' IS NOT NULL
              AND s.decision_payload->>'Candidate Target Price' IS NOT NULL
              AND s.decision_payload->>'Candidate Direction' IS NOT NULL
            ORDER BY s.symbol, s.trading_day, s.scan_timestamp
        """)).mappings().all()


def main():

    random.seed(11)
    rows = load()
    print(f"\n  candidates with full geometry: {len(rows)}")

    taken, inverted, by_setup = [], [], defaultdict(lambda: [[], []])
    days = []

    for row in rows:

        p = row["p"] or {}
        entry = number(p.get("Candidate Entry Price"))
        stop = number(p.get("Candidate Stop Price"))
        target = number(p.get("Candidate Target Price"))
        direction = str(p.get("Candidate Direction") or "").upper()

        if None in (entry, stop, target) or direction not in {"CALL", "PUT"}:
            continue

        day = str(row["trading_day"])
        frame = bars(row["symbol"], day)
        if frame is None or not len(frame):
            continue

        try:
            at = pd.Timestamp(row["scan_timestamp"])
            at = at.tz_localize("America/New_York") if at.tzinfo is None else at.tz_convert("America/New_York")
        except Exception:
            continue

        forward = frame[frame.index > at]
        if len(forward) < 5:
            continue

        is_short = direction == "PUT"
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk <= 0 or reward <= 0:
            continue

        r_taken = walk(forward, entry, stop, target, is_short)

        # The mirror: same entry, same distances, opposite side.
        inv_stop = entry + risk if is_short else entry - risk
        inv_target = entry - reward if is_short else entry + reward
        r_inv = walk(forward, entry, inv_stop, inv_target, not is_short)

        if r_taken is None or r_inv is None:
            continue

        taken.append(r_taken)
        inverted.append(r_inv)
        days.append(day)
        setup = str(p.get("Entry") or p.get("setup") or "UNKNOWN")
        by_setup[setup][0].append(r_taken)
        by_setup[setup][1].append(r_inv)

    if len(taken) < 40:
        print(f"  only {len(taken)} usable; stopping.\n")
        return

    def line(label, values):
        wins = sum(1 for v in values if v > 0)
        return (f"  {label:<22}{len(values):>5}{st.mean(values):>+10.3f}"
                f"{st.median(values):>+10.3f}{wins / len(values) * 100:>8.0f}%")

    print(f"  usable                       : {len(taken)}")
    print(f"  sessions                     : {len(set(days))}\n")
    print(f"  {'arm':<22}{'n':>5}{'mean R':>10}{'median':>10}{'win%':>9}")
    print(f"  {'-' * 56}")
    print(line("as generated", taken))
    print(line("inverted", inverted))

    diff = [i - t for i, t in zip(inverted, taken)]
    boot = []
    for _ in range(4000):
        sample = [random.choice(diff) for _ in diff]
        boot.append(sum(sample) / len(sample))
    boot.sort()

    print(f"\n  inverted minus generated: {st.mean(diff):+.3f}R"
          f"   95% CI [{boot[100]:+.3f}, {boot[3900]:+.3f}]")

    # Holdout, because a difference that only exists in one half is not one.
    ordered = sorted(set(days))
    split = ordered[len(ordered) // 2]
    for label, keep in (("discovery", lambda d: d < split), ("holdout", lambda d: d >= split)):
        sel = [i for i, d in enumerate(days) if keep(d)]
        if len(sel) > 20:
            dt = [diff[i] for i in sel]
            print(f"    {label:<10} n={len(sel):>4}  {st.mean(dt):+.3f}R")

    print(f"\n  {'setup':<22}{'n':>5}{'generated':>12}{'inverted':>11}")
    print(f"  {'-' * 50}")
    for setup, (t, i) in sorted(by_setup.items(), key=lambda kv: -len(kv[1][0])):
        if len(t) >= 15:
            print(f"  {setup[:22]:<22}{len(t):>5}{st.mean(t):>+12.3f}{st.mean(i):>+11.3f}")

    excludes_zero = boot[100] > 0 or boot[3900] < 0
    print()
    if excludes_zero and st.mean(diff) > 0:
        print("  Inverting pays, and the interval excludes zero. The setups are")
        print("  finding real moments and reading them backwards -- the generator")
        print("  needs its sign changed, not its logic replaced.\n")
    elif excludes_zero:
        print("  Inverting is reliably WORSE. The generator is right-signed; what")
        print("  it lacks is edge, not direction.\n")
    else:
        print("  Inverting changes nothing beyond noise. The setups carry no")
        print("  directional information at this horizon, so flipping the sign is")
        print("  not the fix and the generator has to be replaced rather than")
        print("  corrected.\n")


if __name__ == "__main__":
    main()
