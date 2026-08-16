"""Does the exit protect a gain once the trade has one?

The operator's requirement, stated precisely: the subscriber takes profit on
their own appetite, but the app must signal an exit when the trade **reverses** --
before a position that was up turns into a loss, or gives back the profit it
made.

That is a different question from §5.12's "does patience help". Patience is about
not bailing on noise. This is about not letting a winner round-trip. A rule can
pass one and fail the other, and the repo already records the symptom: trades
peaking under +1R give back 76% of their best price, because
`EXIT_BREAKEVEN_TRIGGER_R` sits at 1.0 and never engages for them.

## The measure that matters

    round-trip rate   of trades that were ever up 10% or more,
                      the share that finished at or below zero

That single number is the operator's complaint made countable. Everything else
here is context for it.

## The rules

All are evaluated on the **option's** value, because that is what the subscriber
watches, not on the underlying.

    ema9            close crosses back through EMA9 -- the app today
    atr_only        1.5 ATR hard stop, no indicator exit
    breakeven       once up 10%, never allow it below entry
    giveback_50     once up 10%, exit on giving back half the peak gain
    giveback_33     once up 10%, exit on giving back a third of the peak gain
    atr_giveback    the ATR stop plus the give-back-half rule

`giveback_33` is deliberately tight and is expected to cut winners short. It is
here so the cost of over-protecting is visible rather than assumed.

## Why this is not the failed experiment

Lowering `EXIT_BREAKEVEN_TRIGGER_R` from 1.0 to 0.25 was tried and made things
worse -- 222 trades against 191 with a worse total, because closing early frees
the symbol to re-enter and the extra trades were bad ones. These rules are
measured on a **fixed candidate set with no re-entry**, so they are judged on
whether they protect a gain, not on how many new trades they invite.

    python tools/exit_protection.py

Archive only, no network beyond the cached bars.
"""

import pathlib
import random
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import json
from collections import defaultdict

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from entry_quality import bars, bs, load, number, usable
from exit_patience import enrich

RULES = ["ema9", "atr_only", "breakeven", "giveback_50", "giveback_33", "atr_giveback"]
HARD_STOP_ATR = 1.5
ARM_AT = 10.0            # a gain must reach this before protection engages
BOOTSTRAP = 2000


def option_gain(contract, entry, spot, minutes, is_call):
    """Percent gain on premium at a given underlying price, sellable at the bid."""

    years_in = max(contract["dte"], 0.5) / 365.0
    years_out = max(contract["dte"] - minutes / 390.0, 0.2) / 365.0
    theo_in = bs(entry, contract["strike"], years_in, contract["iv"], is_call)
    if theo_in <= 0.05:
        return None
    theo_out = bs(spot, contract["strike"], years_out, contract["iv"], is_call)
    got = contract["mid"] * (theo_out / theo_in) * (1 - contract["spread_pct"] / 200.0)
    return (got - contract["ask"]) / contract["ask"] * 100.0


def run(forward, rule, contract, entry, atr, is_call):
    """Final gain %, minutes held, and the peak gain seen along the way."""

    hard = entry - HARD_STOP_ATR * atr if is_call else entry + HARD_STOP_ATR * atr
    start = forward.index[0]
    peak = -100.0
    last = None

    for timestamp, bar in forward.iterrows():

        high, low = number(bar["High"]), number(bar["Low"])
        close = number(bar["Close"])
        if high is None or low is None or close is None:
            continue
        minutes = (timestamp - start).total_seconds() / 60.0

        # The hard stop applies to every arm except the pure indicator one, so
        # no rule can look good by never cutting a loser.
        if rule != "ema9":
            if (low <= hard) if is_call else (high >= hard):
                gain = option_gain(contract, entry, hard, minutes, is_call)
                return gain, minutes, peak

        favourable = high if is_call else low
        best_now = option_gain(contract, entry, favourable, minutes, is_call)
        if best_now is not None:
            peak = max(peak, best_now)

        current = option_gain(contract, entry, close, minutes, is_call)
        if current is None:
            continue
        last = (current, minutes)

        if rule == "ema9":
            reference = number(bar["ema9"])
            if reference is not None and ((close < reference) if is_call else (close > reference)):
                return current, minutes, peak

        elif rule in ("breakeven", "giveback_50", "giveback_33", "atr_giveback"):
            if peak >= ARM_AT:
                if rule == "breakeven":
                    floor = 0.0
                elif rule == "giveback_33":
                    floor = peak * (2.0 / 3.0)
                else:
                    floor = peak * 0.5
                if current <= floor:
                    return current, minutes, peak

    if last is None:
        return None, None, peak
    return last[0], last[1], peak


def block_ci(by_day, level=0.95):
    days = list(by_day)
    if len(days) < 5:
        return None, None
    means = []
    for _ in range(BOOTSTRAP):
        pool = [v for d in (random.choice(days) for _ in days) for v in by_day[d]]
        if pool:
            means.append(st.mean(pool))
    if not means:
        return None, None
    means.sort()
    return means[int((1 - level) / 2 * len(means))], means[int((1 + level) / 2 * len(means)) - 1]


def main():

    random.seed(73)
    rows = load()

    finals = {r: defaultdict(list) for r in RULES}
    holds = {r: [] for r in RULES}
    was_green = {r: 0 for r in RULES}
    round_tripped = {r: 0 for r in RULES}
    used = 0
    _enriched = {}

    for record in rows:

        p = record["p"] or {}
        entry = number(p.get("Candidate Entry Price"))
        direction = str(p.get("Candidate Direction") or "").upper()
        if entry is None or entry <= 0 or direction not in {"CALL", "PUT"}:
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
        if (symbol, day) not in _enriched:
            frame = bars(symbol, day)
            _enriched[(symbol, day)] = (enrich(frame) if frame is not None
                                        and len(frame) >= 25 else None)
        frame = _enriched[(symbol, day)]
        if frame is None:
            continue

        try:
            at = pd.Timestamp(record["scan_timestamp"])
            at = (at.tz_localize("America/New_York") if at.tzinfo is None
                  else at.tz_convert("America/New_York"))
        except Exception:
            continue

        forward = frame[frame.index > at]
        before = frame[frame.index <= at]
        if len(forward) < 6 or not len(before):
            continue
        atr = number(before["atr"].iloc[-1])
        if not atr or atr <= 0:
            continue

        is_call = direction == "CALL"
        used += 1

        for rule in RULES:
            gain, minutes, peak = run(forward, rule, contract, entry, atr, is_call)
            if gain is None:
                continue
            finals[rule][day].append(gain)
            holds[rule].append(minutes)
            if peak >= ARM_AT:
                was_green[rule] += 1
                if gain <= 0:
                    round_tripped[rule] += 1

    print(f"\n  candidates : {used}")
    print(f"  protection arms engage once the option has been up {ARM_AT:.0f}%")
    print(f"  fixed candidate set, no re-entry, so a rule cannot win by")
    print(f"  inviting more trades\n")

    if used < 100:
        print("  too few; stopping.\n")
        return

    print(f"  {'rule':<14}{'n':>6}{'mean':>9}{'95% CI':>19}{'median':>9}"
          f"{'win':>6}{'hold':>8}{'was +10%':>10}{'ROUND-TRIP':>12}")
    print(f"  {'':-<95}")

    for rule in RULES:
        values = [v for d in finals[rule] for v in finals[rule][d]]
        if len(values) < 50:
            continue
        lo, hi = block_ci(finals[rule])
        ci = f"[{lo:+.2f}, {hi:+.2f}]" if lo is not None else "--"
        wins = sum(1 for v in values if v > 0) / len(values) * 100
        green = was_green[rule]
        trip = (round_tripped[rule] / green * 100) if green else float("nan")
        print(f"  {rule:<14}{len(values):>6}{st.mean(values):>+8.2f}%{ci:>19}"
              f"{st.median(values):>+8.2f}%{wins:>5.0f}%{st.mean(holds[rule]):>7.0f}m"
              f"{green:>10}{trip:>11.0f}%")

    print("\n  ROUND-TRIP is the operator's requirement made countable: of the")
    print("  trades that were ever up 10% or more, the share that finished at or")
    print("  below zero. Lower is better, and it is the column to read first.\n")


if __name__ == "__main__":
    main()
