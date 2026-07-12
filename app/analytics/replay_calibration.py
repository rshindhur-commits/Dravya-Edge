import math

import pandas as pd

from app.analytics.trade_outcome_tracker import normalize_trade_direction


DEFAULT_STOP_ATR_MULTIPLES = (0.6, 0.8, 1.0, 1.3, 1.5, 1.8, 2.0)
DEFAULT_TARGET_ATR_MULTIPLES = (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0)
DEFAULT_HORIZONS = (5, 10, 15, 20, 30)


def _normalize_candles(candles):

    df = candles.copy()
    df = df.rename(
        columns={
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    df.columns = [str(column).lower() for column in df.columns]

    required_columns = {"high", "low", "close"}
    missing_columns = required_columns - set(df.columns)

    if missing_columns:

        raise ValueError(f"Missing candle columns: {sorted(missing_columns)}")

    return df


def _safe_float(value, default=None):

    try:

        if value is None:

            return default

        if isinstance(value, float) and math.isnan(value):

            return default

        return float(value)

    except Exception:

        return default


def _infer_atr(candles):

    if "atr" in candles.columns:

        atr = _safe_float(candles["atr"].dropna().iloc[-1], None)

        if atr and atr > 0:

            return atr

    ranges = (candles["high"] - candles["low"]).tail(14)

    if ranges.empty:

        return None

    atr = _safe_float(ranges.mean(), None)

    if atr and atr > 0:

        return atr

    return None


def _price_levels(entry_price, direction, atr, stop_multiple, target_multiple):

    normalized_direction = normalize_trade_direction(direction)


    if normalized_direction == "BEARISH":

        return (
            entry_price + (atr * stop_multiple),
            entry_price - (atr * target_multiple)
        )

    return (
        entry_price - (atr * stop_multiple),
        entry_price + (atr * target_multiple)
    )


def _simulate_path(
    candles,
    direction,
    entry_price,
    stop_price,
    target_price,
    horizon_bars,
    ignore_stop_bars=2
):

    normalized_direction = normalize_trade_direction(direction)
    future_df = candles.head(horizon_bars)
    bars_to_target = None
    bars_to_stop = None
    ignored_stop_hit = False
    ignored_stop_bar = None
    max_favorable = 0.0
    max_adverse = 0.0

    for bar_number, row in enumerate(future_df.itertuples(), start=1):

        high = float(row.high)
        low = float(row.low)

        if normalized_direction == "BEARISH":

            favorable_move = entry_price - low
            adverse_move = high - entry_price
            target_hit = low <= target_price
            stop_hit = high >= stop_price
        else:

            favorable_move = high - entry_price
            adverse_move = entry_price - low
            target_hit = high >= target_price
            stop_hit = low <= stop_price

        max_favorable = max(max_favorable, favorable_move)
        max_adverse = max(max_adverse, adverse_move)

        if target_hit and bars_to_target is None:

            bars_to_target = bar_number

        if stop_hit and bar_number <= ignore_stop_bars and not ignored_stop_hit:

            ignored_stop_hit = True
            ignored_stop_bar = bar_number

        if stop_hit and bars_to_stop is None and bar_number > ignore_stop_bars:

            bars_to_stop = bar_number

        if bars_to_target is not None or bars_to_stop is not None:

            break

    if bars_to_target is None and bars_to_stop is None:

        outcome = "NO_HIT"
    elif bars_to_target is not None and bars_to_stop is None:

        outcome = "TARGET_HIT"
    elif bars_to_stop is not None and bars_to_target is None:

        outcome = "STOP_HIT"
    elif bars_to_target <= bars_to_stop:

        outcome = "TARGET_HIT"
    else:

        outcome = "STOP_HIT"

    return {
        "outcome": outcome,
        "bars_to_target": bars_to_target,
        "bars_to_stop": bars_to_stop,
        "ignored_stop_hit": ignored_stop_hit,
        "ignored_stop_bar": ignored_stop_bar,
        "mfe": max_favorable,
        "mae": max_adverse,
    }


def calibrate_replay_path(
    candles,
    entry_price,
    direction,
    atr=None,
    stop_atr_multiples=DEFAULT_STOP_ATR_MULTIPLES,
    target_atr_multiples=DEFAULT_TARGET_ATR_MULTIPLES,
    horizons=DEFAULT_HORIZONS,
    ignore_stop_bars=2,
    metadata=None
):

    normalized_candles = _normalize_candles(candles)
    entry_price = _safe_float(entry_price)
    atr = _safe_float(atr, None) or _infer_atr(normalized_candles)

    if not entry_price or not atr:

        raise ValueError("entry_price and ATR are required for replay calibration")

    metadata = metadata or {}
    records = []

    for horizon in horizons:

        horizon_outcomes = []

        for stop_multiple in stop_atr_multiples:

            for target_multiple in target_atr_multiples:

                stop_price, target_price = _price_levels(
                    entry_price,
                    direction,
                    atr,
                    stop_multiple,
                    target_multiple
                )
                path_result = _simulate_path(
                    normalized_candles,
                    direction,
                    entry_price,
                    stop_price,
                    target_price,
                    horizon,
                    ignore_stop_bars=ignore_stop_bars
                )
                r_multiple = 0.0

                if path_result["outcome"] == "TARGET_HIT":

                    r_multiple = target_multiple / stop_multiple
                elif path_result["outcome"] == "STOP_HIT":

                    r_multiple = -1.0

                record = {
                    **metadata,
                    "horizon_bars": horizon,
                    "stop_atr_multiple": stop_multiple,
                    "target_atr_multiple": target_multiple,
                    "outcome": path_result["outcome"],
                    "r_multiple": round(r_multiple, 4),
                    "bars_to_target": path_result["bars_to_target"],
                    "bars_to_stop": path_result["bars_to_stop"],
                    "ignored_stop_hit": path_result["ignored_stop_hit"],
                    "ignored_stop_bar": path_result["ignored_stop_bar"],
                    "mfe": round(path_result["mfe"], 4),
                    "mae": round(path_result["mae"], 4),
                    "mfe_atr": round(path_result["mfe"] / atr, 4),
                    "mae_atr": round(path_result["mae"] / atr, 4),
                }
                records.append(record)
                horizon_outcomes.append(record)

    calibration_df = pd.DataFrame(records)

    if calibration_df.empty:

        return {
            **metadata,
            "mfe": 0,
            "mae": 0,
            "bars_to_target": None,
            "bars_to_stop": None,
            "best_stop_atr_multiple": None,
            "best_target_atr_multiple": None,
            "best_time_exit_bars": None,
            "win_rate_by_horizon": {},
            "paths": calibration_df,
        }

    best_record = calibration_df.sort_values(
        by=["r_multiple", "horizon_bars"],
        ascending=[False, True]
    ).iloc[0]
    win_rate_by_horizon = (
        calibration_df.assign(is_win=calibration_df["outcome"] == "TARGET_HIT")
        .groupby("horizon_bars")["is_win"]
        .mean()
        .round(4)
        .to_dict()
    )

    return {
        **metadata,
        "mfe": round(calibration_df["mfe"].max(), 4),
        "mae": round(calibration_df["mae"].max(), 4),
        "mfe_atr": round(calibration_df["mfe_atr"].max(), 4),
        "mae_atr": round(calibration_df["mae_atr"].max(), 4),
        "bars_to_target": best_record["bars_to_target"],
        "bars_to_stop": best_record["bars_to_stop"],
        "best_stop_atr_multiple": best_record["stop_atr_multiple"],
        "best_target_atr_multiple": best_record["target_atr_multiple"],
        "best_time_exit_bars": best_record["horizon_bars"],
        "best_r_multiple": best_record["r_multiple"],
        "win_rate_by_horizon": win_rate_by_horizon,
        "paths": calibration_df,
    }


def summarize_calibration(calibration_df, group_columns=None):

    if calibration_df is None or calibration_df.empty:

        return pd.DataFrame()

    group_columns = group_columns or [
        "setup_type",
        "market_regime",
        "time_bucket",
        "top_candidate",
    ]
    available_groups = [
        column for column in group_columns
        if column in calibration_df.columns
    ]

    if not available_groups:

        available_groups = ["outcome"]

    summary = (
        calibration_df
        .assign(is_win=calibration_df["outcome"] == "TARGET_HIT")
        .groupby(available_groups, dropna=False)
        .agg(
            tests=("outcome", "count"),
            win_rate=("is_win", "mean"),
            avg_r=("r_multiple", "mean"),
            avg_mfe_atr=("mfe_atr", "mean"),
            avg_mae_atr=("mae_atr", "mean"),
            median_bars_to_target=("bars_to_target", "median"),
            median_bars_to_stop=("bars_to_stop", "median"),
        )
        .reset_index()
    )
    summary["win_rate"] = (summary["win_rate"] * 100).round(2)

    return summary.round(2)