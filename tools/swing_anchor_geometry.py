"""Does the higher-timeframe anchor reach a move that pays for the option?

This answers the question the app has never been able to answer, and it answers
it for free. Every previous arm cost one to two hours of Polygon option quotes;
this one reads underlying bars that are already cached on disk, because the
question is about geometry, not about which contract got bought.

The chain of reasoning it has to survive:

  1. The 15m anchor gives stops of 0.5-0.75%. Targets at 2R are therefore ~1.5%.
     The option round trip costs 1.5-3.4%. So even a winner barely clears the
     toll, and the measured book returns -3.0%.
  2. A 1h swing pivot sits further away. How much further is measured here, not
     assumed -- a wider stop that is still 0.9% would change nothing.
  3. A wider stop is only worth having if price actually travels that far. So
     each candidate is walked forward bar by bar on real 5m data to first touch
     of stop or target. No lookahead, no optimistic fills: whichever is touched
     first wins, and a bar that contains both is scored as the stop.
  4. The move is then converted to money with the model fitted on 601 archived
     trades. Converted from the **underlying move**, never from R: R is the move
     divided by the stop, so a slope fitted at 0.68% stops says nothing about an
     arm with 2.5% stops. Reading the first version of this tool's output as if
     it did made the swing arm look 4.5 points better than it is.
  5. And charged theta, which the fitted model omits because the book it was fit
     on held for two hours. An arm that holds two sessions pays for them.

Cost: no option quotes, no network if the cache is warm.

    python tools/swing_anchor_geometry.py --days 2026-07-06,...,2026-08-03
    python tools/swing_anchor_geometry.py --days ... --cadence 15 --symbols NVDA
"""

import argparse
import random
import statistics as st
import json
import pathlib
import sys
import warnings
from datetime import datetime, time as clock_time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pandas as pd
from dotenv import load_dotenv

warnings.filterwarnings(
    "ignore", category=FutureWarning, module="app.indicators.*"
)

load_dotenv()

from app.backtesting.historical_market_data import (
    HistoricalDataError,
    load_replay_frames,
)
from app.backtesting.replay_engine import ReplayConfig, build_frames
from app.config.watchlist import WATCHLIST
from app.risk.risk_manager import calculate_risk
from app.strategies.entry_engine import detect_entry
from app.strategies.setup_registry import is_short_setup

ET = "America/New_York"

# premium% = 8.59 x R - toll, fitted on 601 archived trades (R^2 = 0.80).
#
# That slope CANNOT be applied to R from an arm with different stops, and doing
# so is the mistake this constant exists to prevent. R is the move divided by
# the stop distance, so a slope fitted where stops averaged 0.68% means "8.59
# points of premium per 0.68% of underlying". An arm whose stops average 2.5%
# has a completely different number of premium points per R.
#
# So the model is restated in the term that does transfer -- premium points per
# 1% of underlying move -- and every arm is converted through the move it
# actually captured, not through its R.
PREMIUM_PER_R_AT_FIT = 8.59
MEAN_STOP_PCT_AT_FIT = 0.683
PREMIUM_PER_UNDERLYING_PCT = PREMIUM_PER_R_AT_FIT / MEAN_STOP_PCT_AT_FIT

# Cross-check, because the number above is doing a lot of work: a ~50 delta
# contract costing ~3% of notional moves 1% x 0.5 / 3% = 16.7% of premium per
# 1% of underlying. 12.6 is the same order, measured rather than assumed.

TOLL_AT_CEILING_6 = 3.40
TOLL_AT_CEILING_2 = 1.12

# Extrinsic decay per session, as a fraction of premium. For an ATM option
# premium scales with sqrt(T), so dP/P = 0.5 x dT/T -- about 2.4%/session at 21
# DTE. Zero for the intraday book, which is why the fitted model omits it
# entirely and why applying that model to a multi-session hold overstates it.
THETA_PCT_PER_SESSION = 2.4
BARS_PER_SESSION = 78


def scan_grid(trading_day, cadence_minutes, start="09:45", end="15:30"):

    day = pd.Timestamp(trading_day).date()

    first = pd.Timestamp(
        datetime.combine(day, clock_time.fromisoformat(start))
    ).tz_localize(ET)

    last = pd.Timestamp(
        datetime.combine(day, clock_time.fromisoformat(end))
    ).tz_localize(ET)

    return list(pd.date_range(first, last, freq=f"{cadence_minutes}min"))


def continuous_5m(symbol, days, lookback_days):
    """One de-duplicated 5m series spanning every requested session.

    Forward travel has to cross session boundaries -- that is the point of a
    multi-day hold -- and each cached file only covers a trailing window. Stitch
    them once per symbol rather than reloading per candidate.
    """

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
    joined = joined[~joined.index.duplicated(keep="first")].sort_index()

    return joined


def walk_to_first_touch(
    series, entry_time, entry_price, stop, target, is_short, max_hold_bars
):
    """Walk real bars forward to the first touch of stop or target.

    Returns ``(r_multiple, outcome, bars, mfe_r, exit_time)``.

    A bar whose range covers both levels is scored as the stop. Intrabar order
    is unknowable at 5m resolution, and assuming the target would manufacture
    exactly the edge this study is trying to detect.

    ``max_hold_bars`` closes the position at the prevailing price, which is what
    live does rather than holding forever. Those are reported as TIME exits and
    kept separate in the summary, because an arm that holds longer will collect
    more of them and the reader should be able to see that rather than infer it.
    """

    forward = series[series.index > entry_time]

    if forward.empty:

        return None

    risk = abs(entry_price - stop)

    if risk <= 0:

        return None

    best = 0.0
    last_close = entry_price
    last_time = entry_time

    for count, (timestamp, bar) in enumerate(forward.iterrows(), start=1):

        high = float(bar["High"])
        low = float(bar["Low"])
        last_close = float(bar["Close"])
        last_time = timestamp

        if is_short:

            excursion = (entry_price - low) / risk
            stop_hit = high >= stop
            target_hit = low <= target

        else:

            excursion = (high - entry_price) / risk
            stop_hit = low <= stop
            target_hit = high >= target

        best = max(best, excursion)

        if stop_hit:

            return -1.0, "STOP", count, best, timestamp

        if target_hit:

            reward = abs(target - entry_price)

            return reward / risk, "TARGET", count, best, timestamp

        if count >= max_hold_bars:

            move = (
                entry_price - last_close if is_short else last_close - entry_price
            )

            return move / risk, "TIME", count, best, timestamp

    # Ran out of data rather than out of patience -- a trade opened near the end
    # of the range. Not scored: counting it as 0R would flatter whichever arm
    # leaves more positions hanging.
    return None, "UNRESOLVED", len(forward), best, last_time


def build_arms(hold_caps):
    """(label, swing_enabled, max_hold_bars) per arm.

    The hold cap has to be an arm rather than a post-hoc filter, because it
    changes which trades exist. A shorter hold releases the symbol sooner and
    the next candidate becomes takeable, so truncating a long-hold run's trades
    would answer a question nobody asked: what the long arm would have earned
    had it closed early, rather than what a short arm actually trades.
    """

    arms = [("control", False, 234)]

    for cap in hold_caps:

        arms.append((f"swing_h{cap}", True, cap))

    return arms


def evaluate(days, symbols, cadence, lookback_days, arms, capture_features=True):

    config = ReplayConfig()
    config.lookback_days = lookback_days

    rows = []

    for symbol in symbols:

        series = continuous_5m(symbol, days, lookback_days)

        if series is None:

            print(f"  {symbol}: no cached bars, skipped")
            continue

        # Live holds at most one position per symbol and does not re-enter while
        # one is open. Without this, a 15m grid produces a dozen overlapping
        # entries per symbol-day that are near-copies of each other, and the mean
        # R becomes a statement about the grid rather than about the strategy.
        #
        # Tracked per arm on purpose. The control exits in minutes and is free to
        # re-enter; the swing arm holds and is not. That difference in trade
        # count is real, and it is half of what return on capital measures.
        busy_until = {label: None for label, _, _ in arms}

        for day in days:

            try:

                raw = load_replay_frames(symbol, day, lookback_days=lookback_days)

            except HistoricalDataError:

                continue

            if raw is None or raw.empty:

                continue

            for moment in scan_grid(day, cadence):

                built = build_frames(raw, moment, symbol, config)
                df_5m, df_15m, df_1h, _, analysis_15m, _ = built

                if df_5m is None or df_5m.empty or df_15m is None or df_15m.empty:

                    continue

                setup = detect_entry(df_15m, analysis_15m, symbol=symbol)

                if not setup or setup.get("entry_type") in (None, "NO_ENTRY"):

                    continue

                entry_price = float(df_5m["Close"].iloc[-1])
                is_short = is_short_setup(setup.get("entry_type"))

                control = calculate_risk(df_15m, analysis_15m, setup)
                treatment = _with_swing(df_15m, analysis_15m, setup, df_1h)

                row = {
                    "symbol": symbol,
                    "day": day,
                    "moment": str(moment),
                    "entry_type": setup.get("entry_type"),
                    "entry_price": entry_price,
                    "is_short": is_short,
                }

                # The indicator state the decision was actually made on. Without
                # it, asking "does relative volume predict the move" costs a
                # 45-minute rewalk instead of a regression on a dataframe, and
                # the research loop is monthly rather than weekly. Captured for
                # every candidate, including the ones no arm takes -- those are
                # the counterfactual, and a filter cannot be evaluated against
                # only the rows it already keeps.
                if capture_features:

                    row.update(_feature_snapshot(df_15m, df_1h, setup, analysis_15m))

                for label, swing_enabled, max_hold_bars in arms:

                    result = treatment if swing_enabled else control

                    allowed = bool(result.get("trade_allowed"))
                    stop = result.get("stop_loss")
                    target = result.get("take_profit")

                    row[f"{label}_allowed"] = allowed
                    row[f"{label}_rr"] = result.get("risk_reward")
                    row[f"{label}_stop_pct"] = (
                        abs(entry_price - stop) / entry_price * 100.0
                        if stop
                        else None
                    )

                    if not (allowed and stop and target):

                        continue

                    if busy_until[label] is not None and moment <= busy_until[label]:

                        row[f"{label}_outcome"] = "POSITION_OPEN"
                        continue

                    walked = walk_to_first_touch(
                        series,
                        moment,
                        entry_price,
                        stop,
                        target,
                        is_short,
                        max_hold_bars,
                    )

                    if walked is None:

                        continue

                    r_multiple, outcome, bars, mfe_r, exit_time = walked
                    row[f"{label}_r"] = r_multiple
                    row[f"{label}_outcome"] = outcome
                    row[f"{label}_bars"] = bars
                    row[f"{label}_mfe_r"] = mfe_r
                    row[f"{label}_taken"] = True

                    busy_until[label] = exit_time

                rows.append(row)

        print(f"  {symbol}: {len([r for r in rows if r['symbol'] == symbol])} candidates")

    return rows


# Columns worth carrying into the research dataset. Prices and raw volumes are
# excluded deliberately: they differ by two orders of magnitude across the
# watchlist, so any model fitted on them learns which symbol it is looking at
# rather than what the setup looks like. Everything here is a ratio, a distance
# or a flag.
FEATURE_COLUMNS_15M = (
    "ATR_PCT", "RSI", "RSI_SLOPE", "MACD", "MACD_SIGNAL", "EMA9_SLOPE",
    "VWAP_DISTANCE", "REL_VOLUME", "VOLUME_SPIKE", "VOLUME_TREND",
    "BODY_STRENGTH", "DISTANCE_TO_RESISTANCE", "DISTANCE_TO_SUPPORT",
    "SYMBOL_MOVE_PCT", "TREND_PHASE", "CONSOLIDATING", "BREAKOUT", "BREAKDOWN",
    "FAILED_BREAKOUT", "FAILED_BREAKDOWN", "HIGHER_HIGH", "HIGHER_LOW",
    "LOWER_HIGH", "LOWER_LOW", "ORB_BREAKOUT", "ORB_BREAKDOWN",
)

FEATURE_COLUMNS_1H = (
    "ATR_PCT", "RSI", "EMA9_SLOPE", "VWAP_DISTANCE", "REL_VOLUME", "TREND_PHASE",
)


def _scalar(value):
    """JSON-safe, NaN-safe, numpy-safe."""

    if value is None:

        return None

    try:

        if isinstance(value, (bool,)):

            return bool(value)

        number = float(value)

        return None if number != number else number

    except (TypeError, ValueError):

        return str(value)


def _feature_snapshot(df_15m, df_1h, setup, analysis_15m):
    """Indicator state at decision time, prefixed by timeframe."""

    snapshot = {}

    latest_15m = df_15m.iloc[-1]

    for column in FEATURE_COLUMNS_15M:

        if column in df_15m.columns:

            snapshot[f"f15_{column}"] = _scalar(latest_15m.get(column))

    if df_1h is not None and not df_1h.empty:

        latest_1h = df_1h.iloc[-1]

        for column in FEATURE_COLUMNS_1H:

            if column in df_1h.columns:

                snapshot[f"f1h_{column}"] = _scalar(latest_1h.get(column))

    snapshot["f_entry_quality"] = setup.get("entry_quality")
    snapshot["f_avoid_chasing"] = bool(setup.get("avoid_chasing"))
    snapshot["f_signal"] = (analysis_15m or {}).get("signal")
    snapshot["f_market_regime"] = (analysis_15m or {}).get("market_regime")
    snapshot["f_score"] = _scalar((analysis_15m or {}).get("score"))

    # Minutes since the open. Time of day is a Phase 1 hypothesis and is not
    # currently a factor anywhere in the app, so it has to be carried to be
    # testable at all.
    moment = pd.Timestamp(df_15m.index[-1]) if len(df_15m.index) else None

    if moment is not None and moment.tzinfo is not None:

        local = moment.tz_convert(ET)
        snapshot["f_minutes_from_open"] = (
            (local.hour - 9) * 60 + local.minute - 30
        )

    return snapshot


def _with_swing(df_15m, analysis_15m, setup, df_1h):
    """Run the treatment arm without leaking its env into the control."""

    import os

    previous = os.environ.get("SWING_STRUCTURE_ENABLED")
    os.environ["SWING_STRUCTURE_ENABLED"] = "true"

    try:

        return calculate_risk(df_15m, analysis_15m, setup, htf=df_1h)

    finally:

        if previous is None:

            os.environ.pop("SWING_STRUCTURE_ENABLED", None)

        else:

            os.environ["SWING_STRUCTURE_ENABLED"] = previous


def _bootstrap_mean_ci(values, draws=2000, seed=7):
    """Percentile bootstrap on the mean.

    Seeded so a rerun of the same data gives the same interval; an interval that
    moved between readings would invite picking the reading.
    """

    if len(values) < 2:

        return float("nan"), float("nan")

    rng = random.Random(seed)
    means = sorted(
        st.mean(rng.choices(values, k=len(values))) for _ in range(draws)
    )

    return means[int(draws * 0.025)], means[int(draws * 0.975)]


def _percentiles(values, points=(10, 25, 50, 75, 90)):

    if not values:

        return {}

    ordered = sorted(values)

    return {
        f"p{p}": ordered[min(len(ordered) - 1, int(len(ordered) * p / 100))]
        for p in points
    }


def summarise(rows):

    print(f"\n{'='*78}")
    print(f"candidates evaluated: {len(rows)}")

    # Discovered rather than hardcoded, so a run with a different set of hold
    # caps summarises without the reader having to match a flag to the output.
    labels = []

    for row in rows:

        for key in row:

            if key.endswith("_allowed") and key[: -len("_allowed")] not in labels:

                labels.append(key[: -len("_allowed")])

    for label in labels:

        allowed = [r for r in rows if r.get(f"{label}_allowed")]
        stops = [
            r[f"{label}_stop_pct"] for r in allowed if r.get(f"{label}_stop_pct")
        ]

        taken = [r for r in rows if r.get(f"{label}_taken")]

        print(f"\n{'-'*78}\n{label.upper()}")
        print(
            f"  cleared the gates: {len(allowed)} of {len(rows)}"
            f"   actually taken (one position per symbol): {len(taken)}"
        )

        if stops:

            pcts = _percentiles(stops)
            print(
                "  stop distance %:  "
                + "  ".join(f"{k} {v:.2f}" for k, v in pcts.items())
                + f"   mean {sum(stops)/len(stops):.2f}"
            )

        resolved = [r for r in taken if r.get(f"{label}_r") is not None]
        unresolved = [
            r for r in taken if r.get(f"{label}_outcome") == "UNRESOLVED"
        ]

        if not resolved:

            print("  nothing resolved to stop or target")
            continue

        rs = [r[f"{label}_r"] for r in resolved]
        outcomes = {}

        for r in resolved:

            key = r[f"{label}_outcome"]
            outcomes[key] = outcomes.get(key, 0) + 1

        bars = [r[f"{label}_bars"] for r in resolved]
        mfes = [r[f"{label}_mfe_r"] for r in taken if r.get(f"{label}_mfe_r")]

        mean_r = sum(rs) / len(rs)
        wins = [r for r in rs if r > 0]

        print(
            f"  resolved: {len(resolved)}   "
            + "  ".join(f"{k} {v}" for k, v in sorted(outcomes.items()))
            + f"   unresolved {len(unresolved)}"
        )
        print(
            f"  mean R {mean_r:+.3f}   win rate "
            f"{len(wins)/len(rs)*100:.0f}%   median hold "
            f"{sorted(bars)[len(bars)//2]} bars of 5m"
        )

        if mfes:

            print(f"  mean MFE {sum(mfes)/len(mfes):+.2f}R")

        # The only lines that matter, and they are computed from the underlying
        # move rather than from R -- see PREMIUM_PER_UNDERLYING_PCT. Two arms
        # with the same R and different stops do not earn the same money.
        moves = [
            r[f"{label}_r"] * r[f"{label}_stop_pct"] for r in resolved
        ]
        mean_move = sum(moves) / len(moves)
        delta_pnl = PREMIUM_PER_UNDERLYING_PCT * mean_move

        sessions = (sum(bars) / len(bars)) / BARS_PER_SESSION
        theta = THETA_PCT_PER_SESSION * sessions

        print(
            f"  mean underlying move captured: {mean_move:+.4f}% of price"
            f"   (mean MFE {sum(m for m in mfes)/len(mfes) if mfes else 0:+.2f}R)"
        )

        # Printed beside the mean, always, because the mean alone is what got
        # +14.43% reported as this project's first positive result. It was five
        # trades of 331: without them the total is negative and the median trade
        # loses 0.75% of price. Both checks cost milliseconds.
        low, high = _bootstrap_mean_ci(moves)
        trimmed = sorted(moves, reverse=True)[5:]

        print(
            f"  95% CI [{low:+.4f}, {high:+.4f}]"
            f"   median {st.median(moves):+.4f}%"
            f"   without top 5 {st.mean(trimmed) if trimmed else 0:+.4f}%"
        )

        if low <= 0 <= high:

            print("  -> not distinguishable from zero")
        print(
            f"  premium from delta: {delta_pnl:+.2f}%"
            f"   theta over {sessions:.1f} sessions: -{theta:.2f}%"
        )

        for toll, name in (
            (TOLL_AT_CEILING_6, "spread ceiling 6"),
            (TOLL_AT_CEILING_2, "spread ceiling 2"),
        ):

            print(
                f"  implied premium return @ {name}: "
                f"{delta_pnl - toll - theta:+.2f}%"
                f"   (before theta {delta_pnl - toll:+.2f}%)"
            )

        # What the move would have to be, in the term that transfers.
        needed = (TOLL_AT_CEILING_2 + theta) / PREMIUM_PER_UNDERLYING_PCT

        print(
            f"  underlying move needed to break even at ceiling 2: "
            f"{needed:+.3f}%   (captured {mean_move:+.4f}%)"
        )


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", default=None, help="comma-separated YYYY-MM-DD")
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--cadence", type=int, default=15)
    parser.add_argument("--lookback-days", type=int, default=5)
    parser.add_argument(
        "--hold-caps",
        default="234",
        help="comma-separated 5m bar counts; one swing arm per cap. 78 is a "
        "session, 234 is three. Theta is the swing anchor's whole problem -- it "
        "costs 5.07%% over the 2.1 sessions the 234 arm holds -- so the question "
        "this sweeps is whether the edge survives being collected sooner",
    )
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--no-features",
        action="store_true",
        help="skip the indicator snapshot; smaller output, and the "
        "resulting file cannot be used for feature research",
    )
    parser.add_argument(
        "--summarise",
        default=None,
        help="re-read a saved run and summarise it again, without recomputing. "
        "The walk takes 45 minutes; asking a second question of it should not.",
    )
    args = parser.parse_args()

    if args.summarise:

        summarise(json.loads(pathlib.Path(args.summarise).read_text()))

        return

    days = [d.strip() for d in args.days.split(",") if d.strip()]
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",")]
        if args.symbols
        else list(WATCHLIST)
    )

    print(
        f"{len(days)} day(s), {len(symbols)} symbols, {args.cadence}m cadence"
        f"  (underlying bars only -- no option quotes)\n"
    )

    caps = [int(c.strip()) for c in args.hold_caps.split(",") if c.strip()]
    arms = build_arms(caps)

    print("arms: " + ", ".join(label for label, _, _ in arms) + "\n")

    rows = evaluate(
        days, symbols, args.cadence, args.lookback_days, arms,
        capture_features=not args.no_features,
    )

    summarise(rows)

    if args.out:

        destination = pathlib.Path(args.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(rows, indent=2, default=str))
        print(f"\nwrote {destination}")


if __name__ == "__main__":

    main()
