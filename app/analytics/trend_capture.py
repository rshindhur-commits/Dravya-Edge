from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.analytics.trade_efficiency.exit_delay_analysis import analyze_exit_delay
from app.analytics.trade_efficiency.recommendations import (
    calculate_trade_efficiency_score,
    generate_trade_efficiency_recommendation,
)
from app.analytics.trade_efficiency.trend_continuation import analyze_post_exit_trend
from app.storage.daily_paths import daily_path


TREND_CAPTURE_COLUMNS = [
    "Trading Day",
    "Session ID",
    "Trade Key",
    "Symbol",
    "Direction",
    "Setup",
    "Market Regime",
    "Entry Time",
    "Exit Time",
    "Entry Price",
    "Exit Price",
    "Highest Price After Entry",
    "Lowest Price After Entry",
    "Available Move",
    "Captured Move",
    "Trend Capture %",
    "Maximum Favorable Excursion",
    "Maximum Adverse Excursion",
    "Left On Table",
    "Risk Reward",
    "Exit Reason",
    "Bars Held",
    "Replay Outcome",
    "Trend Health Score",
    "Trend Health State",
    "EMA9 At Exit",
    "EMA20 At Exit",
    "VWAP At Exit",
    "MACD At Exit",
    "MACD Signal At Exit",
    "MACD Histogram At Exit",
    "RSI At Exit",
    "ATR At Exit",
    "Volume At Exit",
    "Relative Volume At Exit",
    "Higher High At Exit",
    "Higher Low At Exit",
    "Lower High At Exit",
    "Lower Low At Exit",
    "Price Above EMA9",
    "Price Above VWAP",
    "EMA Alignment",
    "MACD Bullish",
    "Trend Continued",
    "Remaining Move",
    "Remaining %",
    "Bars Remaining",
    "Minutes Remaining",
    "Peak Price",
    "Peak Time",
    "Exit Quality",
    "Exit Verdict",
    "Exit Comments",
    "Primary Exit",
    "Secondary Exits",
    "Ignored Exit Signals",
    "Triggered EMA",
    "Triggered VWAP",
    "Triggered MACD",
    "Triggered Stop",
    "Triggered Target",
    "Triggered Time Exit",
    "Triggered Near Close",
    "Profit +1 Bar",
    "Profit +2 Bars",
    "Profit +3 Bars",
    "Profit +5 Bars",
    "Best Delay",
    "Best Profit",
    "Delay Recommendation",
    "Trade Efficiency Score",
]


def _safe_float(value, default=None):

    try:

        if value is None:

            return default

        value = float(value)

        if pd.isna(value):

            return default

        return value

    except Exception:

        return default


def _parse_time(value):

    if not value:

        return None

    if isinstance(value, datetime):

        return value

    try:

        return datetime.fromisoformat(str(value))

    except Exception:

        pass

    for fmt in [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ]:

        try:

            return datetime.strptime(str(value), fmt)

        except Exception:

            continue

    return None


def _normalize_direction(value):

    value = str(value or "").strip().upper()

    if value in {"CALL", "LONG", "BULLISH", "HIGH CONVICTION BULLISH"}:

        return "LONG"

    if value in {"PUT", "SHORT", "BEARISH", "HIGH CONVICTION BEARISH"}:

        return "SHORT"

    return "UNKNOWN"


def _after_entry_frame(trade, df_5m):

    if df_5m is None or df_5m.empty:

        return pd.DataFrame()

    df_after_entry = df_5m.copy()
    entry_time = _parse_time(
        trade.get("opened_at_et")
        or trade.get("opened_at")
        or trade.get("entry_time")
    )

    if entry_time is None:

        return df_after_entry

    try:

        index = pd.to_datetime(df_after_entry.index)

        if getattr(index, "tz", None) is not None and entry_time.tzinfo is None:

            entry_time = entry_time.replace(tzinfo=index.tz)

        if getattr(index, "tz", None) is None and entry_time.tzinfo is not None:

            entry_time = entry_time.replace(tzinfo=None)

        return df_after_entry.loc[index >= entry_time]

    except Exception:

        return df_after_entry


def _after_exit_frame(trade, df_5m):

    if df_5m is None or df_5m.empty:

        return pd.DataFrame()


def _exit_position(trade, df_5m):

    if df_5m is None or df_5m.empty:

        return None

    exit_time = _parse_time(
        trade.get("closed_at_et")
        or trade.get("closed_at")
        or trade.get("exit_time")
    )

    if exit_time is None:

        return len(df_5m) - 1

    try:

        index = pd.to_datetime(df_5m.index)

        if getattr(index, "tz", None) is not None and exit_time.tzinfo is None:

            exit_time = exit_time.replace(tzinfo=index.tz)

        if getattr(index, "tz", None) is None and exit_time.tzinfo is not None:

            exit_time = exit_time.replace(tzinfo=None)

        candidates = [
            position for position, value in enumerate(index)
            if value <= exit_time
        ]

        return candidates[-1] if candidates else None

    except Exception:

        return len(df_5m) - 1

    exit_time = _parse_time(
        trade.get("closed_at_et")
        or trade.get("closed_at")
        or trade.get("exit_time")
    )

    if exit_time is None:

        return pd.DataFrame()

    try:

        index = pd.to_datetime(df_5m.index)

        if getattr(index, "tz", None) is not None and exit_time.tzinfo is None:

            exit_time = exit_time.replace(tzinfo=index.tz)

        if getattr(index, "tz", None) is None and exit_time.tzinfo is not None:

            exit_time = exit_time.replace(tzinfo=None)

        return df_5m.loc[index > exit_time]

    except Exception:

        return pd.DataFrame()


def classify_exit_quality(trend_capture_pct):

    trend_capture_pct = _safe_float(trend_capture_pct, 0)

    if trend_capture_pct >= 80:

        return "EXCELLENT"

    if trend_capture_pct >= 65:

        return "GOOD"

    if trend_capture_pct >= 50:

        return "AVERAGE"

    return "POOR"


def classify_exit_verdict(trend_capture_pct, trend_health_state):

    trend_capture_pct = _safe_float(trend_capture_pct, 0)
    trend_health_state = str(trend_health_state or "").upper()

    if trend_capture_pct < 55 and trend_health_state == "STRONG":

        return "EXIT_TOO_EARLY"

    if trend_capture_pct < 55 and trend_health_state == "BROKEN":

        return "CORRECT_EXIT"

    if trend_capture_pct > 80:

        return "EXCELLENT_EXIT"

    return "NEEDS_REVIEW"


def exit_comments(exit_verdict):

    return {
        "EXIT_TOO_EARLY": "Trend remained strong after exit; review trailing/hold logic.",
        "CORRECT_EXIT": "Trend was broken with low remaining capture; exit likely protected capital.",
        "EXCELLENT_EXIT": "Exit captured most available trend.",
        "NEEDS_REVIEW": "Review exit context against setup and regime.",
    }.get(exit_verdict, "Review exit context.")



def analyze_trend_capture(
    trade: dict,
    df_5m: pd.DataFrame
) -> dict:

    trade = trade or {}
    df_after_entry = _after_entry_frame(
        trade,
        df_5m
    )

    if df_after_entry.empty:

        return {
            "available_move": 0,
            "captured_move": 0,
            "trend_capture_pct": 0,
            "highest_after_entry": None,
            "lowest_after_entry": None,
            "mfe": 0,
            "mae": 0,
            "left_on_table": 0,
            "bars_held": 0,
            "bars_remaining": 0,
            "post_exit_trend": {},
            "exit_delay": {},
        }

    entry_price = _safe_float(trade.get("entry_price"), 0)
    exit_price = _safe_float(
        trade.get("exit_price", trade.get("close_price")),
        entry_price
    )
    highest = _safe_float(df_after_entry["High"].max(), entry_price)
    lowest = _safe_float(df_after_entry["Low"].min(), entry_price)
    direction = _normalize_direction(
        trade.get("direction")
        or trade.get("Candidate Direction")
        or trade.get("final_signal")
    )

    if direction == "SHORT":

        available_move = max(entry_price - lowest, 0)
        captured_move = entry_price - exit_price
        mfe = available_move
        mae = max(highest - entry_price, 0)
        left_on_table = max(exit_price - lowest, 0)

    else:

        available_move = max(highest - entry_price, 0)
        captured_move = exit_price - entry_price
        mfe = available_move
        mae = max(entry_price - lowest, 0)
        left_on_table = max(highest - exit_price, 0)

    trend_capture_pct = (
        (captured_move / available_move) * 100
        if available_move > 0
        else 0
    )

    exit_position = _exit_position(
        trade,
        df_5m
    )
    post_exit_trend = analyze_post_exit_trend(
        exit_position,
        df_5m,
        direction
    )
    exit_delay = analyze_exit_delay(
        exit_position,
        df_5m,
        direction
    )

    return {
        "available_move": round(available_move, 4),
        "captured_move": round(captured_move, 4),
        "trend_capture_pct": round(trend_capture_pct, 2),
        "highest_after_entry": round(highest, 4),
        "lowest_after_entry": round(lowest, 4),
        "mfe": round(mfe, 4),
        "mae": round(mae, 4),
        "left_on_table": round(left_on_table, 4),
        "bars_held": int(len(df_after_entry)),
        "bars_remaining": post_exit_trend.get("bars_remaining", 0),
        "post_exit_trend": post_exit_trend,
        "exit_delay": exit_delay,
    }


def _list_text(value):

    if value is None:

        return None

    if isinstance(value, list):

        return ", ".join(str(item) for item in value)

    return str(value)


def _triggered(exit_reason, *needles):

    exit_reason = str(exit_reason or "").upper()

    return any(needle in exit_reason for needle in needles)


def build_trend_capture_row(trade, trend_capture, snapshot=None):

    scanner_context = trade.get("scanner_context") or trade.get("close_scanner_context") or {}
    snapshot = snapshot or {}
    exit_quality = classify_exit_quality(
        trend_capture.get("trend_capture_pct")
    )
    exit_verdict = classify_exit_verdict(
        trend_capture.get("trend_capture_pct"),
        snapshot.get("trend_health_state")
    )
    post_exit_trend = trend_capture.get("post_exit_trend") or {}
    exit_delay = trend_capture.get("exit_delay") or {}
    primary_exit = trade.get("primary_exit") or trade.get("exit_reason")
    secondary_exits = trade.get("secondary_exits")
    ignored_exit_signals = trade.get("ignored_exit_signals")
    row = {
        "Trading Day": trade.get("trading_day"),
        "Session ID": trade.get("session_id"),
        "Trade Key": trade.get("trade_key"),
        "Symbol": trade.get("symbol"),
        "Direction": trade.get("direction"),
        "Setup": trade.get("setup_type") or trade.get("entry_type") or scanner_context.get("Entry"),
        "Market Regime": trade.get("market_regime") or scanner_context.get("Market Regime"),
        "Entry Time": trade.get("opened_at_et") or trade.get("opened_at"),
        "Exit Time": trade.get("closed_at_et") or trade.get("closed_at"),
        "Entry Price": trade.get("entry_price"),
        "Exit Price": trade.get("close_price") or trade.get("exit_price"),
        "Highest Price After Entry": trend_capture.get("highest_after_entry"),
        "Lowest Price After Entry": trend_capture.get("lowest_after_entry"),
        "Available Move": trend_capture.get("available_move"),
        "Captured Move": trend_capture.get("captured_move"),
        "Trend Capture %": trend_capture.get("trend_capture_pct"),
        "Maximum Favorable Excursion": trend_capture.get("mfe"),
        "Maximum Adverse Excursion": trend_capture.get("mae"),
        "Left On Table": trend_capture.get("left_on_table"),
        "Risk Reward": trade.get("planned_rr") or scanner_context.get("Risk Reward"),
        "Exit Reason": trade.get("exit_reason"),
        "Bars Held": trend_capture.get("bars_held"),
        "Replay Outcome": trade.get("outcome") or scanner_context.get("Replay Outcome"),
        "Trend Health Score": snapshot.get("trend_health_score"),
        "Trend Health State": snapshot.get("trend_health_state"),
        "EMA9 At Exit": snapshot.get("ema9"),
        "EMA20 At Exit": snapshot.get("ema20"),
        "VWAP At Exit": snapshot.get("vwap"),
        "MACD At Exit": snapshot.get("macd"),
        "MACD Signal At Exit": snapshot.get("macd_signal"),
        "MACD Histogram At Exit": snapshot.get("macd_hist"),
        "RSI At Exit": snapshot.get("rsi"),
        "ATR At Exit": snapshot.get("atr"),
        "Volume At Exit": snapshot.get("volume"),
        "Relative Volume At Exit": snapshot.get("relative_volume"),
        "Higher High At Exit": snapshot.get("higher_high"),
        "Higher Low At Exit": snapshot.get("higher_low"),
        "Lower High At Exit": snapshot.get("lower_high"),
        "Lower Low At Exit": snapshot.get("lower_low"),
        "Price Above EMA9": snapshot.get("price_above_ema9"),
        "Price Above VWAP": snapshot.get("price_above_vwap"),
        "EMA Alignment": snapshot.get("ema_alignment"),
        "MACD Bullish": snapshot.get("macd_bullish"),
        "Trend Continued": post_exit_trend.get("trend_continued", snapshot.get("trend_health_state") in {"STRONG", "HEALTHY"}),
        "Remaining Move": post_exit_trend.get("remaining_move"),
        "Remaining %": post_exit_trend.get("remaining_pct"),
        "Bars Remaining": trend_capture.get("bars_remaining"),
        "Minutes Remaining": post_exit_trend.get("minutes_remaining"),
        "Peak Price": post_exit_trend.get("peak_price"),
        "Peak Time": post_exit_trend.get("peak_time"),
        "Exit Quality": exit_quality,
        "Exit Verdict": exit_verdict,
        "Exit Comments": exit_comments(exit_verdict),
        "Primary Exit": primary_exit,
        "Secondary Exits": _list_text(secondary_exits),
        "Ignored Exit Signals": _list_text(ignored_exit_signals),
        "Triggered EMA": _triggered(primary_exit, "EMA"),
        "Triggered VWAP": _triggered(primary_exit, "VWAP"),
        "Triggered MACD": _triggered(primary_exit, "MACD"),
        "Triggered Stop": _triggered(primary_exit, "STOP"),
        "Triggered Target": _triggered(primary_exit, "TARGET"),
        "Triggered Time Exit": _triggered(primary_exit, "TIME"),
        "Triggered Near Close": _triggered(primary_exit, "NEAR_CLOSE", "NEAR CLOSE"),
        "Profit +1 Bar": exit_delay.get("profit_1_bar"),
        "Profit +2 Bars": exit_delay.get("profit_2_bars"),
        "Profit +3 Bars": exit_delay.get("profit_3_bars"),
        "Profit +5 Bars": exit_delay.get("profit_5_bars"),
        "Best Delay": exit_delay.get("best_delay"),
        "Best Profit": exit_delay.get("best_profit"),
        "Delay Recommendation": exit_delay.get("delay_recommendation"),
        "Trade Efficiency Score": None,
    }
    row["Trade Efficiency Score"] = calculate_trade_efficiency_score(
        pd.DataFrame([row])
    )

    return row


def append_trend_capture_row(trading_day, row):

    path = daily_path(
        trading_day,
        "trend_capture_analysis.csv"
    )
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )
    write_header = not path.exists() or path.stat().st_size == 0

    with path.open("a", newline="", encoding="utf-8") as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=TREND_CAPTURE_COLUMNS
        )

        if write_header:

            writer.writeheader()

        writer.writerow(
            {
                column: row.get(column)
                for column in TREND_CAPTURE_COLUMNS
            }
        )

    return path


def summarize_trend_capture(df):

    if df is None or df.empty:

        return {
            "average_capture": None,
            "median_capture": None,
            "average_mfe": None,
            "average_mae": None,
            "average_left_on_table": None,
            "best_capture": None,
            "worst_capture": None,
            "most_left_on_table": None,
            "by_setup": pd.DataFrame(),
            "by_regime": pd.DataFrame(),
            "by_exit_reason": pd.DataFrame(),
            "exit_verdict_distribution": pd.DataFrame(),
            "average_trend_health": None,
            "average_bars_remaining": None,
            "average_delay_gain": None,
            "trade_efficiency_score": None,
            "engineering_recommendation": None,
            "recommendation": None,
        }

    data = df.copy()

    for column in [
        "Trend Capture %",
        "Maximum Favorable Excursion",
        "Maximum Adverse Excursion",
        "Left On Table",
        "Trend Health Score",
        "Bars Remaining",
        "Remaining Move",
        "Remaining %",
        "Profit +1 Bar",
        "Profit +2 Bars",
        "Profit +3 Bars",
        "Profit +5 Bars",
        "Best Profit",
        "Trade Efficiency Score",
    ]:

        if column in data.columns:

            data[column] = pd.to_numeric(
                data[column],
                errors="coerce"
            )

    capture = data.get("Trend Capture %", pd.Series(dtype=float)).dropna()
    left_on_table = data.get("Left On Table", pd.Series(dtype=float)).dropna()
    trend_health = data.get("Trend Health Score", pd.Series(dtype=float)).dropna()
    bars_remaining = data.get("Bars Remaining", pd.Series(dtype=float)).dropna()
    delay_gain = data.get("Profit +2 Bars", pd.Series(dtype=float)).dropna()
    trade_efficiency_score = data.get("Trade Efficiency Score", pd.Series(dtype=float)).dropna()
    average_capture = (
        round(float(capture.mean()), 2)
        if not capture.empty
        else None
    )
    engineering_recommendation = generate_trade_efficiency_recommendation(data)
    recommendation = None

    if average_capture is not None and average_capture < 55:

        recommendation = engineering_recommendation.get(
            "recommendation",
            "Improve trend management. Entries are finding trends but exits are leaving significant profit."
        )

    def grouped_average(group_column):

        if group_column not in data.columns or "Trend Capture %" not in data.columns:

            return pd.DataFrame()

        return (
            data.groupby(group_column, dropna=True)["Trend Capture %"]
            .mean()
            .round(2)
            .reset_index(name="Average Trend Capture %")
            .sort_values("Average Trend Capture %", ascending=False)
        )

    return {
        "average_capture": average_capture,
        "median_capture": (
            round(float(capture.median()), 2)
            if not capture.empty
            else None
        ),
        "average_mfe": (
            round(float(data["Maximum Favorable Excursion"].mean()), 4)
            if "Maximum Favorable Excursion" in data.columns
            else None
        ),
        "average_mae": (
            round(float(data["Maximum Adverse Excursion"].mean()), 4)
            if "Maximum Adverse Excursion" in data.columns
            else None
        ),
        "average_left_on_table": (
            round(float(left_on_table.mean()), 4)
            if not left_on_table.empty
            else None
        ),
        "best_capture": (
            round(float(capture.max()), 2)
            if not capture.empty
            else None
        ),
        "worst_capture": (
            round(float(capture.min()), 2)
            if not capture.empty
            else None
        ),
        "most_left_on_table": (
            round(float(left_on_table.max()), 4)
            if not left_on_table.empty
            else None
        ),
        "by_setup": grouped_average("Setup"),
        "by_regime": grouped_average("Market Regime"),
        "by_exit_reason": grouped_average("Exit Reason"),
        "exit_verdict_distribution": (
            data["Exit Verdict"]
            .fillna("UNKNOWN")
            .astype(str)
            .value_counts()
            .rename_axis("Exit Verdict")
            .reset_index(name="Count")
            if "Exit Verdict" in data.columns
            else pd.DataFrame()
        ),
        "average_trend_health": (
            round(float(trend_health.mean()), 2)
            if not trend_health.empty
            else None
        ),
        "average_bars_remaining": (
            round(float(bars_remaining.mean()), 2)
            if not bars_remaining.empty
            else None
        ),
        "average_delay_gain": (
            round(float(delay_gain.mean()), 4)
            if not delay_gain.empty
            else None
        ),
        "trade_efficiency_score": (
            round(float(trade_efficiency_score.mean()), 2)
            if not trade_efficiency_score.empty
            else calculate_trade_efficiency_score(data)
        ),
        "engineering_recommendation": engineering_recommendation,
        "recommendation": recommendation,
    }