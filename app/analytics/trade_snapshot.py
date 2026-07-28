from __future__ import annotations

import csv

import pandas as pd

from app.storage.daily_paths import daily_path


TRADE_EXIT_SNAPSHOT_COLUMNS = [
    "trade_key",
    "symbol",
    "direction",
    "setup",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "ema9",
    "ema20",
    "vwap",
    "macd",
    "macd_signal",
    "macd_hist",
    "rsi",
    "atr",
    "volume",
    "relative_volume",
    "higher_high",
    "higher_low",
    "lower_high",
    "lower_low",
    "price_above_ema9",
    "price_above_vwap",
    "ema_alignment",
    "macd_bullish",
    "trend_health_score",
    "trend_health_state",
    "exit_reason",
    "bars_held",
    "option_ticker",
    "option_entry_mid",
    "option_exit_mid",
    "option_exit_quote_source",
    "r_multiple_net",
    "pnl_option_est",
    "cost_total",
    "pnl_source",
]


def _get(mapping, *names, default=None):

    for name in names:

        try:

            value = mapping.get(name)

        except Exception:

            value = None

        if value is not None and str(value).strip().lower() not in {"", "nan", "none"}:

            return value

    return default


def _float(value, default=None):

    try:

        if value is None or pd.isna(value):

            return default

        return float(value)

    except Exception:

        return default


def _bool(value):

    if isinstance(value, bool):

        return value

    return str(value).strip().lower() in {"true", "1", "yes", "y", "on"}


def _indicator_value(latest_bar, indicators, *names):

    return _get(
        indicators or {},
        *names,
        default=_get(latest_bar or {}, *names)
    )


def build_trade_snapshot(
    trade,
    latest_bar,
    indicators,
    trend_health
):

    trade = trade or {}
    latest_bar = latest_bar or {}
    indicators = indicators or {}
    scanner_context = trade.get("scanner_context") or trade.get("close_scanner_context") or {}
    close = _float(_indicator_value(latest_bar, indicators, "Close"))
    ema9 = _float(_indicator_value(latest_bar, indicators, "EMA9"))
    ema20 = _float(_indicator_value(latest_bar, indicators, "EMA20"))
    vwap = _float(_indicator_value(latest_bar, indicators, "VWAP"))
    macd = _float(_indicator_value(latest_bar, indicators, "MACD"))
    macd_signal = _float(_indicator_value(latest_bar, indicators, "MACD_SIGNAL"))

    return {
        "trade_key": trade.get("trade_key"),
        "symbol": trade.get("symbol"),
        "direction": trade.get("direction"),
        "setup": trade.get("setup_type") or trade.get("entry_type") or scanner_context.get("Entry"),
        "entry_time": trade.get("opened_at_et") or trade.get("opened_at"),
        "exit_time": trade.get("closed_at_et") or trade.get("closed_at"),
        "entry_price": trade.get("entry_price"),
        "exit_price": trade.get("close_price") or trade.get("exit_price"),
        "ema9": ema9,
        "ema20": ema20,
        "vwap": vwap,
        "macd": macd,
        "macd_signal": macd_signal,
        "macd_hist": _float(_indicator_value(latest_bar, indicators, "MACD_HIST")),
        "rsi": _float(_indicator_value(latest_bar, indicators, "RSI")),
        "atr": _float(_indicator_value(latest_bar, indicators, "ATR")),
        "volume": _float(_indicator_value(latest_bar, indicators, "Volume", "volume")),
        "relative_volume": _float(_indicator_value(latest_bar, indicators, "REL_VOLUME")),
        "higher_high": _bool(_indicator_value(latest_bar, indicators, "HIGHER_HIGH")),
        "higher_low": _bool(_indicator_value(latest_bar, indicators, "HIGHER_LOW")),
        "lower_high": _bool(_indicator_value(latest_bar, indicators, "LOWER_HIGH")),
        "lower_low": _bool(_indicator_value(latest_bar, indicators, "LOWER_LOW")),
        "price_above_ema9": close is not None and ema9 is not None and close > ema9,
        "price_above_vwap": close is not None and vwap is not None and close > vwap,
        "ema_alignment": ema9 is not None and ema20 is not None and ema9 > ema20,
        "macd_bullish": macd is not None and macd_signal is not None and macd > macd_signal,
        "trend_health_score": (trend_health or {}).get("score"),
        "trend_health_state": (trend_health or {}).get("state"),
        "exit_reason": trade.get("exit_reason"),
        "bars_held": trade.get("bars_held"),
    }


def append_trade_exit_snapshot(trading_day, snapshot):

    path = daily_path(
        trading_day,
        "trade_exit_snapshots.csv"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    columns = TRADE_EXIT_SNAPSHOT_COLUMNS

    if not write_header:

        columns = _existing_header(path) or TRADE_EXIT_SNAPSHOT_COLUMNS

    with path.open("a", newline="", encoding="utf-8") as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=columns
        )

        if write_header:

            writer.writeheader()

        writer.writerow({
            column: snapshot.get(column)
            for column in columns
        })

    return path


def _existing_header(path):

    """Append against the file's own header.

    A day file written before a column was added keeps its original header;
    writing today's wider row into it would misalign every value. Falling back
    to the file's header drops the new columns for that day instead.
    """

    try:

        with path.open("r", newline="", encoding="utf-8") as handle:

            header = next(csv.reader(handle), None)

        return header or None

    except Exception:

        return None