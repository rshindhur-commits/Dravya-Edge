"""Does the signal's edge grow with time held, and does the growth outrun theta?

PLAN B, PHASE 0. This is a gate, not a build step: if no holding period clears
the option toll, a rebuild changes nothing, because the limit is a property of
the signal rather than of the software.

The reason to ask. Six selection levers returned null, and one line explains all
of them at once -- the signal earns +0.134R against a break-even near +0.40R, so
the edge is thin, real, and evenly spread. There is no losing subset to filter
out. What has never been varied is the *hold*: `contract_choice_sweep` walks the
underlying to its stop or target inside the same day, and `entry_timing_sweep`
moves the entry while pinning instrument and duration. Every experiment here has
priced a better contract onto a ten-minute scalp.

That matters because the two costs behave differently. The spread is charged per
round trip and is close to fixed; the move grows with time held. Ten minutes is
the worst available ratio between them.

## What the first version of this tool got wrong

Three faults, all of which flattered a longer hold, and all fixed here:

**R without a stop is not the R that break-even was measured in.** +0.134R came
from a walk that caps a loss at -1R. Holding plainly to a close has no such cap,
and the denominator is a small intraday stop, so a routine multi-day drift prints
as +9R. That number is arithmetically correct and economically meaningless. The
comparison against +0.40R has been removed; **the option column decides this**,
because a percentage of premium needs no conversion to be spent.

**The two panels were different samples.** Only 7 of 11 archived days record
implied volatility, so the option panel silently dropped to 2 days at the longest
horizon while the underlying panel kept 7. Both panels now run on the identical
IV-bearing sample, and the day count is printed beside every row.

**The spread charged was the wrong contract's.** The old code took the median
across the whole recorded chain -- mostly near-dated weeklies -- and applied it to
a 35-day ITM contract. Spread is now taken from the matching bucket in the same
chain, which measures 4.37% median for 26d+ ITM against 7.00% for 0-10d ATM.

## The confound this exists to rule out

Most candidates are calls, and the archive spans 12 days of one market. If those
days drifted up, a direction-aware return will look like an edge when it is only
beta. So every horizon is scored twice: **once following the signal, once always
long the same symbol over the same window**. The signal only has information if
it beats always-long. That is the "judge against random, not zero" rule applied
to the one benchmark that can masquerade as skill here.

**Confidence intervals resample days, not candidates.** Candidates inside a day
share a market, so the day is the independent unit. With 7 to 12 blocks the
intervals are wide, and that width is the honest answer rather than a defect to
tune away.

    python tools/planb_horizon.py

Daily bars, one Polygon range call per symbol. Run outside 09:30-16:00 ET: the
rate limiter in `polygon_client` is per-process, so this competes with the live
worker for one account limit rather than sharing a budget with it.
"""

import math
import pathlib
import random
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import json

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.backtesting.historical_market_data import fetch_bars
from app.db.connection import get_engine

HORIZONS = [0, 1, 2, 3, 5]

# The contract Plan B would actually buy: far enough in the money that delta is
# high and the spread is a small share of premium, far enough out in time that a
# multi-day hold is not fighting the steep end of the decay curve.
ITM_PCT = 3.0
DTE = 35.0
RATE = 0.04

# Median quoted spread for 26d+ ITM across 7,087 recorded contracts. Only used
# where the scan's own chain has no contract in that bucket to measure.
FALLBACK_SPREAD_PCT = 4.37

BOOTSTRAP = 4000


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs(spot, strike, years, iv, is_call):
    """Black-Scholes price. Intrinsic value at expiry or zero vol."""

    if years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return max(0.0, (spot - strike) if is_call else (strike - spot))

    d1 = (math.log(spot / strike) + (RATE + 0.5 * iv * iv) * years) / (iv * math.sqrt(years))
    d2 = d1 - iv * math.sqrt(years)
    disc = math.exp(-RATE * years)

    if is_call:
        return spot * norm_cdf(d1) - strike * disc * norm_cdf(d2)
    return strike * disc * norm_cdf(-d2) - spot * norm_cdf(-d1)


def number(value):
    try:
        result = float(value)
        return None if result != result else result
    except (TypeError, ValueError):
        return None


def load():
    with get_engine().begin() as connection:
        return connection.execute(text("""
            SELECT DISTINCT ON (s.symbol, s.trading_day, s.scan_timestamp)
                   s.symbol, s.trading_day, s.scan_timestamp,
                   s.decision_payload->>'Option Liquidity Attempts' AS chain,
                   s.decision_payload AS p
            FROM scanner_snapshot s
            WHERE s.decision_payload->>'Candidate Entry Price' IS NOT NULL
              AND s.decision_payload->>'Candidate Stop Price' IS NOT NULL
              AND s.decision_payload->>'Candidate Direction' IS NOT NULL
            ORDER BY s.symbol, s.trading_day, s.scan_timestamp
        """)).mappings().all()


_daily = {}


def daily(symbol, first, last):
    """One range call per symbol, sliced per candidate afterwards."""

    if symbol not in _daily:
        try:
            frame = fetch_bars(symbol, first, last, multiplier=1, timespan="day")
            if frame is None or not len(frame):
                _daily[symbol] = None
            else:
                frame = frame.copy()
                frame.index = pd.to_datetime(frame.index, utc=True).tz_convert("America/New_York")
                frame["__day"] = [str(ts.date()) for ts in frame.index]
                _daily[symbol] = frame.reset_index(drop=True)
        except Exception:
            _daily[symbol] = None
    return _daily[symbol]


def chain_read(raw, entry, is_call):
    """Median IV, and the median spread of contracts in the bucket we would buy.

    Median rather than nearest-strike: a single contract's quote can be stale or
    crossed, and the median is the stable read of what that symbol was priced at
    that day. The spread is drawn from 26d+ ITM contracts specifically, because
    charging a weekly's spread to a 35-day ITM contract is what made the first
    version of this tool report a loss before any time had passed.
    """

    try:
        contracts = json.loads(raw or "[]")
    except Exception:
        return None, None

    ivs, bucket = [], []

    for contract in contracts:

        iv = number(contract.get("iv"))
        if iv and 0.02 < iv < 3.0:
            ivs.append(iv)

        strike = number(contract.get("strike"))
        dte = number(contract.get("dte"))
        spread = number(contract.get("spread_pct"))

        if None in (strike, dte, spread) or not (0 <= spread < 50):
            continue

        moneyness = ((strike - entry) if is_call else (entry - strike)) / entry * 100.0
        if dte >= 26 and moneyness < -2.0:
            bucket.append(spread)

    if not ivs:
        return None, None
    return st.median(ivs), (st.median(bucket) if bucket else None)


def block_ci(by_day, level=0.95):
    """Bootstrap over days, resampling whole days with replacement."""

    days = list(by_day)
    if len(days) < 3:
        return None, None

    means = []
    for _ in range(BOOTSTRAP):
        picked = [random.choice(days) for _ in days]
        pool = [v for d in picked for v in by_day[d]]
        if pool:
            means.append(st.mean(pool))

    if not means:
        return None, None

    means.sort()
    return means[int((1 - level) / 2 * len(means))], means[int((1 + level) / 2 * len(means)) - 1]


def without_top(values, k=5):
    return None if len(values) <= k else st.mean(sorted(values)[:-k])


def row(label, by_day, positive_is_good=True):
    values = [v for day in by_day for v in by_day[day]]
    if len(values) < 40:
        return f"  {label:<8}{len(values):>7}{len(by_day):>6}   too few to read"

    mean = st.mean(values)
    lo, hi = block_ci(by_day)
    stripped = without_top(values)
    wins = sum(1 for v in values if v > 0) / len(values) * 100

    ci = f"[{lo:+.2f}, {hi:+.2f}]" if lo is not None else "--"
    strip = f"{stripped:+.2f}" if stripped is not None else "--"
    flag = "  CLEARS" if (positive_is_good and lo is not None and lo > 0) else ""

    return (f"  {label:<8}{len(values):>7}{len(by_day):>6}{mean:>+9.2f}"
            f"{ci:>19}{strip:>9}{wins:>7.0f}%{flag}")


def main():

    random.seed(29)
    rows = load()
    print(f"\n  candidates with geometry      : {len(rows)}")

    days_all = sorted({str(r["trading_day"]) for r in rows})
    first, last = days_all[0], days_all[-1]
    last_fetch = str((pd.Timestamp(last) + pd.Timedelta(days=max(HORIZONS) * 2 + 10)).date())
    print(f"  archive span                  : {first} to {last}  ({len(days_all)} trading days)")

    sig_pct = {h: defaultdict(list) for h in HORIZONS}
    long_pct = {h: defaultdict(list) for h in HORIZONS}
    opt_sig = {h: defaultdict(list) for h in HORIZONS}
    opt_long = {h: defaultdict(list) for h in HORIZONS}

    used = 0
    calls = 0
    spread_used = []

    for record in rows:

        p = record["p"] or {}
        entry = number(p.get("Candidate Entry Price"))
        direction = str(p.get("Candidate Direction") or "").upper()
        if entry is None or entry <= 0 or direction not in {"CALL", "PUT"}:
            continue

        is_call = direction == "CALL"

        # Both panels must run on one sample, so a candidate without a chain IV
        # is dropped from everything rather than from the option panel alone.
        iv, bucket_spread = chain_read(record["chain"], entry, is_call)
        if iv is None:
            continue

        symbol, day = record["symbol"], str(record["trading_day"])
        frame = daily(symbol, first, last_fetch)
        if frame is None or not len(frame):
            continue

        where = frame.index[frame["__day"] == day]
        if not len(where):
            continue
        start = int(where[0])

        spread = bucket_spread if bucket_spread is not None else FALLBACK_SPREAD_PCT
        spread_used.append(spread)
        half = spread / 200.0

        strike = entry * (1 - ITM_PCT / 100.0) if is_call else entry * (1 + ITM_PCT / 100.0)
        # The always-long benchmark buys the call regardless of what the signal said.
        long_strike = entry * (1 - ITM_PCT / 100.0)

        used += 1
        calls += 1 if is_call else 0

        for h in HORIZONS:

            index = start + h
            if index >= len(frame):
                continue

            exit_price = number(frame["Close"].iloc[index])
            if exit_price is None or exit_price <= 0:
                continue

            move = (exit_price - entry) / entry * 100.0
            sig_pct[h][day].append(move if is_call else -move)
            long_pct[h][day].append(move)

            years_in = DTE / 365.0
            years_out = max(DTE - h, 1.0) / 365.0

            for strike_used, is_call_used, sink in (
                (strike, is_call, opt_sig),
                (long_strike, True, opt_long),
            ):
                theo_in = bs(entry, strike_used, years_in, iv, is_call_used)
                theo_out = bs(exit_price, strike_used, years_out, iv, is_call_used)
                if theo_in <= 0.05:
                    continue
                fill_in = theo_in * (1 + half)
                fill_out = theo_out * (1 - half)
                sink[h][day].append((fill_out - fill_in) / fill_in * 100.0)

    print(f"  usable (chain IV present)     : {used}")
    if not used:
        print("  nothing usable; stopping.\n")
        return

    print(f"  direction mix                 : {calls / used * 100:.0f}% CALL, "
          f"{(used - calls) / used * 100:.0f}% PUT")
    print(f"  spread charged, median        : {st.median(spread_used):.2f}%  "
          f"(26d+ ITM bucket from each scan's own chain)\n")

    head = f"  {'horizon':<8}{'n':>7}{'days':>6}{'mean':>9}{'95% CI (by day)':>19}{'-top5':>9}{'win':>8}"

    print("  UNDERLYING MOVE, % , following the signal's direction")
    print(f"  {'':-<72}")
    print(head)
    for h in HORIZONS:
        print(row("close" if h == 0 else f"+{h}d", sig_pct[h]))

    print("\n  SAME WINDOWS, ALWAYS LONG -- the confound to beat")
    print(f"  {'':-<72}")
    print(head)
    for h in HORIZONS:
        print(row("close" if h == 0 else f"+{h}d", long_pct[h]))

    print(f"\n  OPTION, {ITM_PCT:.0f}% ITM at {DTE:.0f} DTE, ask in / bid out, % of premium")
    print(f"  {'':-<72}")
    print(head)
    for h in HORIZONS:
        print(row("close" if h == 0 else f"+{h}d", opt_sig[h]))

    print("\n  SAME OPTION, ALWAYS LONG -- the confound to beat")
    print(f"  {'':-<72}")
    print(head)
    for h in HORIZONS:
        print(row("close" if h == 0 else f"+{h}d", opt_long[h]))

    print("\n  Plan B is worth building only if the signal's option line is")
    print("  positive with its lower bound above zero, AND beats always-long.\n")


if __name__ == "__main__":
    main()
