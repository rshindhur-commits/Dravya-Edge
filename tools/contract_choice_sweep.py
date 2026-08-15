"""Same signal, different contract. The last untested lever inside options.

Every experiment so far has varied the signal and held the contract fixed. The
contract the app actually buys is the most hostile one on the board: median 9 DTE
and 2.3% out of the money, which is maximum theta, maximum spread as a share of
premium, and low delta -- so it gives back the most and captures the least of a
move the signal got right.

The gap that matters is not the edge. It is +0.134R of signal against a round
trip that needs about +0.40R. The numerator has now been attacked from five
directions and will not move. This attacks the denominator.

No API calls. `scanner_snapshot` stores the whole chain the selector examined at
each decision -- ticker, strike, expiry, DTE, bid, ask, delta, IV -- so the
alternatives can be priced from what is already on disk.

Method:

  1  take each candidate with a full chain and a resolvable outcome
  2  walk the underlying to its stop or target, which fixes the exit price and
     the time held
  3  reprice every contract in that chain at entry and at exit with
     Black-Scholes, using the IV recorded at entry
  4  charge the recorded spread at both ends -- buy the ask, sell the bid
  5  group the contracts into arms by DTE and moneyness and compare

The levels are model prices and are not tradeable numbers. IV is held constant
when in reality it moves, and the exit spread is assumed to match the entry
spread. Both approximations fall on every arm equally, so the *comparison between
arms* is the output; the absolute returns are not.

    python tools/contract_choice_sweep.py

Cached bars and stored chains. No network.
"""

import json
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
from tools.inverted_trigger import number, walk

RATE = 0.045
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


def arm_of(dte, moneyness_pct):
    """Bucket a contract by tenor and moneyness. `moneyness_pct` > 0 is OTM."""

    tenor = "0-10d" if dte <= 10 else "11-25d" if dte <= 25 else "26d+"

    if moneyness_pct > 1.0:
        money = "OTM"
    elif moneyness_pct < -2.0:
        money = "ITM"
    else:
        money = "ATM"

    return f"{tenor} {money}"


def load():
    with get_engine().begin() as connection:
        return connection.execute(text("""
            SELECT symbol, trading_day, scan_timestamp,
                   decision_payload->>'Option Liquidity Attempts' AS chain,
                   decision_payload AS p
            FROM scanner_snapshot
            WHERE jsonb_typeof(decision_payload->'Option Liquidity Attempts') = 'string'
              AND decision_payload->>'Candidate Entry Price' IS NOT NULL
              AND decision_payload->>'Candidate Stop Price' IS NOT NULL
              AND decision_payload->>'Candidate Target Price' IS NOT NULL
              AND decision_payload->>'Candidate Direction' IS NOT NULL
            ORDER BY scan_timestamp
        """)).mappings().all()


def main():

    random.seed(23)
    rows = load()
    print(f"\n  snapshots with a stored chain: {len(rows)}")

    arms = defaultdict(list)
    days_seen = defaultdict(set)
    baseline = []
    used = 0

    for row in rows:

        p = row["p"] or {}
        entry = number(p.get("Candidate Entry Price"))
        stop = number(p.get("Candidate Stop Price"))
        target = number(p.get("Candidate Target Price"))
        direction = str(p.get("Candidate Direction") or "").upper()

        if None in (entry, stop, target) or direction not in {"CALL", "PUT"}:
            continue

        try:
            chain = json.loads(row["chain"] or "[]")
        except Exception:
            continue
        if not chain:
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
        if len(forward) < 5:
            continue

        is_short = direction == "PUT"
        is_call = not is_short
        risk = abs(entry - stop)
        if risk <= 0:
            continue

        # Where and when the underlying trade ends.
        exit_price, held_minutes = None, None
        for ts, bar in forward.iterrows():
            hit_stop = bar["High"] >= stop if is_short else bar["Low"] <= stop
            hit_target = bar["Low"] <= target if is_short else bar["High"] >= target
            if hit_stop:
                exit_price, held_minutes = stop, (ts - at).total_seconds() / 60
                break
            if hit_target:
                exit_price, held_minutes = target, (ts - at).total_seconds() / 60
                break
        if exit_price is None:
            exit_price = float(forward["Close"].iloc[-1])
            held_minutes = (forward.index[-1] - at).total_seconds() / 60

        r_under = ((entry - exit_price) if is_short else (exit_price - entry)) / risk
        baseline.append(r_under)
        used += 1
        day = str(row["trading_day"])

        for contract in chain:

            strike = number(contract.get("strike"))
            dte = number(contract.get("dte"))
            iv = number(contract.get("iv"))
            bid = number(contract.get("bid"))
            ask = number(contract.get("ask"))

            if None in (strike, dte, iv, bid, ask) or iv <= 0 or ask <= 0:
                continue
            if (contract.get("ticker") or "").endswith("C") != is_call and \
               ("C0" in str(contract.get("ticker"))) != is_call:
                pass          # ticker side is unreliable; direction comes from the signal

            years_in = max(dte, 0.5) / 365.0
            years_out = max(dte - held_minutes / 390.0, 0.2) / 365.0

            theo_in = bs(entry, strike, years_in, iv, is_call)
            theo_out = bs(exit_price, strike, years_out, iv, is_call)

            if theo_in <= 0.05:
                continue

            # The recorded spread, charged at both ends, applied to the model
            # price so the arms differ only by the contract chosen.
            spread = number(contract.get("spread_pct")) or 0.0
            half = spread / 200.0
            fill_in = theo_in * (1 + half)
            fill_out = theo_out * (1 - half)

            ret = (fill_out - fill_in) / fill_in * 100.0

            moneyness = (strike - entry) / entry * 100.0
            if not is_call:
                moneyness = (entry - strike) / entry * 100.0

            arm = arm_of(dte, moneyness)
            arms[arm].append(ret)
            days_seen[arm].add(day)

    if used < 50:
        print(f"  only {used} usable candidates; stopping.\n")
        return

    print(f"  candidates priced            : {used}")
    print(f"  mean underlying outcome      : {st.mean(baseline):+.3f}R\n")

    def boot(values):
        out = []
        for _ in range(2000):
            out.append(sum(random.choice(values) for _ in values) / len(values))
        out.sort()
        return out[50], out[1950]

    print(f"  {'contract arm':<16}{'n':>7}{'mean %':>10}{'median':>9}"
          f"{'no top 5':>10}{'win%':>7}{'95% CI':>20}")
    print(f"  {'-' * 79}")

    ranked = sorted(arms.items(), key=lambda kv: -st.mean(kv[1]))

    for arm, values in ranked:
        if len(values) < 60:
            continue
        lo, hi = boot(values)
        s = sorted(values)
        flag = "  <==" if lo > 0 else ""
        print(f"  {arm:<16}{len(values):>7}{st.mean(values):>+10.2f}{st.median(values):>+9.2f}"
              f"{st.mean(s[:-5]):>+10.2f}"
              f"{sum(1 for v in values if v > 0) / len(values) * 100:>6.0f}%"
              f"   [{lo:+.2f}, {hi:+.2f}]{flag}")

    best = next((a for a, v in ranked if len(v) >= 60), None)

    print()
    if best:
        lo, _hi = boot(arms[best])
        current = next((a for a in arms if a.startswith("0-10d") and a.endswith("OTM")), None)
        if current and current in arms:
            print(f"  the app currently buys   : {current}"
                  f"  at {st.mean(arms[current]):+.2f}%")
        print(f"  best arm                 : {best}  at {st.mean(arms[best]):+.2f}%")
        if lo > 0:
            print("\n  A contract arm is positive with its interval clear of zero.")
            print("  Re-price it against live chains before changing the selector.\n")
        else:
            print("\n  No contract arm is positive with its interval clear of zero.")
            print("  Contract choice does not rescue a signal this size, which")
            print("  closes the last untested lever inside options.\n")


if __name__ == "__main__":
    main()
