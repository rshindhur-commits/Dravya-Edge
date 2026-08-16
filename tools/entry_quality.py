"""How often does our entry hand the subscriber a tradeable gain?

This measures the product as it is actually sold, which earlier work did not.

Every previous measurement scored a **fixed entry-and-exit policy** and reported
its average return. That answers "what would the app earn trading its own book."
It does not answer the question the business rests on:

    the app gives an entry
    the subscriber takes profit at their own level -- 10%, 20%, 50%
    the app only signals an exit when the trade is going against them

Under that design the round-trip cost is close to irrelevant. It is a fixed drag
of a couple of percent on an instrument the subscriber is holding for twenty or
more, and averaging it against a rule-based exit understates the product badly.

## What is measured instead

For every candidate, the option is bought at the **ask** and then tracked forward
bar by bar. The number that matters is how high the **bid** gets -- what the
subscriber could actually have sold into -- before the protective stop is hit.

    reach +10%   could the subscriber have taken 10% off the table
    reach +20%   ... 20%
    reach +50%   ... 50%
    stopped      the protective exit fired with no such chance offered

A trade counts as reaching a level only if the level is available **before** the
stop triggers. Later highs do not count, because the subscriber is already out.

## The control that decides whether the entry is any good

**Random entry, same day, same symbol, same contract, same stop distance.**

This is the whole point. Options are volatile, so *any* entry reaches +20%
sometimes. A signal is only worth paying for if it reaches these levels **more
often than a coin-toss entry on the same instrument**. Without this comparison
a high hit rate proves nothing except that options move.

    python tools/entry_quality.py

Archive only, no network beyond the cached bars.
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

RATE = 0.04
LEVELS = [10.0, 20.0, 30.0, 50.0, 100.0]
RANDOM_DRAWS = 5
BOOTSTRAP = 2000


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
            WHERE jsonb_typeof(s.decision_payload->'Option Liquidity Attempts') = 'string'
              AND s.decision_payload->>'Candidate Entry Price' IS NOT NULL
              AND s.decision_payload->>'Candidate Stop Price' IS NOT NULL
              AND s.decision_payload->>'Candidate Direction' IS NOT NULL
            ORDER BY s.symbol, s.trading_day, s.scan_timestamp
        """)).mappings().all()


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


def usable(contract):
    need = ("strike", "dte", "iv", "bid", "ask", "spread_pct")
    values = {k: number(contract.get(k)) for k in need}
    if any(v is None for v in values.values()):
        return None
    if values["ask"] <= 0 or values["bid"] <= 0 or values["iv"] <= 0:
        return None
    if not (0 < values["spread_pct"] < 50):
        return None
    if str(contract.get("quote_status") or "QUOTE_OK") != "QUOTE_OK":
        return None
    values["mid"] = (values["bid"] + values["ask"]) / 2.0
    if values["mid"] <= 0.05:
        return None
    values["oi"] = number(contract.get("open_interest")) or 0
    values["volume"] = number(contract.get("volume")) or 0
    return values


def peak_before_stop(forward, contract, entry, stop, is_call):
    """Best sellable gain %, and whether the stop ended it.

    The option is bought at the ask. Its value is tracked with Black-Scholes
    supplying only the *ratio* to the recorded quote, and what the subscriber
    could sell into is the bid, so the spread is charged on the way out too.
    """

    years_in = max(contract["dte"], 0.5) / 365.0
    theo_in = bs(entry, contract["strike"], years_in, contract["iv"], is_call)
    if theo_in <= 0.05:
        return None, None

    paid = contract["ask"]
    half = contract["spread_pct"] / 200.0
    best = -100.0
    minutes = 0.0
    start = forward.index[0]

    for timestamp, bar in forward.iterrows():

        high, low = number(bar["High"]), number(bar["Low"])
        close = number(bar["Close"])
        if high is None or low is None or close is None:
            continue

        minutes = (timestamp - start).total_seconds() / 60.0

        # The best underlying price reached inside this bar, in our direction.
        favourable = high if is_call else low
        years_out = max(contract["dte"] - minutes / 390.0, 0.2) / 365.0
        theo_out = bs(favourable, contract["strike"], years_out, contract["iv"], is_call)
        sellable = contract["mid"] * (theo_out / theo_in) * (1 - half)
        best = max(best, (sellable - paid) / paid * 100.0)

        # The protective exit. Checked after the bar's favourable excursion,
        # which is generous by exactly one bar and is the honest direction to
        # err in when intrabar order is unknowable.
        if (low <= stop) if is_call else (high >= stop):
            return best, True

    return best, False


def summarise(label, peaks, stopped):
    if len(peaks) < 30:
        return f"  {label:<20}{len(peaks):>7}   too few"
    cells = "".join(
        f"{sum(1 for p in peaks if p >= level) / len(peaks) * 100:>9.0f}%"
        for level in LEVELS)
    median = st.median(peaks)
    return (f"  {label:<20}{len(peaks):>7}{cells}{median:>+10.1f}%"
            f"{sum(stopped) / len(stopped) * 100:>9.0f}%")


def main():

    random.seed(61)
    rows = load()

    signal_peaks, signal_stopped = [], []
    random_peaks, random_stopped = [], []
    by_day = defaultdict(list)
    rnd_by_day = defaultdict(list)

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

        forward = frame[frame.index > at]
        if len(forward) < 5:
            continue

        is_call = direction == "CALL"
        peak, was_stopped = peak_before_stop(forward, contract, entry, stop, is_call)
        if peak is None:
            continue
        signal_peaks.append(peak)
        signal_stopped.append(was_stopped)
        by_day[day].append(peak)

        # The control: same day, same contract, same stop distance, entry moment
        # chosen at random -- but given **the same number of forward bars** as
        # the signal it is paired with.
        #
        # Without that match the control is unfair in its own favour: signals
        # arrive later in the session, so a control drawn from anywhere would
        # hold more remaining time and reach any given level more often for that
        # reason alone. The horizon has to be identical for the comparison to be
        # about entry timing rather than about clock left on the day.
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
            if len(window) < 5:
                continue
            rpeak, rstopped = peak_before_stop(window, contract,
                                               other_entry, other_stop, is_call)
            if rpeak is None:
                continue
            random_peaks.append(rpeak)
            random_stopped.append(rstopped)
            rnd_by_day[day].append(rpeak)

    print(f"\n  candidates measured : {len(signal_peaks)}")
    print(f"  random controls     : {len(random_peaks)}  "
          f"({RANDOM_DRAWS} per candidate, same contract and stop distance)\n")

    if len(signal_peaks) < 50:
        print("  too few; stopping.\n")
        return

    header = "".join(f"{f'+{int(l)}%':>10}" for l in LEVELS)
    print(f"  {'arm':<20}{'n':>7}{header}{'median':>10}{'stopped':>10}")
    print(f"  {'':-<95}")
    print(summarise("app entry", signal_peaks, signal_stopped))
    print(summarise("random entry", random_peaks, random_stopped))

    print("\n  Read the +20% column. That is the fraction of signals that gave a")
    print("  subscriber a real chance to take 20% off the table before the")
    print("  protective exit fired. It is only worth anything if it beats the")
    print("  random row, because options reach 20% on their own sometimes.\n")

    for level in LEVELS:
        ours = sum(1 for p in signal_peaks if p >= level) / len(signal_peaks) * 100
        theirs = sum(1 for p in random_peaks if p >= level) / len(random_peaks) * 100
        edge = ours - theirs
        verdict = "app better" if edge > 0 else "random better"
        print(f"    +{int(level):>3}%   app {ours:5.1f}%   random {theirs:5.1f}%   "
              f"difference {edge:+5.1f} points   {verdict}")

    print(f"\n  median peak, app {st.median(signal_peaks):+.1f}%   "
          f"random {st.median(random_peaks):+.1f}%")
    print(f"  stopped out, app {sum(signal_stopped) / len(signal_stopped) * 100:.0f}%   "
          f"random {sum(random_stopped) / len(random_stopped) * 100:.0f}%\n")


if __name__ == "__main__":
    main()
