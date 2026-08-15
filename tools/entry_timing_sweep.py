"""How much is the entry moment costing, and does waiting recover it?

The archive measured this app's entry timing as **worse than a random minute** by
8-20 standard deviations, in both holdout halves. `tools/inverted_trigger.py`
then showed the direction is firmly right -- inverting it produces a 1% win rate
-- and that the signal carries a real +0.134R against a +0.40R break-even for the
option round trip.

Those two facts together say the app finds the correct move and enters it at the
wrong second. This measures how wrong, and whether the wrongness is recoverable
by the cheapest possible change: waiting.

For every candidate, the same trade -- same direction, same risk, same reward
distance -- entered at the signal, and again after a delay, with the geometry
re-anchored to the delayed price so risk stays constant and R stays comparable.
A trade whose entry never becomes reachable in the window is dropped from both
arms equally.

Two families:

  DELAY n     enter at the price n minutes after the signal, whatever it is.
              Costs nothing to implement -- one sleep -- so if it works it is
              the cheapest fix available.

  PULLBACK n  enter only if price comes back *toward* the entry within n
              minutes, at the best price it offers. This is the version a
              trader would actually want, and it forgoes trades that run away.

Scored the way every counterfactual here is scored: whichever level is touched
first ends the trade, and a bar touching both counts as the stop.

    python tools/entry_timing_sweep.py

Cached bars only. No option quotes, no network beyond the bar cache.
"""

import pathlib
import random
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from tools.inverted_trigger import bars, load, number, walk

DELAYS = (5, 10, 15, 30, 45)

# How much better than the signal price the resting order sits, in units of the
# trade's own risk. 0.25R is a quarter of the way to the stop -- a real pullback,
# not a rounding error, and reachable often enough to leave a sample.
LIMIT_OFFSET_R = 0.25


def geometry(entry, risk, reward, is_short):
    """Stop and target re-anchored to a new entry, keeping the distances."""

    if is_short:
        return entry + risk, entry - reward
    return entry - risk, entry + reward


def main():

    random.seed(17)
    rows = load()

    arms = {"signal": []}
    for d in DELAYS:
        arms[f"delay {d}m"] = []
        arms[f"limit {d}m"] = []

    days = []
    considered = 0

    for row in rows:

        p = row["p"] or {}
        entry = number(p.get("Candidate Entry Price"))
        stop = number(p.get("Candidate Stop Price"))
        target = number(p.get("Candidate Target Price"))
        direction = str(p.get("Candidate Direction") or "").upper()

        if None in (entry, stop, target) or direction not in {"CALL", "PUT"}:
            continue

        frame = bars(row["symbol"], str(row["trading_day"]))
        if frame is None or not len(frame):
            continue

        try:
            at = pd.Timestamp(row["scan_timestamp"])
            at = (at.tz_localize("America/New_York") if at.tzinfo is None
                  else at.tz_convert("America/New_York"))
        except Exception:
            continue

        forward = frame[frame.index > at]
        if len(forward) < 60:          # need room for the longest delay plus a trade
            continue

        is_short = direction == "PUT"
        risk, reward = abs(entry - stop), abs(target - entry)
        if risk <= 0 or reward <= 0:
            continue

        base = walk(forward, entry, stop, target, is_short)
        if base is None:
            continue

        row_arms = {"signal": base}
        ok = True

        for d in DELAYS:

            after = forward[forward.index >= at + pd.Timedelta(minutes=d)]
            if len(after) < 5:
                ok = False
                break

            # DELAY: take whatever the price is then.
            px = float(after["Open"].iloc[0])
            s2, t2 = geometry(px, risk, reward, is_short)
            r = walk(after, px, s2, t2, is_short)
            if r is None:
                ok = False
                break
            row_arms[f"delay {d}m"] = r

            # LIMIT: a resting order a fixed distance better than the signal
            # price, filled only if price trades through it inside the window.
            #
            # The first version of this took the *best* price in the window --
            # `window["Low"].min()` for a call -- which is lookahead: nobody
            # knows in advance where the next 45 minutes bottom out. It produced
            # a result that improved monotonically with window length, 5m
            # through 45m, which is the signature of a longer window containing
            # a better cherry-picked price rather than of a real effect. A limit
            # order is the honest version: it fills at its own price or not at
            # all.
            window = forward[
                (forward.index > at)
                & (forward.index <= at + pd.Timedelta(minutes=d))
            ]
            if not len(window):
                ok = False
                break

            limit = entry + LIMIT_OFFSET_R * risk if is_short else entry - LIMIT_OFFSET_R * risk
            filled = (
                float(window["High"].max()) >= limit if is_short
                else float(window["Low"].min()) <= limit
            )

            if filled:
                rest = forward[forward.index > at + pd.Timedelta(minutes=d)]
                if len(rest) < 5:
                    ok = False
                    break
                s3, t3 = geometry(limit, risk, reward, is_short)
                r = walk(rest, limit, s3, t3, is_short)
                row_arms[f"limit {d}m"] = base if r is None else r
            else:
                # Never filled: no position, scored flat. Forgoing the trade is
                # the rule's whole point, and it forgoes winners too.
                row_arms[f"limit {d}m"] = 0.0

        if not ok:
            continue

        considered += 1
        days.append(str(row["trading_day"]))
        for name, value in row_arms.items():
            arms[name].append(value)

    if considered < 50:
        print(f"\n  only {considered} usable candidates; stopping.\n")
        return

    print(f"\n  candidates      : {considered}")
    print(f"  sessions        : {len(set(days))}")
    print(f"  break-even for the option round trip is about +0.40R\n")

    def boot(values):
        out = []
        for _ in range(3000):
            out.append(sum(random.choice(values) for _ in values) / len(values))
        out.sort()
        return out[75], out[2925]

    ordered = sorted(set(days))
    split = ordered[len(ordered) // 2]
    disc = [i for i, d in enumerate(days) if d < split]
    hold = [i for i, d in enumerate(days) if d >= split]

    print(f"  {'arm':<16}{'mean R':>9}{'no top 5':>10}{'win%':>7}"
          f"{'95% CI':>20}{'disc':>8}{'hold':>8}")
    print(f"  {'-' * 78}")

    baseline = st.mean(arms["signal"])

    for name, values in arms.items():

        lo, hi = boot(values)
        s = sorted(values)
        d_mean = st.mean([values[i] for i in disc]) if len(disc) > 20 else None
        h_mean = st.mean([values[i] for i in hold]) if len(hold) > 20 else None

        flag = ""
        if st.mean(values) > 0.40 and lo > 0:
            flag = "  <== clears break-even"
        elif st.mean(values) > baseline and lo > 0:
            flag = "  <== beats signal"

        print(f"  {name:<16}{st.mean(values):>+9.3f}{st.mean(s[:-5]):>+10.3f}"
              f"{sum(1 for v in values if v > 0) / len(values) * 100:>6.0f}%"
              f"   [{lo:+.3f}, {hi:+.3f}]"
              f"{(f'{d_mean:+.3f}' if d_mean is not None else '   -'):>8}"
              f"{(f'{h_mean:+.3f}' if h_mean is not None else '   -'):>8}{flag}")

    print()
    best = max(arms.items(), key=lambda kv: st.mean(kv[1]))
    gain = st.mean(best[1]) - baseline

    print(f"  best arm: {best[0]}  at {st.mean(best[1]):+.3f}R"
          f"  ({gain:+.3f}R against the signal entry)")

    if st.mean(best[1]) > 0.40:
        print("  -> clears the option round trip. Re-test on unseen sessions before")
        print("     touching the app.\n")
    elif gain > 0.05:
        print("  -> recovers part of the timing loss but does not reach break-even")
        print("     on options. It would matter more on shares.\n")
    else:
        print("  -> waiting does not recover the timing loss. The entry moment is")
        print("     not the recoverable part, and the leak is elsewhere.\n")


if __name__ == "__main__":
    main()
