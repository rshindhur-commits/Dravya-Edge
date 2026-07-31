"""Daily-timeframe trend, levels and realised volatility, cached per trading day.

Every frame the scanner uses is resampled from the same 5-minute pull: 15m and 1h
come out of df_5m_raw, and nothing looks further out than that. A candidate can
therefore be a textbook 15-minute pullback while the daily chart has been in a
downtrend for three weeks, and nothing in the pipeline can see the difference.

That matters most for the trades held longest. A MULTIDAY position taken against
the daily trend pays theta overnight on a thesis the higher timeframe disagrees
with, and prior-day high/low/close are the levels the whole market watches -- they
are where intraday moves stall, which is exactly where a 15-minute breakout looks
best and works worst.

The daily frame also gives a much better realised-volatility estimate than the
15-minute one. app/risk/iv_richness.py has to convert a 15m ATR into an annual
figure through 6,552 bars and a range-to-sigma constant; from daily bars the same
number is one square root of 252 away, so the IV/RV ratio stops depending on the
shakiest part of that chain.

Cached by trading day rather than by seconds. Daily bars do not change during the
session, so 26 symbols cost 26 requests on the first scan of the day and nothing
after that -- POLYGON_CACHE_TTL is 30 seconds, which would refetch all of it on
every scan.

Every failure path returns an empty context. A missing daily frame must never
block a trade: it is a data gap, not a bearish signal.
"""

from __future__ import annotations

import math
import os
from datetime import datetime
from zoneinfo import ZoneInfo


MARKET_TZ = ZoneInfo("America/New_York")

# Calendar days requested. ~82 trading days, comfortably above the 20-bar minimum
# compute_indicators() needs and enough for a 50-period view of trend.
DEFAULT_LOOKBACK_DAYS = 120

# Daily ATR is a range and overstates standard deviation; see iv_richness for the
# same constant and reasoning.
RANGE_TO_SIGMA = 1.6
TRADING_DAYS_PER_YEAR = 252

_cache: dict = {}


def _number(value):
    if value is None or value == "":
        return None

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if result != result or result in (float("inf"), float("-inf")):
        return None

    return result


def _lookback_days():
    try:
        return int(os.getenv("DAILY_CONTEXT_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS))
    except ValueError:
        return DEFAULT_LOOKBACK_DAYS


def daily_context_enabled():
    return str(
        os.getenv("DAILY_CONTEXT_ENABLED", "true") or "true"
    ).strip().lower() not in {"false", "0", "no", "off"}


def empty_context(reason="NO_DAILY_DATA"):
    return {
        "daily_trend": "UNKNOWN",
        "daily_trend_reason": reason,
        "daily_atr_pct": None,
        "daily_realised_vol": None,
        "prior_day_high": None,
        "prior_day_low": None,
        "prior_day_close": None,
        "above_prior_day_high": None,
        "below_prior_day_low": None,
        "daily_close": None,
        "daily_ema9": None,
        "daily_ema20": None,
    }


def annualised_realised_vol(daily_atr_pct):
    """Annualised realised volatility, in percent, from daily ATR percent."""

    atr_pct = _number(daily_atr_pct)

    if atr_pct is None or atr_pct <= 0:
        return None

    sigma_per_day = (atr_pct / 100.0) / RANGE_TO_SIGMA

    return sigma_per_day * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0


def _classify_daily_trend(close, ema9, ema20):
    """BULL, BEAR or NEUTRAL from the daily moving-average structure.

    Deliberately coarse. This is a context filter, not a signal: it only has to
    answer whether a candidate is trading with or against the higher timeframe, and
    a finer classification would invite treating it as an entry trigger.
    """

    if close is None or ema9 is None or ema20 is None:
        return "UNKNOWN", "MISSING_DAILY_LEVELS"

    if close > ema9 > ema20:
        return "BULL", "CLOSE_ABOVE_RISING_EMAS"

    if close < ema9 < ema20:
        return "BEAR", "CLOSE_BELOW_FALLING_EMAS"

    return "NEUTRAL", "EMAS_MIXED"


def build_daily_context(df_daily):
    """Derive the context from a daily frame that already carries indicators."""

    if df_daily is None or df_daily.empty or len(df_daily) < 2:
        return empty_context("INSUFFICIENT_DAILY_BARS")

    latest = df_daily.iloc[-1]
    prior = df_daily.iloc[-2]

    close = _number(latest.get("Close"))
    ema9 = _number(latest.get("EMA9"))
    ema20 = _number(latest.get("EMA20"))
    atr_pct = _number(latest.get("ATR_PCT"))

    trend, reason = _classify_daily_trend(close, ema9, ema20)

    # The prior completed session, not the one in progress: these levels are only
    # meaningful because the whole market saw them settle.
    prior_high = _number(prior.get("High"))
    prior_low = _number(prior.get("Low"))
    prior_close = _number(prior.get("Close"))

    return {
        "daily_trend": trend,
        "daily_trend_reason": reason,
        "daily_atr_pct": atr_pct,
        "daily_realised_vol": annualised_realised_vol(atr_pct),
        "prior_day_high": prior_high,
        "prior_day_low": prior_low,
        "prior_day_close": prior_close,
        "above_prior_day_high": (
            close > prior_high if close is not None and prior_high is not None else None
        ),
        "below_prior_day_low": (
            close < prior_low if close is not None and prior_low is not None else None
        ),
        "daily_close": close,
        "daily_ema9": ema9,
        "daily_ema20": ema20,
    }


def _trading_day_key(now=None):
    return (now or datetime.now(MARKET_TZ)).date().isoformat()


def daily_context(symbol, force_refresh=False, now=None):
    """Cached daily context for one symbol. Never raises."""

    symbol = str(symbol or "").strip().upper()

    if not symbol or not daily_context_enabled():
        return empty_context("DAILY_CONTEXT_DISABLED")

    key = (symbol, _trading_day_key(now))

    if not force_refresh and key in _cache:
        return _cache[key]

    try:
        from app.indicators.technical_indicators import (
            compute_indicators,
            get_polygon_data,
        )

        raw = get_polygon_data(symbol, 1, "day", _lookback_days())

        if raw is None or raw.empty:
            context = empty_context("NO_DAILY_DATA")

        else:
            context = build_daily_context(
                compute_indicators(raw, interval="1d", symbol=symbol)
            )

    except Exception as exc:
        print(f"[DAILY CONTEXT WARNING] {symbol}: {exc}")
        context = empty_context("DAILY_CONTEXT_ERROR")

    _cache[key] = context

    return context


def clear_cache():
    _cache.clear()
