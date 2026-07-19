from __future__ import annotations

import pandas as pd


def _position(exit_index, df):

    if df is None or df.empty:

        return None

    if isinstance(exit_index, int):

        return exit_index if 0 <= exit_index < len(df) else None

    try:

        return int(df.index.get_loc(exit_index))

    except Exception:

        try:

            index = pd.to_datetime(df.index)
            target = pd.Timestamp(exit_index)

            if getattr(index, "tz", None) is not None and target.tzinfo is None:

                target = target.tz_localize(index.tz)

            if getattr(index, "tz", None) is None and target.tzinfo is not None:

                target = target.tz_localize(None)

            candidates = [
                pos for pos, value in enumerate(index)
                if value <= target
            ]

            return candidates[-1] if candidates else None

        except Exception:

            return None


def _direction(value):

    value = str(value or "").strip().upper()

    if value in {"PUT", "SHORT", "BEARISH", "HIGH CONVICTION BEARISH"}:

        return "SHORT"

    return "LONG"



def analyze_post_exit_trend(exit_index, df, direction):

    if df is None or df.empty:

        return {
            "trend_continued": False,
            "remaining_move": 0,
            "remaining_pct": 0,
            "bars_remaining": 0,
            "minutes_remaining": 0,
            "peak_price": None,
            "peak_time": None,
            "peak_bar": None,
        }

    pos = _position(exit_index, df)

    if pos is None or pos >= len(df) - 1:

        exit_price = float(df["Close"].iloc[pos]) if pos is not None and "Close" in df.columns else None

        return {
            "trend_continued": False,
            "remaining_move": 0,
            "remaining_pct": 0,
            "bars_remaining": 0,
            "minutes_remaining": 0,
            "peak_price": exit_price,
            "peak_time": str(df.index[pos]) if pos is not None else None,
            "peak_bar": pos,
        }

    exit_price = float(df["Close"].iloc[pos])
    future = df.iloc[pos + 1:]

    if _direction(direction) == "SHORT":

        peak_price = float(future["Low"].min())
        peak_label = future["Low"].idxmin()
        remaining_move = max(exit_price - peak_price, 0)

    else:

        peak_price = float(future["High"].max())
        peak_label = future["High"].idxmax()
        remaining_move = max(peak_price - exit_price, 0)

    peak_bar = int(df.index.get_loc(peak_label))
    bars_remaining = max(peak_bar - pos, 0)

    return {
        "trend_continued": remaining_move > 0,
        "remaining_move": round(remaining_move, 4),
        "remaining_pct": round((remaining_move / exit_price) * 100, 2) if exit_price else 0,
        "bars_remaining": bars_remaining,
        "minutes_remaining": bars_remaining * 5,
        "peak_price": round(peak_price, 4),
        "peak_time": str(peak_label),
        "peak_bar": peak_bar,
    }