"""What the price did after each exit fired.

A different question from the one §1.6 settled, and the difference matters.

§1.6 asked "what if the momentum exits were removed" and answered decisively:
holding to stop or target instead costs 18.6R (bull) and 23.8R (bear), because a
dead trade then runs the full distance to its stop. That is settled and this does
not reopen it.

This asks something the archive has never been asked: **when a momentum exit
fires, has the move actually ended, or does it keep going without us?** Those are
not the same question. A rule can be right to cut losses and still be firing
before the trend it is reading has finished, and the fix for that is a
confirmation delay, not removal -- which §1.6 already priced and rejected.

On 2026-08-14, 4 of 5 live trades continued in the traded direction after the
exit: CRWD gave another 1.84R, TSLA 1.23R, SPCX 1.05R, NFLX 0.50R. Five trades is
not evidence -- a stop-floor theory looked decisive on twelve and died on 310 --
so this runs the same question across the whole archive.

Measured per exit, in R against the trade's own risk:

  continuation   how far it travelled our way after we left, from the exit price
  regret         continuation minus what we gave back, i.e. what leaving cost
  reversal       how far it went against us after we left, which is what the
                 exit was for -- reported beside continuation so a rule that
                 saves more than it costs cannot be made to look bad

    python tools/post_exit_continuation.py
    python tools/post_exit_continuation.py --horizon 120 --run phase1_21day_be025

Cached underlying bars only. No option quotes, no network beyond the bar cache.
"""

import argparse
import json
import pathlib
import random
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from app.backtesting.historical_market_data import fetch_bars

DEFAULT_RUN = "phase1_21day_20260803_202603"

# Exits that read momentum rather than a price level. These are the ones under
# test; a hard stop or a target is a level being hit and cannot be "early".
MOMENTUM_MARKERS = ("MACD", "EMA9", "VWAP", "Failed breakout")

_bars = {}


def bars(symbol, day):
    key = (symbol, day)
    if key not in _bars:
        try:
            frame = fetch_bars(symbol, day, day)
            if frame is not None and len(frame):
                frame.index = frame.index.tz_convert("America/New_York")
            _bars[key] = frame
        except Exception:
            _bars[key] = None
    return _bars[key]


def number(value):
    try:
        result = float(value)
        return None if result != result else result
    except (TypeError, ValueError):
        return None


def is_momentum(reason):
    return any(marker in str(reason) for marker in MOMENTUM_MARKERS)


def bootstrap(values, draws=3000):
    if len(values) < 3:
        return None, None
    means = []
    for _ in range(draws):
        sample = [random.choice(values) for _ in values]
        means.append(sum(sample) / len(sample))
    means.sort()
    return means[int(0.025 * draws)], means[int(0.975 * draws)]


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default=DEFAULT_RUN)
    parser.add_argument("--horizon", type=int, default=60,
                        help="minutes after the exit to follow")
    args = parser.parse_args()

    random.seed(17)

    path = pathlib.Path("data/forward_runs") / f"{args.run}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    trades = payload.get("trades") if isinstance(payload, dict) else payload

    rows = []
    skipped = defaultdict(int)

    for trade in trades:

        exit_time = trade.get("exit_time")
        entry = number(trade.get("entry_price"))
        stop = number(trade.get("stop_loss"))

        if not exit_time or entry is None or stop is None:
            skipped["missing geometry"] += 1
            continue

        risk = abs(entry - stop)
        if risk <= 0:
            skipped["zero risk"] += 1
            continue

        try:
            exit_at = pd.Timestamp(exit_time)
            if exit_at.tzinfo is None:
                exit_at = exit_at.tz_localize("America/New_York")
            else:
                exit_at = exit_at.tz_convert("America/New_York")
        except Exception:
            skipped["bad timestamp"] += 1
            continue

        day = exit_at.strftime("%Y-%m-%d")
        frame = bars(trade["symbol"], day)

        if frame is None or not len(frame):
            skipped["no bars"] += 1
            continue

        window = frame[
            (frame.index > exit_at)
            & (frame.index <= exit_at + pd.Timedelta(minutes=args.horizon))
        ]

        if not len(window):
            skipped["no bars after exit"] += 1
            continue

        is_short = str(trade.get("direction", "")).upper() in {"PUT", "SHORT"}

        # The exit price is not recorded, so R at exit is the trade's own
        # r_multiple and the exit price is reconstructed from it. That keeps
        # continuation measured from where the trade actually left.
        booked = number(trade.get("r_multiple")) or 0.0
        exit_price = entry - booked * risk if is_short else entry + booked * risk

        if is_short:
            best = (exit_price - window["Low"].min()) / risk
            worst = (window["High"].max() - exit_price) / risk
        else:
            best = (window["High"].max() - exit_price) / risk
            worst = (exit_price - window["Low"].min()) / risk

        rows.append({
            "symbol": trade["symbol"],
            "reason": trade.get("exit_reason"),
            "momentum": is_momentum(trade.get("exit_reason")),
            "booked": booked,
            "mfe_r": number(trade.get("mfe_r")) or 0.0,
            "continuation": best,
            "reversal": worst,
        })

    print(f"\nrun: {args.run}   horizon: {args.horizon} minutes")
    print(f"exits measured: {len(rows)} of {len(trades)}")
    if skipped:
        print(f"skipped: {dict(skipped)}")

    momentum = [r for r in rows if r["momentum"]]

    if not momentum:
        print("\nno momentum exits found.\n")
        return

    cont = [r["continuation"] for r in momentum]
    rev = [r["reversal"] for r in momentum]
    net = [r["continuation"] - r["reversal"] for r in momentum]

    lo, hi = bootstrap(cont)
    net_lo, net_hi = bootstrap(net)

    print(f"\n=== momentum exits (n = {len(momentum)}) ===\n")
    print(f"  continuation   mean {st.mean(cont):+.3f}R   median {st.median(cont):+.3f}R"
          f"   95% CI [{lo:+.3f}, {hi:+.3f}]")
    print(f"  reversal       mean {st.mean(rev):+.3f}R   median {st.median(rev):+.3f}R")
    print(f"  net (cont-rev) mean {st.mean(net):+.3f}R   median {st.median(net):+.3f}R"
          f"   95% CI [{net_lo:+.3f}, {net_hi:+.3f}]")

    strip = sorted(cont)[:-5]
    print(f"\n  continuation without its best 5: {st.mean(strip):+.3f}R")

    ran_on = sum(1 for r in momentum if r["continuation"] > r["reversal"])
    print(f"  kept going our way more than against: {ran_on}/{len(momentum)}"
          f" = {ran_on / len(momentum) * 100:.0f}%")

    print(f"\n=== by rule ===\n")
    print(f"  {'rule':<34}{'n':>5}{'cont':>9}{'rev':>9}{'net':>9}")

    by_rule = defaultdict(list)
    for row in momentum:
        by_rule[str(row["reason"])].append(row)

    for rule, group in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
        c = st.mean([g["continuation"] for g in group])
        v = st.mean([g["reversal"] for g in group])
        print(f"  {rule:<34}{len(group):>5}{c:>+9.3f}{v:>+9.3f}{c - v:>+9.3f}")

    print(f"\n  Read `net` as: how much further it ran our way than against us")
    print(f"  after we left. Positive means leaving was premature on average;")
    print(f"  negative means the rule got us out before it turned.\n")


if __name__ == "__main__":
    main()
