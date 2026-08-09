"""Attach an unconditional forward outcome to every candidate.

The geometry walk only resolves candidates an arm actually took, because live
holds one position per symbol and the lock skips the rest. That is right for
measuring a strategy and wrong for researching one: 792 of 5,590 rows carried an
outcome, and those 792 are exactly the rows the current rules already select. A
filter cannot be evaluated against only the rows it already keeps -- the
question "would skipping these have helped" has no answer if the skipped ones
were never scored.

So this scores all of them, and scores them in a way that is independent of the
stop and target in force at the time. The labels are raw forward moves at fixed
horizons, sign-adjusted for direction, so a PUT that fell 1% and a CALL that
rose 1% both read +1.0. No stop, no target, no exit rule -- those are strategy
choices, and baking one into the label would mean every future hypothesis was
tested against today's geometry.

Runs off cached 5m bars only. No indicator recomputation, so it is minutes
rather than the 45 the full walk takes, and no API quota.

    python tools/label_candidates.py research/candidates_21day.json
"""

import argparse
import json
import pathlib
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

ET = "America/New_York"

# 5m bars. One hour, half a session, one session, three sessions -- wide enough
# that the horizon itself becomes a testable question rather than an assumption
# baked in at collection time.
HORIZONS = (12, 39, 78, 234)


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


def label(series, moment, entry_price, is_short):
    """Forward move at each horizon, plus excursions, as % of entry price.

    Sign-adjusted: positive always means the trade's direction was right.
    """

    forward = series[series.index > moment]

    if forward.empty or entry_price <= 0:

        return None

    sign = -1.0 if is_short else 1.0
    out = {}

    for horizon in HORIZONS:

        window = forward.iloc[:horizon]

        if window.empty:

            continue

        closing = float(window["Close"].iloc[-1])
        out[f"label_fwd_{horizon}"] = sign * (closing - entry_price) / entry_price * 100.0

        # Excursions are what any stop or target would actually have interacted
        # with, so carrying them lets a later hypothesis test a geometry without
        # re-walking the bars.
        high = float(window["High"].max())
        low = float(window["Low"].min())

        favourable = (high - entry_price) if not is_short else (entry_price - low)
        adverse = (entry_price - low) if not is_short else (high - entry_price)

        out[f"label_mfe_{horizon}"] = favourable / entry_price * 100.0
        out[f"label_mae_{horizon}"] = adverse / entry_price * 100.0
        out[f"label_bars_{horizon}"] = len(window)

    return out or None


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset")
    parser.add_argument("--out", default=None, help="defaults to in place")
    args = parser.parse_args()

    path = pathlib.Path(args.dataset)
    rows = json.loads(path.read_text())

    days = sorted({str(row["day"]) for row in rows})
    symbols = sorted({row["symbol"] for row in rows})

    print(f"{len(rows)} candidates, {len(symbols)} symbols, {len(days)} sessions")

    labelled = 0

    for symbol in symbols:

        series = continuous_5m(symbol, days)

        if series is None:

            print(f"  {symbol}: no cached bars, left unlabelled")
            continue

        for row in rows:

            if row["symbol"] != symbol:

                continue

            result = label(
                series,
                pd.Timestamp(row["moment"]),
                float(row["entry_price"]),
                bool(row["is_short"]),
            )

            if result:

                row.update(result)
                labelled += 1

        print(f"  {symbol}: labelled", flush=True)

    destination = pathlib.Path(args.out) if args.out else path
    destination.write_text(json.dumps(rows, default=str))

    print(f"\nlabelled {labelled} of {len(rows)} candidates -> {destination}")

    # The coverage line matters more than the count. The whole reason this tool
    # exists is that the previous dataset covered 14% of its own rows.
    print(f"coverage {labelled/len(rows)*100:.1f}%")


if __name__ == "__main__":

    main()
