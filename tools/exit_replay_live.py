"""What would the patient exits have done to the real book?

Everything measured so far ran on *candidates* with the option priced by model.
This runs on the **42 trades actually taken**, 2026-07-09 to 2026-08-14, and
prices them with the **option contract's own recorded bars**. No Black-Scholes,
no synthetic contract, no assumed spread -- the traded ticker's real prints.

That makes this the most honest test available here, and also the smallest: 42
trades is a thin sample and no conclusion from it should outrank the 1,306
candidate study. It answers a different question -- not "is the rule better in
principle" but "what would it have done to the money that was actually at risk".

## The rules

    ACTUAL         what the app really did, from the recorded exit
    ema9_like      exit on the first close back through the option's EMA9
    atr_only       1.5 ATR stop on the underlying, otherwise hold to the close
    giveback_50    once the option is up 10%, exit on giving back half the peak
    giveback_33    the same, tighter
    hold_close     no exit rule at all -- the honest ceiling and floor

`hold_close` is not a proposal. It is there to show what the exits are adding or
destroying relative to doing nothing.

## Entry basis

Every arm, including ACTUAL, starts from the same recorded `option_entry_mid`, so
the comparison is unaffected by whether that mid was achievable. Differences
between arms are exits and nothing else.

    python tools/exit_replay_live.py

One option-bar fetch per trade. Run outside 09:30-16:00 ET.
"""

import pathlib
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.backtesting.historical_market_data import fetch_bars
from app.db.connection import get_engine

RULES = ["ema9_like", "atr_only", "giveback_50", "giveback_33", "hold_close"]
HARD_STOP_ATR = 1.5
ARM_AT = 10.0


def number(value):
    try:
        result = float(value)
        return None if result != result else result
    except (TypeError, ValueError):
        return None


def load():
    with get_engine().begin() as connection:
        return connection.execute(text("""
            SELECT trade_key, symbol, direction, option_ticker, entry_price,
                   option_entry_mid, option_close_mid, pnl_pct, r_multiple,
                   days_held, opened_at, closed_at, payload
            FROM paper_trades
            WHERE status = 'CLOSED' AND option_ticker IS NOT NULL
              AND option_entry_mid IS NOT NULL AND option_entry_mid > 0
            ORDER BY opened_at
        """)).mappings().all()


def option_bars(ticker, day):
    try:
        frame = fetch_bars(ticker, day, day, multiplier=5, timespan="minute")
        if frame is None or not len(frame):
            return None
        frame = frame.copy()
        frame.index = pd.to_datetime(frame.index, utc=True).tz_convert("America/New_York")
        return frame.between_time("09:30", "16:00")
    except Exception:
        return None


def underlying_bars(symbol, day):
    try:
        frame = fetch_bars(symbol, day, day)
        frame.index = frame.index.tz_convert("America/New_York")
        return frame.between_time("09:30", "16:00")
    except Exception:
        return None


def atr_of(frame):
    if frame is None or len(frame) < 15:
        return None
    close = frame["Close"]
    span = pd.concat([
        frame["High"] - frame["Low"],
        (frame["High"] - close.shift()).abs(),
        (frame["Low"] - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return number(span.ewm(span=14, adjust=False).mean().iloc[-1])


def simulate(rule, opt, under, paid, entry_spot, is_call, hard):
    """Final gain % on the option, minutes held, and the peak gain seen."""

    ema = opt["Close"].ewm(span=9, adjust=False).mean()
    start = opt.index[0]
    peak = -100.0
    last = None

    for position, (timestamp, bar) in enumerate(opt.iterrows()):

        high = number(bar["High"])
        low = number(bar["Low"])
        close = number(bar["Close"])
        if close is None or high is None:
            continue
        minutes = (timestamp - start).total_seconds() / 60.0

        peak = max(peak, (high - paid) / paid * 100.0)
        current = (close - paid) / paid * 100.0
        last = (current, minutes)

        # The underlying stop applies to every arm except the pure indicator one.
        if rule != "ema9_like" and under is not None and hard is not None:
            window = under[under.index <= timestamp]
            if len(window):
                spot_high = number(window["High"].iloc[-1])
                spot_low = number(window["Low"].iloc[-1])
                if spot_low is not None and spot_high is not None:
                    if (spot_low <= hard) if is_call else (spot_high >= hard):
                        return current, minutes, peak

        if rule == "ema9_like":
            reference = number(ema.iloc[position])
            if reference is not None and close < reference and position > 2:
                return current, minutes, peak

        elif rule in ("giveback_50", "giveback_33"):
            if peak >= ARM_AT:
                floor = peak * (0.5 if rule == "giveback_50" else (2.0 / 3.0))
                if current <= floor:
                    return current, minutes, peak

    if last is None:
        return None, None, peak
    return last[0], last[1], peak


def main():

    rows = load()
    print(f"\n  closed trades with an option ticker : {len(rows)}")

    actual, results = [], {r: [] for r in RULES}
    holds = {r: [] for r in RULES}
    peaks_seen = {r: [] for r in RULES}
    actual_peak = []
    used = 0
    skipped = 0
    skip_index = set()

    for record in rows:

        paid = number(record["option_entry_mid"])
        got = number(record["option_close_mid"])
        if not paid or paid <= 0:
            continue

        day = str(pd.Timestamp(record["opened_at"]).tz_convert("America/New_York").date())
        opt = option_bars(record["option_ticker"], day)
        if opt is None or len(opt) < 6:
            skipped += 1
            continue

        opened = pd.Timestamp(record["opened_at"]).tz_convert("America/New_York")
        forward = opt[opt.index >= opened]
        if len(forward) < 5:
            skipped += 1
            continue

        under = underlying_bars(record["symbol"], day)
        is_call = str(record["direction"] or "").upper() == "CALL"
        entry_spot = number(record["entry_price"])
        atr = atr_of(under[under.index <= opened]) if under is not None else None
        hard = None
        if entry_spot and atr:
            hard = entry_spot - HARD_STOP_ATR * atr if is_call else entry_spot + HARD_STOP_ATR * atr

        forward_under = under[under.index >= opened] if under is not None else None

        if (number(record['days_held']) or 1) > 1:
            skip_index.add(used)
        used += 1
        if got and got > 0:
            actual.append((got - paid) / paid * 100.0)
        else:
            actual.append(number(record["pnl_pct"]) or 0.0)
        actual_peak.append(max((number(forward["High"].max()) - paid) / paid * 100.0, -100.0))

        for rule in RULES:
            gain, minutes, peak = simulate(rule, forward, forward_under, paid,
                                           entry_spot, is_call, hard)
            if gain is None:
                continue
            results[rule].append(gain)
            holds[rule].append(minutes)
            peaks_seen[rule].append(peak)

    print(f"  replayed on real option bars        : {used}")
    if skipped:
        print(f"  skipped, no usable option bars      : {skipped}")
    print()

    if used < 15:
        print("  too few; stopping.\n")
        return

    def line(label, values, hold=None, peaks=None):
        if not values:
            return f"  {label:<14} --"
        wins = sum(1 for v in values if v > 0) / len(values) * 100
        green = sum(1 for p in (peaks or []) if p >= ARM_AT)
        trip = (sum(1 for p, v in zip(peaks or [], values) if p >= ARM_AT and v <= 0)
                / green * 100) if green else float("nan")
        held = f"{st.mean(hold):.0f}m" if hold else "--"
        strip = st.mean(sorted(values)[:-5]) if len(values) > 5 else float("nan")
        return (f"  {label:<14}{len(values):>5}{st.mean(values):>+9.2f}%"
                f"{strip:>+9.2f}%{st.median(values):>+9.2f}%{sum(values):>+10.1f}%"
                f"{wins:>6.0f}%{held:>8}{trip:>10.0f}%")

    print(f"  {'rule':<14}{'n':>5}{'mean':>10}{'-top5':>9}{'median':>9}{'total':>10}"
          f"{'win':>6}{'hold':>8}{'RNDTRIP':>10}")
    print(f"  {'':-<85}")
    print(line("ACTUAL", actual, None, actual_peak))
    for rule in RULES:
        print(line(rule, results[rule], holds[rule], peaks_seen[rule]))

    # The 9-day SMCI position ran unmanaged and booked -27.45%, which is 43% of
    # the whole recorded loss from one trade the system was never supposed to
    # hold. Every arm here exits it on day one, so leaving it in credits the new
    # rules for fixing a bug rather than for exiting better.
    if len(skip_index) and used > len(skip_index) + 10:
        keep = [i for i in range(used) if i not in skip_index]
        print(f"\n  EXCLUDING {len(skip_index)} multi-day trade(s) -- the 9-day SMCI")
        print(f"  orphan alone is 43% of the recorded loss and every arm exits it")
        print(f"  on day one, so leaving it in credits these rules for a bug fix.")
        print(f"  {'':-<85}")
        print(line("ACTUAL", [actual[i] for i in keep], None,
                   [actual_peak[i] for i in keep]))
        for rule in RULES:
            if len(results[rule]) == used:
                print(line(rule, [results[rule][i] for i in keep],
                           [holds[rule][i] for i in keep],
                           [peaks_seen[rule][i] for i in keep]))

    print("\n  ACTUAL is what the app really did. `total` is the sum of per-trade")
    print("  percentage returns -- the headline the operator asked for.")
    print("  ROUNDTRIP is, of trades ever up 10%, the share ending at or below zero.\n")

    base = sum(actual)
    for rule in RULES:
        if results[rule]:
            change = sum(results[rule]) - base
            print(f"    {rule:<14} total {sum(results[rule]):+8.1f}%   "
                  f"against actual {change:+8.1f} points")
    print()


if __name__ == "__main__":
    main()
