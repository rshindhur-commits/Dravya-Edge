"""Does waiting improve the chance of handing the subscriber a real gain?

§5.11 established that the app's entries clear every profit level **less often
than a random moment** on the same contract, and are stopped out 58% of the time
against random's 50%. Worse than random is not uninformative -- it means the
trigger is reading something backwards, and the repo already agrees: the entry
timing score predicts inversely, and `avoid_chasing` blocks candidates that lose
three times the book average. All three say the app fires late in a move.

If that is right, waiting should help.

Delay was measured once before and returned null, but **against average return
with a fixed exit**. That is a different question from the one that matters here:
does waiting raise the odds of the option reaching +20% before the stop? A delay
can easily lower the average while raising the chance of a large favourable
excursion, because it trades away small winners for better-placed entries.

## The control, at every delay

Waiting costs clock. A trade entered 60 minutes later has less of the session
left and will clear any level less often for that reason alone, with no bearing
on entry quality. So **each delay gets its own random control with the identical
remaining horizon.** The comparison that means anything is app-at-delay-N against
random-at-delay-N, never against the undelayed baseline.

Stop distance is held constant in price terms and re-anchored to the delayed
entry, which is what an app that waited would actually do.

    python tools/entry_delay_quality.py

Archive only, no network beyond the cached bars.
"""

import pathlib
import random
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import json

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from entry_quality import LEVELS, bars, load, number, peak_before_stop, usable

DELAYS = [0, 5, 10, 15, 30, 45, 60]
RANDOM_DRAWS = 3


def main():

    random.seed(67)
    rows = load()

    peaks = {d: [] for d in DELAYS}
    stopped = {d: [] for d in DELAYS}
    rnd_peaks = {d: [] for d in DELAYS}
    rnd_stopped = {d: [] for d in DELAYS}
    horizons = {d: [] for d in DELAYS}

    used = 0

    for record in rows:

        p = record["p"] or {}
        entry0 = number(p.get("Candidate Entry Price"))
        stop0 = number(p.get("Candidate Stop Price"))
        direction = str(p.get("Candidate Direction") or "").upper()
        if None in (entry0, stop0) or direction not in {"CALL", "PUT"} or entry0 <= 0:
            continue
        risk = abs(entry0 - stop0)
        if risk <= 0:
            continue

        try:
            chain = json.loads(record["chain"] or "[]")
        except Exception:
            continue
        pool = [c for c in (usable(k) for k in chain) if c]
        liquid = [c for c in pool if c["oi"] >= 500 and c["volume"] >= 50]
        if not liquid:
            continue
        contract = min(liquid, key=lambda c: c["spread_pct"])

        symbol, day = record["symbol"], str(record["trading_day"])
        frame = bars(symbol, day)
        if frame is None or len(frame) < 20:
            continue
        try:
            at = pd.Timestamp(record["scan_timestamp"])
            at = (at.tz_localize("America/New_York") if at.tzinfo is None
                  else at.tz_convert("America/New_York"))
        except Exception:
            continue

        is_call = direction == "CALL"
        used += 1

        for delay in DELAYS:

            start = at + pd.Timedelta(minutes=delay)
            after = frame[frame.index > start]
            if len(after) < 5:
                continue

            entry = number(after["Close"].iloc[0])
            if not entry or entry <= 0:
                continue
            stop = (entry - risk) if is_call else (entry + risk)

            forward = after.iloc[1:]
            if len(forward) < 4:
                continue

            peak, was_stopped = peak_before_stop(forward, contract, entry, stop, is_call)
            if peak is None:
                continue
            peaks[delay].append(peak)
            stopped[delay].append(was_stopped)
            horizons[delay].append(len(forward))

            # Control with the identical remaining horizon, so the comparison is
            # about where the entry lands and not about clock left in the day.
            horizon = len(forward)
            latest = len(frame) - horizon - 1
            for _ in range(RANDOM_DRAWS):
                if latest <= 0:
                    break
                spot = random.randrange(0, latest)
                other_entry = number(frame["Close"].iloc[spot])
                if not other_entry or other_entry <= 0:
                    continue
                other_stop = (other_entry - risk) if is_call else (other_entry + risk)
                window = frame.iloc[spot + 1:spot + 1 + horizon]
                if len(window) < 4:
                    continue
                rpeak, rstopped = peak_before_stop(window, contract, other_entry,
                                                   other_stop, is_call)
                if rpeak is None:
                    continue
                rnd_peaks[delay].append(rpeak)
                rnd_stopped[delay].append(rstopped)

    print(f"\n  candidates : {used}")
    print(f"  stop distance held constant, re-anchored to the delayed entry")
    print(f"  each delay has its own control at the identical horizon\n")

    header = "".join(f"{f'+{int(l)}%':>9}" for l in LEVELS)
    print(f"  {'delay':<9}{'n':>7}{header}{'median':>10}{'stopped':>9}{'bars':>7}")
    print(f"  {'':-<86}")

    for delay in DELAYS:
        values = peaks[delay]
        if len(values) < 50:
            print(f"  {str(delay) + 'm':<9}{len(values):>7}   too few")
            continue
        cells = "".join(
            f"{sum(1 for v in values if v >= level) / len(values) * 100:>8.0f}%"
            for level in LEVELS)
        print(f"  {str(delay) + 'm':<9}{len(values):>7}{cells}"
              f"{st.median(values):>+9.1f}%"
              f"{sum(stopped[delay]) / len(stopped[delay]) * 100:>8.0f}%"
              f"{st.mean(horizons[delay]):>7.0f}")

    print(f"\n  RANDOM CONTROL at each delay's own horizon")
    print(f"  {'':-<86}")
    print(f"  {'delay':<9}{'n':>7}{header}{'median':>10}{'stopped':>9}")
    for delay in DELAYS:
        values = rnd_peaks[delay]
        if len(values) < 50:
            continue
        cells = "".join(
            f"{sum(1 for v in values if v >= level) / len(values) * 100:>8.0f}%"
            for level in LEVELS)
        print(f"  {str(delay) + 'm':<9}{len(values):>7}{cells}"
              f"{st.median(values):>+9.1f}%"
              f"{sum(rnd_stopped[delay]) / len(rnd_stopped[delay]) * 100:>8.0f}%")

    print(f"\n  APP MINUS RANDOM, at the +20% level -- the number that decides it")
    print(f"  {'':-<86}")
    for delay in DELAYS:
        ours, theirs = peaks[delay], rnd_peaks[delay]
        if len(ours) < 50 or len(theirs) < 50:
            continue
        a = sum(1 for v in ours if v >= 20.0) / len(ours) * 100
        b = sum(1 for v in theirs if v >= 20.0) / len(theirs) * 100
        stop_a = sum(stopped[delay]) / len(stopped[delay]) * 100
        stop_b = sum(rnd_stopped[delay]) / len(rnd_stopped[delay]) * 100
        flag = "  APP AHEAD" if a > b else ""
        print(f"    wait {delay:>3}m   app {a:5.1f}%   random {b:5.1f}%   "
              f"difference {a - b:+5.1f} pts   stop {stop_a:.0f}% vs {stop_b:.0f}%{flag}")

    print("\n  If waiting fixes a late entry, the difference should climb toward")
    print("  zero and cross it. If it stays flat or negative at every delay, the")
    print("  trigger is not merely early or late -- it is picking the wrong bars.\n")


if __name__ == "__main__":
    main()
