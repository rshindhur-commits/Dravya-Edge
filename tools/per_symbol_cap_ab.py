"""Should a symbol be tradeable more than once a day?

On 2026-08-14 SMCI signalled a PUT at 17:30, traded it, closed four minutes
later, then signalled a CALL eight more times and was refused every time --
`MAX_TRADES_PER_SYMBOL_PER_DAY = 1`. The operator's question is the obvious one:
why give up a strong later setup because the same ticker already traded?

## The interaction that decides it

`MAX_DAILY_ENTRIES = 5` sits above the per-symbol cap. On 2026-08-14 exactly five
trades happened, so the day was already at its ceiling — raising the per-symbol
cap alone would have changed **nothing**, it would only have altered *which* five
trades were taken. Both are therefore swept together, because testing one alone
answers a question nobody asked.

## Method

Each session's candidates are walked in time order and opened under the live
gates: no second position in a symbol at once, a 60-minute cooldown after a
symbol closes, at most 4 open, at most 3 per direction, and the two caps under
test. A trade holds its slot until its own exit, so a longer hold really does
block later signals, as it does live.

Exits use the rules shipped 2026-08-16 -- 1.5 ATR stop, flush armed at +10%,
floor 10/25 -- priced from the recorded quote with the spread crossed both ways.

## Reading it

The question is not whether more trades earn more in total; taking more trades at
a worse average is how this book has lost money before (§5.10). What matters is
whether the **extra** trades a looser cap admits are worth taking, so they are
reported on their own, and the top-5 strip is applied to them specifically.

    python tools/per_symbol_cap_ab.py

Archive only, no network beyond the cached bars.
"""

import json
import pathlib
import random
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from entry_quality import bars, bs, number, usable
from spread_ceiling_ab import (
    ARM, BE, FLUSH_ARM, FLUSH_MULT, KEEP, MAX_COST, MAX_DTE, MIN_COST,
    MIN_DTE, MIN_OI, MIN_VOLUME, STOP_ATR, load, prepare,
)

CEILING = 3.0
COOLDOWN_MINUTES = 60
MAX_OPEN = 4
MAX_PER_DIRECTION = 3

PER_SYMBOL_CAPS = [1, 2, 3]
DAILY_CAPS = [5, 8, 99]


def walk_timed(forward, contract, entry, hard, is_call):
    """Percent return AND the bar it exited on.

    `walk()` returns only the number. The exit time is needed here because a
    position holds its slot until it closes, and that is the whole mechanism
    being tested.
    """

    years_in = max(contract["dte"], 0.5) / 365.0
    theo_in = bs(entry, contract["strike"], years_in, contract["iv"], is_call)

    if theo_in <= 0.05:
        return None, None

    paid = contract["ask"]
    half = contract["spread_pct"] / 200.0
    peak = -100.0
    start = forward.index[0]

    for timestamp, bar in forward.iterrows():

        high, low = number(bar["High"]), number(bar["Low"])
        close = number(bar["Close"])

        if None in (high, low, close):
            continue

        minutes = (timestamp - start).total_seconds() / 60.0
        years_out = max(contract["dte"] - minutes / 390.0, 0.2) / 365.0

        def premium(spot):
            theo = bs(spot, contract["strike"], years_out, contract["iv"], is_call)
            return contract["mid"] * (theo / theo_in)

        if (low <= hard) if is_call else (high >= hard):
            return (premium(hard) * (1 - half) - paid) / paid * 100.0, timestamp

        favourable = high if is_call else low
        peak = max(peak, (premium(favourable) * (1 - half) - paid) / paid * 100.0)
        gain = (premium(close) * (1 - half) - paid) / paid * 100.0

        atr, average = number(bar["atr"]), number(bar["avgvol"])
        volume = number(bar["Volume"])

        if None not in (atr, average, volume) and atr > 0 and average > 0:
            against = (close < number(bar["Open"])) if is_call else (close > number(bar["Open"]))
            if (peak >= FLUSH_ARM and against and (high - low) > atr
                    and volume > FLUSH_MULT * average):
                return gain, timestamp

        floor = peak * KEEP if peak >= ARM else (0.0 if peak >= BE else None)

        if floor is not None and gain <= floor:
            return gain, timestamp

    last = number(forward["Close"].iloc[-1])
    return (premium(last) * (1 - half) - paid) / paid * 100.0, forward.index[-1]


def pick(chain):
    """Tightest contract passing every live gate, as the app selects."""

    best = None

    for raw in chain:
        contract = usable(raw)
        if contract is None:
            continue
        cost = number(raw.get("contract_cost"))
        if cost is None or not (MIN_COST <= cost <= MAX_COST):
            continue
        if contract["oi"] < MIN_OI or contract["volume"] < MIN_VOLUME:
            continue
        if not (MIN_DTE <= contract["dte"] <= MAX_DTE):
            continue
        if contract["spread_pct"] > CEILING:
            continue
        if best is None or contract["spread_pct"] < best["spread_pct"]:
            best = contract

    return best


def main():

    random.seed(83)
    rows = load()
    print(f"\n  {len(rows)} candidates, ceiling {CEILING:g}%, "
          f"cooldown {COOLDOWN_MINUTES}m, max open {MAX_OPEN}\n", flush=True)

    # Resolve every candidate once: contract, outcome, exit time.
    resolved = defaultdict(list)
    _frames = {}

    for index, record in enumerate(rows):

        if index % 400 == 0:
            print(f"    ... {index}/{len(rows)}", flush=True)

        payload = record["p"] or {}
        entry = number(payload.get("Candidate Entry Price"))
        direction = str(payload.get("Candidate Direction") or "").upper()

        if entry is None or entry <= 0 or direction not in ("CALL", "PUT"):
            continue

        try:
            chain = json.loads(record["chain"] or "[]")
        except Exception:
            continue

        if not chain:
            continue

        symbol, day = record["symbol"], str(record["trading_day"])

        if (symbol, day) not in _frames:
            frame = bars(symbol, day)
            _frames[(symbol, day)] = (
                prepare(frame) if frame is not None and len(frame) >= 25 else None
            )

        frame = _frames[(symbol, day)]

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

        if len(forward) < 5 or not len(before):
            continue

        atr = number(before["atr"].iloc[-1])

        if not atr or atr <= 0:
            continue

        contract = pick(chain)

        if contract is None:
            continue

        is_call = direction == "CALL"
        hard = entry - STOP_ATR * atr if is_call else entry + STOP_ATR * atr
        value, exit_at = walk_timed(forward, contract, entry, hard, is_call)

        if value is None:
            continue

        resolved[day].append({
            "at": at, "symbol": symbol, "direction": direction,
            "value": value, "exit_at": exit_at,
        })

    for day in resolved:
        resolved[day].sort(key=lambda c: c["at"])

    print(f"\n  {sum(len(v) for v in resolved.values())} buyable candidates "
          f"across {len(resolved)} sessions\n")

    print(f"  {'per-sym':>8}{'daily':>7}{'trades':>8}{'mean':>9}{'-top5':>9}"
          f"{'total':>10}{'win':>6}   extra trades vs baseline")
    print(f"  {'':-<86}")

    baseline_keys = None

    for per_symbol in PER_SYMBOL_CAPS:
        for daily in DAILY_CAPS:

            taken = []

            for day, candidates in resolved.items():

                open_positions = []   # (exit_at, symbol, direction)
                closed_at = {}        # symbol -> last exit
                per_symbol_count = defaultdict(int)

                for candidate in candidates:

                    now = candidate["at"]
                    open_positions = [p for p in open_positions if p[0] > now]

                    if sum(per_symbol_count.values()) >= daily:
                        continue
                    if per_symbol_count[candidate["symbol"]] >= per_symbol:
                        continue
                    if any(p[1] == candidate["symbol"] for p in open_positions):
                        continue

                    last_exit = closed_at.get(candidate["symbol"])

                    if last_exit is not None and (
                        (now - last_exit).total_seconds() / 60.0 < COOLDOWN_MINUTES
                    ):
                        continue

                    if len(open_positions) >= MAX_OPEN:
                        continue
                    if sum(
                        1 for p in open_positions if p[2] == candidate["direction"]
                    ) >= MAX_PER_DIRECTION:
                        continue

                    per_symbol_count[candidate["symbol"]] += 1
                    open_positions.append(
                        (candidate["exit_at"], candidate["symbol"], candidate["direction"])
                    )
                    closed_at[candidate["symbol"]] = candidate["exit_at"]
                    taken.append((day, candidate["symbol"], candidate["at"],
                                  candidate["value"]))

            keys = {(d, s, a) for d, s, a, _ in taken}
            values = [v for _, _, _, v in taken]

            if baseline_keys is None:
                baseline_keys = keys

            extra = [v for d, s, a, v in taken if (d, s, a) not in baseline_keys]

            strip = st.mean(sorted(values)[:-5]) if len(values) > 5 else float("nan")
            wins = sum(1 for v in values if v > 0) / len(values) * 100

            if extra:
                extra_strip = (
                    st.mean(sorted(extra)[:-5]) if len(extra) > 5 else float("nan")
                )
                note = (f"{len(extra):>3} at {st.mean(extra):+.2f}% "
                        f"(strip {extra_strip:+.2f}%)")
            else:
                note = "  baseline"

            print(f"  {per_symbol:>8}{daily:>7}{len(values):>8}"
                  f"{st.mean(values):>+8.2f}%{strip:>+8.2f}%"
                  f"{sum(values):>+9.1f}%{wins:>5.0f}%   {note}")

    print("\n  The extra column is what decides it. More trades at a worse")
    print("  average is how this book has lost money before -- so the question")
    print("  is whether the trades a looser cap ADMITS are worth taking.\n")


if __name__ == "__main__":
    main()
