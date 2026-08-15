"""Does risking more for more pay, once the option toll is charged honestly?

PLAN B, PHASE 0b. The operator's challenge, and it is a fair one: a 2% round trip
was asserted as a gate, but the toll is a *percentage* of premium, not a fixed
fee. An option that gains 50% keeps 43% after a 7% round trip. The toll does not
grow with the winner, so a large enough winner absorbs it.

That is a real strategy class rather than wishful thinking. Convex books -- long
volatility, tail hedges, trend following -- run win rates in the twenties and pay
tolls far worse than ours, and they work because the winners are multiples rather
than increments. **Options reward convex payoffs and punish incremental ones**,
which makes a high-toll instrument the worst possible home for the small-target
strategy this app has been running.

So the question this tool asks is not "is the spread small enough" but "**is
there a target wide enough that the spread stops mattering**".

## What it does not test

Position size. Trading larger multiplies whatever the per-trade expectancy is,
and 601 trades across every cost cap already showed the rate does not move -- the
cap changes the stake, not the outcome. Size is excluded deliberately: it is the
lever that feels like risk while changing nothing.

## Method

Every candidate keeps its own entry and stop, so risk per trade is unchanged.
Only the **target** varies, from 1R out to 12R and finally to no target at all.
Each is walked on daily bars until the stop is touched, the target is touched, or
the archive runs out. **A bar touching both counts as a stop**, since intrabar
order is unknowable at this resolution and resolving it favourably would
manufacture the edge being measured.

The option is then priced at entry and exit with the IV recorded on that symbol's
own chain that day, bought at the ask and sold at the bid, charged the spread
measured for the bucket actually being bought.

A wide target should show a **falling win rate and a rising mean**. If the mean
rises past zero, convexity is the answer and Plan B has a design. If the mean
stays negative as the target widens, the payoff is not convex, and no amount of
risk taken converts it -- that is the honest end of the road for buying options
directionally.

    python tools/planb_convexity.py

Cached daily bars. Run outside 09:30-16:00 ET.
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

TARGETS = [1.0, 2.0, 3.0, 5.0, 8.0, 12.0, None]

# Widening the target alone is only half of "risk more to get more". With the
# app's own stop the trade dies in 0.6 days at a 3% win rate, so a wide target is
# never reached -- the stop resolves it first. Giving the trade room is the other
# half, and it is the half that actually raises risk per trade.
STOP_MULTS = [1.0, 2.0, 3.0, 5.0]
MAX_DAYS = 10

ITM_PCT = 3.0
DTE = 35.0
RATE = 0.04
FALLBACK_SPREAD_PCT = 6.92      # median, 26-60 DTE ITM, QUOTE_OK only
BOOTSTRAP = 3000


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs(spot, strike, years, iv, is_call):
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
    """Median IV, and the median spread of the bucket we would actually buy."""

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
        status = str(contract.get("quote_status") or "")
        if None in (strike, dte, spread) or not (0 <= spread < 50):
            continue
        if status and status != "QUOTE_OK":
            continue
        moneyness = ((strike - entry) if is_call else (entry - strike)) / entry * 100.0
        if 26 <= dte <= 60 and moneyness < -2.0:
            bucket.append(spread)

    if not ivs:
        return None, None
    return st.median(ivs), (st.median(bucket) if bucket else None)


_intraday = {}


def intraday(symbol, day):
    """5-minute bars for the entry day, so day zero starts at the signal.

    The entry day cannot be walked as a daily bar. That bar's range covers the
    whole session including the hours *before* the signal fired, so a stop a few
    tenths of a percent away is registered as touched on essentially every trade
    -- which is exactly what the first run of this tool reported: a 0% win rate
    at every target width, resolved in 0.0 days.
    """

    key = (symbol, day)
    if key not in _intraday:
        try:
            frame = fetch_bars(symbol, day, day)
            frame.index = frame.index.tz_convert("America/New_York")
            _intraday[key] = frame.between_time("09:30", "16:00")
        except Exception:
            _intraday[key] = None
    return _intraday[key]


def _touch(high, low, entry, stop, target_price, is_call):
    """(exit price or None). The stop wins a bar that touches both."""

    if high is None or low is None:
        return None
    hit_stop = low <= stop if is_call else high >= stop
    hit_target = (target_price is not None and
                  (high >= target_price if is_call else low <= target_price))
    if hit_stop:
        return stop
    if hit_target:
        return target_price
    return None


def walk(frame, start, entry, stop, target_price, is_call, max_days, first_day):
    """Exit price and days held, walking the entry day intraday then daily."""

    # Day zero, from the signal forward only.
    if first_day is not None and len(first_day):
        for _ts, bar in first_day.iterrows():
            hit = _touch(number(bar["High"]), number(bar["Low"]),
                         entry, stop, target_price, is_call)
            if hit is not None:
                return hit, 0

    for offset in range(1, max_days + 1):
        index = start + offset
        if index >= len(frame):
            break
        hit = _touch(number(frame["High"].iloc[index]), number(frame["Low"].iloc[index]),
                     entry, stop, target_price, is_call)
        if hit is not None:
            return hit, offset

    last = min(start + max_days, len(frame) - 1)
    if last < start:
        return None, None
    if last == start and first_day is not None and len(first_day):
        return number(first_day["Close"].iloc[-1]), 0
    return number(frame["Close"].iloc[last]), last - start


def block_ci(by_day, level=0.95):
    days = list(by_day)
    if len(days) < 3:
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


def without_top(values, k=5):
    return None if len(values) <= k else st.mean(sorted(values)[:-k])


def report(label, by_day, extra=""):
    values = [v for day in by_day for v in by_day[day]]
    if len(values) < 40:
        return f"  {label:<8}{len(values):>7}   too few"
    mean = st.mean(values)
    lo, hi = block_ci(by_day)
    stripped = without_top(values)
    wins = sum(1 for v in values if v > 0) / len(values) * 100
    ci = f"[{lo:+.1f}, {hi:+.1f}]" if lo is not None else "--"
    strip = f"{stripped:+.2f}" if stripped is not None else "--"
    flag = "  CLEARS" if lo is not None and lo > 0 else ""
    return (f"  {label:<8}{len(values):>7}{len(by_day):>6}{mean:>+9.2f}"
            f"{ci:>18}{strip:>9}{wins:>7.0f}%{extra:>8}{flag}")


def main():

    random.seed(31)
    rows = load()
    days_all = sorted({str(r["trading_day"]) for r in rows})
    first, last = days_all[0], days_all[-1]
    last_fetch = str((pd.Timestamp(last) + pd.Timedelta(days=MAX_DAYS * 2 + 10)).date())

    print(f"\n  candidates                    : {len(rows)}")
    print(f"  archive                       : {first} to {last}")
    print(f"  target widths tested          : {TARGETS}")
    print(f"  max hold                      : {MAX_DAYS} trading days\n")

    cells = [(m, t) for m in STOP_MULTS for t in TARGETS]
    r_by_target = {c: defaultdict(list) for c in cells}
    opt_by_target = {c: defaultdict(list) for c in cells}
    held_by_target = defaultdict(list)
    used = 0
    spreads = []

    for record in rows:

        p = record["p"] or {}
        entry = number(p.get("Candidate Entry Price"))
        stop = number(p.get("Candidate Stop Price"))
        direction = str(p.get("Candidate Direction") or "").upper()
        if None in (entry, stop) or direction not in {"CALL", "PUT"} or entry <= 0:
            continue

        risk = abs(entry - stop)
        if risk <= 0:
            continue

        is_call = direction == "CALL"
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
        if start + 1 >= len(frame):
            continue

        # Bars after the scan on the entry day; without these the walk starts
        # before the signal existed.
        session = intraday(symbol, day)
        if session is None or not len(session):
            continue
        try:
            at = pd.Timestamp(record["scan_timestamp"])
            at = (at.tz_localize("America/New_York") if at.tzinfo is None
                  else at.tz_convert("America/New_York"))
        except Exception:
            continue
        first_day = session[session.index > at]
        if len(first_day) < 2:
            continue

        spread = bucket_spread if bucket_spread is not None else FALLBACK_SPREAD_PCT
        spreads.append(spread)
        half = spread / 200.0
        strike = entry * (1 - ITM_PCT / 100.0) if is_call else entry * (1 + ITM_PCT / 100.0)
        used += 1

        for stop_mult in STOP_MULTS:

            wide_risk = risk * stop_mult
            wide_stop = entry - wide_risk if is_call else entry + wide_risk

            for target in TARGETS:

                target_price = None
                if target is not None:
                    target_price = (entry + target * wide_risk if is_call
                                    else entry - target * wide_risk)

                exit_price, held = walk(frame, start, entry, wide_stop, target_price,
                                        is_call, MAX_DAYS, first_day)
                if exit_price is None or exit_price <= 0:
                    continue

                cell = (stop_mult, target)
                r = ((exit_price - entry) if is_call else (entry - exit_price)) / wide_risk
                r_by_target[cell][day].append(r)
                held_by_target[cell].append(held)

                theo_in = bs(entry, strike, DTE / 365.0, iv, is_call)
                theo_out = bs(exit_price, strike, max(DTE - held, 1.0) / 365.0, iv, is_call)
                if theo_in <= 0.05:
                    continue
                fill_in = theo_in * (1 + half)
                fill_out = theo_out * (1 - half)
                opt_by_target[cell][day].append((fill_out - fill_in) / fill_in * 100.0)

    print(f"  usable                        : {used}")
    if used < 50:
        print("  too few; stopping.\n")
        return
    print(f"  spread charged, median        : {st.median(spreads):.2f}%\n")

    def grid(source, title, unit):
        print(f"\n  {title}")
        print(f"  {'':-<78}")
        header = "  stop  " + "".join(
            f"{('none' if t is None else f'{t:.0f}R'):>10}" for t in TARGETS)
        print(header + "     held")
        for mult in STOP_MULTS:
            line = f"  {mult:.0f}x   "
            holds = []
            for target in TARGETS:
                by_day = source[(mult, target)]
                values = [v for d in by_day for v in by_day[d]]
                holds.extend(held_by_target[(mult, target)])
                line += f"{st.mean(values):>+10.2f}" if len(values) >= 40 else f"{'--':>10}"
            line += f"   {st.mean(holds):>5.1f}d" if holds else ""
            print(line)
        print(f"  ({unit})")

    grid(r_by_target, "UNDERLYING, mean R -- R is measured against each row's own stop", "R")
    grid(opt_by_target, f"OPTION, {ITM_PCT:.0f}% ITM {DTE:.0f} DTE, ask in / bid out", "% of premium")

    print("\n  The best cell, by option return:")
    best = None
    for mult in STOP_MULTS:
        for target in TARGETS:
            by_day = opt_by_target[(mult, target)]
            values = [v for d in by_day for v in by_day[d]]
            if len(values) < 40:
                continue
            mean = st.mean(values)
            if best is None or mean > best[0]:
                best = (mean, mult, target, by_day, values)

    if best:
        mean, mult, target, by_day, values = best
        lo, hi = block_ci(by_day)
        stripped = without_top(values)
        wins = sum(1 for v in values if v > 0) / len(values) * 100
        label = "none" if target is None else f"{target:.0f}R"
        print(f"    stop {mult:.0f}x, target {label}: {mean:+.2f}%  "
              f"CI [{lo:+.1f}, {hi:+.1f}]  -top5 {stripped:+.2f}  win {wins:.0f}%  n={len(values)}")

    print("\n  Convexity would show as the mean RISING as you move right and down.")
    print("  If every cell is negative, no combination of risk taken converts it.\n")


if __name__ == "__main__":
    main()
