"""Every recorded trade, replayed under the rules shipped on 2026-08-16.

The whole book rather than a day of it. Each trade is priced on **the option
contract it actually traded**, using that contract's own 5-minute bars, so no
Black-Scholes and no synthetic instrument appears anywhere in the result.

## What is applied

    entry cutoff      a trade opened after 14:05 ET does not happen
    orphan close      an INTRADAY position is closed at its own session end
                      (shipped 2026-08-14 as 2dcc57f, before this work)
    hard stop         1.5 ATR on the underlying, active on every trade
    volume flush      a bar closing against the position on >1.5x its own
                      average volume with a range over 1 ATR
    profit floor      up 10% it may not go red; up 25% it keeps half its peak

## What is NOT applied, and why

**The contract preference.** It changes *which* contract is bought, so replaying
it would need bars for a contract this trade never held. Its measured effect is
on how many candidates become tradeable rather than on the trades already taken,
where all six selection rules land within 0.8 points of each other. Leaving it
out understates nothing here.

## How to read the total

The orphan close is separated out. It is worth more than everything else
combined, it is a **bug fix rather than a strategy change**, and it shipped two
days before the rules being tested. Folding it into the headline would credit
this work with someone else's repair.

    python tools/replay_new_rules.py

Run outside 09:30-16:00 ET.
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

CUTOFF_MINUTES = 14 * 60 + 5
ARM, KEEP, BE = 25.0, 0.5, 10.0
FLUSH_MULT, STOP_ATR = 1.5, 1.5


def number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result


def prepare(frame):
    out = frame.copy()
    close = out["Close"]
    span = pd.concat([
        out["High"] - out["Low"],
        (out["High"] - close.shift()).abs(),
        (out["Low"] - close.shift()).abs(),
    ], axis=1).max(axis=1)
    out["atr"] = span.ewm(span=14, adjust=False).mean()
    out["avgvol"] = out["Volume"].rolling(20, min_periods=5).mean()
    return out


def bars(ticker, day, multiplier, span):
    try:
        frame = fetch_bars(ticker, day, day, multiplier=multiplier, timespan=span)
    except Exception:
        return None
    if frame is None or not len(frame):
        return None
    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index, utc=True).tz_convert("America/New_York")
    return frame.between_time("09:30", "16:00")


def replay(option, under, paid, is_call, hard):
    """(final gain %, peak %, reason, timestamp) under the shipped exit rules."""

    peak = -100.0

    for timestamp, bar in option.iterrows():

        high, close = number(bar["High"]), number(bar["Close"])
        if high is None or close is None:
            continue

        window = under[under.index <= timestamp]

        if len(window):

            low_u, high_u = number(window["Low"].iloc[-1]), number(window["High"].iloc[-1])
            if hard is not None and low_u is not None and high_u is not None:
                if (low_u <= hard) if is_call else (high_u >= hard):
                    return (close - paid) / paid * 100.0, peak, "hard stop", timestamp

            row = window.iloc[-1]
            values = [number(row[k]) for k in ("Close", "Open", "High", "Low", "Volume", "atr", "avgvol")]
            if None not in values:
                bar_close, bar_open, bar_high, bar_low, volume, atr, average = values
                against = (bar_close < bar_open) if is_call else (bar_close > bar_open)
                if (against and atr > 0 and average > 0
                        and (bar_high - bar_low) > atr
                        and volume > FLUSH_MULT * average):
                    return ((close - paid) / paid * 100.0, peak,
                            "reversal: volume flush", timestamp)

        peak = max(peak, (high - paid) / paid * 100.0)
        gain = (close - paid) / paid * 100.0
        floor = peak * KEEP if peak >= ARM else (0.0 if peak >= BE else None)

        if floor is not None and gain <= floor:
            label = "floor: half of peak" if peak >= ARM else "floor: breakeven"
            return gain, peak, label, timestamp

    last = option.index[-1]
    return (number(option["Close"].iloc[-1]) - paid) / paid * 100.0, peak, "session close", last


def main():

    with get_engine().begin() as connection:
        rows = connection.execute(text("""
            SELECT symbol, direction, option_ticker, entry_price, option_entry_mid,
                   option_close_mid, pnl_pct, days_held, opened_at,
                   payload->>'initial_stop_loss' AS stop_loss,
                   payload->>'exit_reason' AS reason
            FROM paper_trades
            WHERE status='CLOSED' AND option_ticker IS NOT NULL
              AND option_entry_mid > 0
            ORDER BY opened_at
        """)).mappings().all()

    print(f"\n  {len(rows)} closed trades with a traded option contract")
    print(f"  priced on that contract's own 5-minute bars\n")
    print(f"  {'symbol':<6}{'date':<11}{'in':<6}{'actual':>9}{'peak':>8}"
          f"{'why out':<24}{'new':>9}{'change':>9}")
    print(f"  {'':-<82}")

    actual_total = new_total = 0.0
    orphan_total = 0.0
    blocked = kept = 0
    changes = []
    reasons = {}

    for record in rows:

        paid = number(record["option_entry_mid"])
        opened = pd.Timestamp(record["opened_at"]).tz_convert("America/New_York")
        day = str(opened.date())
        closed = number(record["option_close_mid"])
        actual = ((closed - paid) / paid * 100.0 * paid) if closed else 0.0
        actual = ((closed - paid) * 100.0) if closed else (number(record["pnl_pct"]) or 0.0)

        multiday = (number(record["days_held"]) or 1) > 1

        if multiday:
            # The orphan close ends it at its own session end instead of days
            # later. Reported separately: it is a bug fix that shipped first.
            orphan_total += actual
            print(f"  {record['symbol']:<6}{day:<11}{opened:%H:%M} {actual:>+9.2f}"
                  f"{'--':>8}{'ORPHAN — force-closed now':<24}{'':>9}{'':>9}")
            continue

        minutes = opened.hour * 60 + opened.minute
        actual_total += actual

        if minutes > CUTOFF_MINUTES:
            blocked += 1
            changes.append(-actual)
            print(f"  {record['symbol']:<6}{day:<11}{opened:%H:%M} {actual:>+9.2f}"
                  f"{'--':>8}{'BLOCKED after 14:05':<24}{0.0:>+9.2f}{-actual:>+9.2f}")
            continue

        option = bars(record["option_ticker"], day, 5, "minute")
        under = bars(record["symbol"], day, 5, "minute")
        if option is None or under is None:
            continue
        under = prepare(under)
        forward = option[option.index >= opened]
        if len(forward) < 3:
            continue

        is_call = str(record["direction"] or "").upper() == "CALL"

        # The trade's OWN stop, as it was set at entry. An earlier version of
        # this used a synthetic 1.5 ATR level, which is not the stop any of
        # these trades actually carried -- ten of thirty-one exit here, so the
        # difference is not cosmetic.
        hard = number(record["stop_loss"])
        if hard is None:
            before = under[under.index <= opened]
            atr = number(before["atr"].iloc[-1]) if len(before) else None
            entry_spot = number(record["entry_price"])
            hard = ((entry_spot - STOP_ATR * atr if is_call else entry_spot + STOP_ATR * atr)
                    if (entry_spot and atr) else None)

        gain, peak, why, when = replay(forward, under[under.index >= opened],
                                       paid, is_call, hard)
        new_dollars = gain / 100.0 * paid * 100.0
        new_total += new_dollars
        kept += 1
        changes.append(new_dollars - actual)
        reasons[why] = reasons.get(why, 0) + 1

        print(f"  {record['symbol']:<6}{day:<11}{opened:%H:%M} {actual:>+9.2f}"
              f"{peak:>+7.1f}%{why:<24}{new_dollars:>+9.2f}{new_dollars - actual:>+9.2f}")

    print(f"  {'':-<82}")
    print(f"\n  INTRADAY TRADES  ({kept} traded, {blocked} blocked by the cutoff)")
    print(f"    the book as it ran        {actual_total:>+10.2f}")
    print(f"    under the new rules       {new_total:>+10.2f}")
    print(f"    difference                {new_total - actual_total:>+10.2f}")

    if changes:
        better = sum(1 for c in changes if c > 0)
        print(f"\n    better on {better} trades, worse on {len(changes) - better}")
        print(f"    median change per trade   {st.median(changes):>+10.2f}")
        strip = sorted(changes)[:-3]
        if strip:
            print(f"    without the best 3        {sum(strip):>+10.2f}  "
                  f"(total change {sum(changes):+.2f})")

    print(f"\n  WHAT ENDED EACH TRADE")
    for why, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"    {why:<26}{count:>4}")

    print(f"\n  MULTI-DAY POSITIONS, reported separately")
    print(f"    booked                    {orphan_total:>+10.2f}")
    print(f"    These were INTRADAY trades left open for days. The force-close")
    print(f"    shipped 2026-08-14 as 2dcc57f, before the rules tested here, so")
    print(f"    it is a bug fix rather than a result of this work.\n")


if __name__ == "__main__":
    main()
