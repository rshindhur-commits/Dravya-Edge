"""What the same signals would have earned at randomly chosen moments.

Every number this project has produced compares a strategy against zero. Zero is
the wrong benchmark. If the market rose over the measurement window and most
candidates were long, a positive sign-adjusted return is beta and nothing else --
and the first cut of the 21-session labels looked exactly like that: +0.69% on
the holdout, where longs made +1.48% and shorts lost -0.15%, in a period the
market rose.

A benchmark computed from the candidates themselves is circular, which is the
trap the first attempt fell into. So the null here holds everything constant
except the one thing under test:

  **timing null** -- same symbol, same session, same direction, same horizon,
  random entry minute inside the entry window. Isolates whether *when* the
  scanner fires carries information. This is the primary question: the app's
  claim is that it identifies moments, not that it picks stocks.

  **direction null** -- same symbol, same moment, direction flipped. Isolates
  whether the long/short call carries information, independent of drift, since
  flipping the sign flips the exposure to the market too.

**The absolute levels this prints are not achievable returns, and must never be
quoted as one.** The random-timing arm reuses the symbol and direction the
scanner chose at some point in that session, and applies them at an earlier
minute -- which is lookahead. Nobody knows at 10:00 that the scanner will fire
NVDA short at 14:00.

The *difference* is still clean, and that is what the tool is for: both arms
carry exactly the same contaminated direction information, so it cancels, and
what is left measures the one thing that differs -- the minute chosen. Read the
"edge over random" line. Ignore the levels.

Both run off cached 5m bars. Minutes, no quota.

    python tools/null_model.py research/candidates_21day.json --draws 20
"""

import argparse
import json
import pathlib
import random
import statistics as st
import sys
import warnings

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=FutureWarning, module="app.indicators.*")

load_dotenv()

from app.backtesting.historical_market_data import (
    HistoricalDataError,
    load_replay_frames,
)
from app.research import holdout

ET = "America/New_York"
ENTRY_START = "09:45"
ENTRY_END = "15:30"


def continuous_5m(symbol, days, lookback_days=5):

    frames = []

    for day in days:

        try:

            raw = load_replay_frames(symbol, day, lookback_days=lookback_days)

        except HistoricalDataError:

            continue

        if raw is not None and not raw.empty:

            frames.append(raw)

    if not frames:

        return None

    joined = pd.concat(frames)

    return joined[~joined.index.duplicated(keep="first")].sort_index()


def forward_move(series, moment, horizon, is_short):
    """Sign-adjusted forward move as % of the price at ``moment``."""

    at_or_before = series[series.index <= moment]
    forward = series[series.index > moment]

    if at_or_before.empty or forward.empty:

        return None

    entry = float(at_or_before["Close"].iloc[-1])

    if entry <= 0:

        return None

    window = forward.iloc[:horizon]

    if window.empty:

        return None

    closing = float(window["Close"].iloc[-1])
    sign = -1.0 if is_short else 1.0

    return sign * (closing - entry) / entry * 100.0


def session_moments(series, day):
    """Every 5m timestamp inside the entry window for that session."""

    day_bars = series[series.index.tz_convert(ET).date == pd.Timestamp(day).date()]

    if day_bars.empty:

        return []

    local = day_bars.index.tz_convert(ET)
    mask = (
        (local.time >= pd.Timestamp(ENTRY_START).time())
        & (local.time <= pd.Timestamp(ENTRY_END).time())
    )

    return list(day_bars.index[mask])


def run(rows, horizon, draws, seed=11):

    rng = random.Random(seed)

    days = sorted({str(r["day"]) for r in rows})
    symbols = sorted({r["symbol"] for r in rows})

    series_by_symbol = {}

    for symbol in symbols:

        series_by_symbol[symbol] = continuous_5m(symbol, days)

    actual = []
    flipped = []
    timing_draws = [[] for _ in range(draws)]

    moments_cache = {}

    for row in rows:

        series = series_by_symbol.get(row["symbol"])

        if series is None:

            continue

        moment = pd.Timestamp(row["moment"])
        is_short = bool(row["is_short"])

        real = forward_move(series, moment, horizon, is_short)

        if real is None:

            continue

        actual.append(real)
        flipped.append(-real)

        key = (row["symbol"], str(row["day"]))

        if key not in moments_cache:

            moments_cache[key] = session_moments(series, row["day"])

        candidates = moments_cache[key]

        if not candidates:

            continue

        for draw in range(draws):

            alternative = forward_move(
                series, rng.choice(candidates), horizon, is_short
            )

            if alternative is not None:

                timing_draws[draw].append(alternative)

    return actual, flipped, timing_draws


def report(label, actual, flipped, timing_draws, horizon):

    if not actual:

        print(f"{label}: nothing to report")
        return

    mean_actual = st.mean(actual)
    se = st.stdev(actual) / (len(actual) ** 0.5)

    draw_means = [st.mean(d) for d in timing_draws if d]
    null_mean = st.mean(draw_means)
    null_sd = st.stdev(draw_means) if len(draw_means) > 1 else float("nan")

    # How many random-timing draws beat the real signal. This is the p-value the
    # honest way round: not "is it different from zero" but "is it different
    # from firing at random times in the same sessions".
    beaten = sum(1 for m in draw_means if m >= mean_actual)

    # Both levels are lookahead-contaminated and are printed only so the
    # difference can be checked by hand. Flipping direction at the same moment
    # returns exactly -actual, so it is arithmetic rather than evidence and is
    # not printed; it was in an earlier version and read as a third data point.
    print(f"\n{label}  horizon {horizon} bars  n={len(actual)}")
    print(f"  signal          {mean_actual:+.4f}%   SE {se:.4f}      (level: not tradable)")
    print(f"  random timing   {null_mean:+.4f}%   sd {null_sd:.4f}   (level: not tradable)")
    print(
        f"  EDGE OVER RANDOM {mean_actual - null_mean:+.4f}%"
        f"   <- the clean number"
        f"   ({beaten}/{len(draw_means)} random draws beat the signal)"
    )


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset")
    parser.add_argument("--horizons", default="12,39,78,234")
    parser.add_argument("--draws", type=int, default=20)
    parser.add_argument(
        "--holdout",
        action="store_true",
        help="also evaluate the holdout. Off by default: the holdout is spent "
        "once, and a null model is characterisation, not a hypothesis.",
    )
    args = parser.parse_args()

    rows = json.loads(pathlib.Path(args.dataset).read_text())
    train, held = holdout.partition(rows)

    parts = [("TRAIN", train)] + ([("HOLDOUT", held)] if args.holdout else [])

    for horizon in [int(h) for h in args.horizons.split(",")]:

        for name, part in parts:

            actual, flipped, timing = run(part, horizon, args.draws)
            report(name, actual, flipped, timing, horizon)

    holdout.record_comparison(
        "null_model_baseline",
        {"horizons": args.horizons, "draws": args.draws, "holdout": args.holdout},
    )

    print(f"\ncomparisons logged: {holdout.comparison_count()}")


if __name__ == "__main__":

    main()
