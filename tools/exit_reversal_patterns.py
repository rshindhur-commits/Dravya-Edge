"""Can a real reversal pattern end the trade, rather than a P&L threshold?

The earlier trend test was narrower than it was described. It ran EMA9 immediate,
EMA9 confirmed at one, two and three bars, and EMA20 -- six variations of a single
idea, moving-average crossing, reported as though it settled "trend exits". It
did not. A moving-average cross is the weakest reversal signal there is: on a
five-minute chart price crosses its EMA constantly inside a live trend.

The operator's requirement is an exit that fires when the move genuinely turns,
at any profit level, rather than a floor keyed to how much has been made. That
deserves a fair test with real patterns.

## The patterns

Read on the UNDERLYING, mirrored for shorts, all measured on the 5m frame except
where stated.

    swing_break     price takes out the most recent swing low -- structure,
                    not an average. The classic definition of a trend ending.
    lower_low       a bar makes a lower low AND a lower close than the prior
                    three bars. Weaker than a swing break, fires sooner.
    volume_flush    a bar closes against the position on more than 1.5x average
                    volume with a range over 1 ATR -- conviction, not drift.
    engulfing       the bar's body swallows the previous bar's, against us
    vwap_loss       close crosses the session VWAP against the position
    htf_ema         EMA9 cross on the 15-minute frame rather than the 5-minute,
                    which is the same idea as before with the noise removed

Each is compared against the two-tier P&L rule and against the book. The hard
stop is active on every arm, so none of them is being judged on whether it
prevents a large loss -- only on whether it protects a gain.

## What would make a pattern worth having

It has to beat the P&L rule on **gave-it-all-back** -- the share of trades that
were up 10% or more and finished at or below zero -- without destroying
**kept>=25%**, the share of trades reaching +25% that were still held at +25%.

A pattern that fires early scores well on the first and badly on the second. The
P&L rule sits at 29% and 45%. That is the bar.

    python tools/exit_reversal_patterns.py

Real option bars for the live trades. Run outside 09:30-16:00 ET.
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

ARM, KEEP, BE, STOP_ATR = 25.0, 0.5, 10.0, 1.5
PATTERNS = ["swing_break", "lower_low", "volume_flush", "engulfing",
            "vwap_loss", "htf_ema"]
COMBOS = ["volume_flush", "engulfing", "vwap_loss"]
ARMS = (["ACTUAL", "two-tier P&L"] + PATTERNS
        + ["%s + P&L" % c for c in COMBOS])


def number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result


def prepare(frame):
    out = frame.copy()
    close = out["Close"]
    typical = (out["High"] + out["Low"] + close) / 3.0
    volume = out["Volume"].replace(0, pd.NA).ffill().fillna(1.0)
    out["vwap"] = (typical * volume).cumsum() / volume.cumsum()
    span = pd.concat([
        out["High"] - out["Low"],
        (out["High"] - close.shift()).abs(),
        (out["Low"] - close.shift()).abs(),
    ], axis=1).max(axis=1)
    out["atr"] = span.ewm(span=14, adjust=False).mean()
    out["avgvol"] = out["Volume"].rolling(20, min_periods=5).mean()
    fifteen = close.resample("15min").last().ewm(span=9, adjust=False).mean()
    out["ema15"] = fifteen.reindex(out.index, method="ffill")
    return out


def fired(name, frame, position, is_call):
    """Has this reversal pattern printed on the bar at `position`?"""

    if position < 4:
        return False

    bar = frame.iloc[position]
    prior = frame.iloc[position - 1]
    window = frame.iloc[max(0, position - 4):position]

    close = number(bar["Close"])
    low, high = number(bar["Low"]), number(bar["High"])
    open_ = number(bar["Open"])
    if None in (close, low, high, open_):
        return False

    if name == "swing_break":
        # The lowest low of the last ten bars, excluding the current one.
        recent = frame.iloc[max(0, position - 10):position]
        if not len(recent):
            return False
        level = number(recent["Low"].min()) if is_call else number(recent["High"].max())
        return (low < level) if is_call else (high > level)

    if name == "lower_low":
        prior_low = number(window["Low"].min())
        prior_high = number(window["High"].max())
        prior_close = number(prior["Close"])
        if None in (prior_low, prior_high, prior_close):
            return False
        if is_call:
            return low < prior_low and close < prior_close
        return high > prior_high and close > prior_close

    if name == "volume_flush":
        volume = number(bar["Volume"]) or 0
        average = number(bar["avgvol"]) or 1.0
        atr = number(bar["atr"]) or 0
        against = close < open_ if is_call else close > open_
        return (against and volume > 1.5 * average
                and atr > 0 and (high - low) > atr)

    if name == "engulfing":
        prior_open, prior_close = number(prior["Open"]), number(prior["Close"])
        if prior_open is None or prior_close is None:
            return False
        if is_call:
            return (close < open_ and prior_close > prior_open
                    and close < prior_open and open_ > prior_close)
        return (close > open_ and prior_close < prior_open
                and close > prior_open and open_ < prior_close)

    if name == "vwap_loss":
        vwap = number(bar["vwap"])
        prior_close = number(prior["Close"])
        if vwap is None or prior_close is None:
            return False
        if is_call:
            return close < vwap and prior_close >= vwap
        return close > vwap and prior_close <= vwap

    if name == "htf_ema":
        ema = number(bar["ema15"])
        if ema is None:
            return False
        return (close < ema) if is_call else (close > ema)

    return False


def main():

    with get_engine().begin() as connection:
        rows = connection.execute(text("""
            SELECT symbol, direction, option_ticker, entry_price, option_entry_mid,
                   option_close_mid, pnl_pct, days_held, opened_at
            FROM paper_trades
            WHERE status='CLOSED' AND option_ticker IS NOT NULL
              AND option_entry_mid > 0
            ORDER BY opened_at
        """)).mappings().all()

    results = {a: [] for a in ARMS}
    peaks = []

    for record in rows:

        if (number(record["days_held"]) or 1) > 1:
            continue
        paid = number(record["option_entry_mid"])
        day = str(pd.Timestamp(record["opened_at"]).tz_convert("America/New_York").date())

        try:
            option = fetch_bars(record["option_ticker"], day, day, multiplier=5, timespan="minute")
            under = fetch_bars(record["symbol"], day, day)
        except Exception:
            continue
        if option is None or not len(option) or under is None or not len(under):
            continue

        option = option.copy()
        option.index = pd.to_datetime(option.index, utc=True).tz_convert("America/New_York")
        option = option.between_time("09:30", "16:00")
        under = under.copy()
        under.index = under.index.tz_convert("America/New_York")
        under = prepare(under.between_time("09:30", "16:00"))

        opened = pd.Timestamp(record["opened_at"]).tz_convert("America/New_York")
        forward = option[option.index >= opened]
        if len(forward) < 4:
            continue

        before = under[under.index <= opened]
        if not len(before):
            continue
        atr = number(before["atr"].iloc[-1])
        entry_spot = number(record["entry_price"])
        is_call = str(record["direction"] or "").upper() == "CALL"
        hard = ((entry_spot - STOP_ATR * atr if is_call else entry_spot + STOP_ATR * atr)
                if (entry_spot and atr) else None)

        closed = number(record["option_close_mid"])
        results["ACTUAL"].append(((closed - paid) / paid * 100.0) if closed
                                 else (number(record["pnl_pct"]) or 0.0))
        peaks.append(max((number(forward["High"].max()) - paid) / paid * 100.0, -100.0))

        index_of = {ts: i for i, ts in enumerate(under.index)}

        for arm in ARMS[1:]:
            peak, out = -100.0, None
            for timestamp, bar in forward.iterrows():
                high, close = number(bar["High"]), number(bar["Close"])
                if high is None or close is None:
                    continue

                window = under[(under.index >= opened) & (under.index <= timestamp)]
                if hard is not None and len(window):
                    low_u, high_u = number(window["Low"].iloc[-1]), number(window["High"].iloc[-1])
                    if low_u is not None and high_u is not None:
                        if (low_u <= hard) if is_call else (high_u >= hard):
                            out = (close - paid) / paid * 100.0
                            break

                peak = max(peak, (high - paid) / paid * 100.0)
                gain = (close - paid) / paid * 100.0

                if arm in PATTERNS or arm.endswith(" + P&L"):
                    name = arm if arm in PATTERNS else arm.replace(" + P&L", "")
                    nearest = under.index[under.index <= timestamp]
                    if len(nearest):
                        position = index_of.get(nearest[-1])
                        if position is not None and fired(name, under, position, is_call):
                            out = gain
                            break

                if arm == "two-tier P&L" or arm.endswith(" + P&L"):
                    floor = (peak * KEEP if peak >= ARM
                             else (0.0 if peak >= BE else None))
                    if floor is not None and gain <= floor:
                        out = gain
                        break

            if out is None:
                out = (number(forward["Close"].iloc[-1]) - paid) / paid * 100.0
            results[arm].append(out)

    n = len(peaks)
    big = sum(1 for p in peaks if p >= 25.0)
    print("")
    print("  %d live trades, real option bars, hard stop on every arm" % n)
    print("  patterns read on the underlying; mirrored for puts")
    print("")
    print("  %-22s%9s%9s%10s%7s%16s%11s"
          % ("arm", "mean", "-top5", "total", "win", "gave it all back", "kept>=25%"))
    print("  " + "-" * 86)
    for arm in ARMS:
        values = results[arm]
        if not values:
            continue
        green = [i for i, p in enumerate(peaks) if p >= 10.0]
        back = sum(1 for i in green if values[i] <= 0) / len(green) * 100 if green else 0
        kept = sum(1 for i, p in enumerate(peaks) if p >= 25.0 and values[i] >= 25.0)
        print("  %-22s%+8.2f%%%+8.2f%%%+9.1f%%%6.0f%%%15.0f%%%10.0f%%"
              % (arm, st.mean(values), st.mean(sorted(values)[:-5]), sum(values),
                 sum(1 for v in values if v > 0) / len(values) * 100,
                 back, kept / max(big, 1) * 100))

    print("")
    print("  The bar is the P&L rule: 29%% gave it all back, 45%% of big winners kept.")
    print("  A pattern is worth having only if it beats both, not one.")
    print("")


if __name__ == "__main__":
    main()
