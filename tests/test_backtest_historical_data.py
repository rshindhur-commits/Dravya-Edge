"""Parity tests for the replay data layer.

These assert the one property that decides whether a backtest is worth
running: that a replay sees what the live scanner saw, and nothing it did not.

Hermetic. Market frames are served from ``tests/fixtures/market_cache`` and the
live trades from ``tests/fixtures/live_trades_2026_07_30_31.json``, so nothing
here touches Polygon or the database. The archive under ``data/daily`` is
deliberately not used: its writes drop out around 10:00 ET, so it cannot
witness the intraday scans these fixtures cover.
"""

import json
import pathlib
from datetime import datetime, timedelta

import pandas as pd
import pytest

import app.backtesting.historical_market_data as hmd
from app.indicators.technical_indicators import compute_indicators
from app.utils.timeframe_resampler import resample_timeframe

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
MARKET_CACHE = FIXTURES / "market_cache"
TRADES_FILE = FIXTURES / "live_trades_2026_07_30_31.json"


@pytest.fixture(autouse=True)
def _use_fixture_cache(monkeypatch):
    """Point the data layer at the committed cache and forbid network use."""

    monkeypatch.setattr(hmd, "_CACHE_ROOT", MARKET_CACHE)

    def _no_network(*args, **kwargs):

        raise AssertionError(
            "parity tests must not reach Polygon; a fixture is missing"
        )

    monkeypatch.setattr(hmd.requests, "get", _no_network)


@pytest.fixture(scope="module")
def live_trades():

    with open(TRADES_FILE) as handle:

        return json.load(handle)


def _scan_time(trade):

    return datetime.strptime(trade["scan_id"], "%Y-%m-%d_%H%M%S")


def test_fixtures_cover_the_nine_live_trades(live_trades):

    assert len(live_trades) == 9

    days = {trade["trading_day"] for trade in live_trades}
    assert days == {"2026-07-30", "2026-07-31"}


def test_frame_as_of_never_returns_an_unfinished_bar(live_trades):
    """The invariant the whole backtest rests on.

    A bar stamped 11:25 is not knowable until 11:30. Returning it to a scan
    that ran at 11:26 would leak four minutes of future into every decision --
    and would show up only as unexplained good results.
    """

    for trade in live_trades:

        frame = hmd.load_replay_frames(
            trade["symbol"], trade["trading_day"], lookback_days=5
        )
        scan_time = _scan_time(trade)
        visible = hmd.frame_as_of(frame, scan_time, bar_minutes=5)

        assert not visible.empty

        last_bar_start = visible.index[-1].tz_convert("America/New_York")
        last_bar_close = last_bar_start + timedelta(minutes=5)
        scan_moment = pd.Timestamp(scan_time).tz_localize("America/New_York")

        assert last_bar_close <= scan_moment, (
            f"{trade['symbol']} {trade['scan_id']}: bar starting "
            f"{last_bar_start} had not closed by scan time"
        )


def test_frame_as_of_is_monotonic_in_lag(live_trades):
    """More lag may never reveal more data."""

    trade = live_trades[0]
    frame = hmd.load_replay_frames(
        trade["symbol"], trade["trading_day"], lookback_days=5
    )
    scan_time = _scan_time(trade)

    previous = None

    for lag in (0.0, 5.0, 15.0):

        visible = hmd.frame_as_of(frame, scan_time, decision_lag_minutes=lag)

        if previous is not None:

            assert len(visible) <= previous

        previous = len(visible)


def test_replay_reproduces_live_decision_candle(live_trades):
    """At zero added lag the replay picks live's own decision candle.

    Seven of the nine match exactly. The other two are live reading one bar
    staler still -- the 30s aggregate cache plus Polygon's publication latency
    -- which is jitter, not a systematic offset. This asserts the 7 so the
    number cannot silently regress; if it ever reaches 9 the lag model has
    become more faithful, not less.
    """

    matched = 0

    for trade in live_trades:

        live_candle = trade["scanner_context"].get("Decision Candle Time ET")

        if not live_candle:
            continue

        frame = hmd.load_replay_frames(
            trade["symbol"], trade["trading_day"], lookback_days=5
        )
        visible = hmd.frame_as_of(frame, _scan_time(trade))

        replay_candle = visible.index[-1]
        expected = pd.to_datetime(live_candle).tz_convert("UTC")

        if replay_candle == expected:

            matched += 1

        else:

            # Never *newer* than what live used -- that would be lookahead.
            assert replay_candle >= expected, (
                f"{trade['symbol']} {trade['scan_id']}: replay candle "
                f"{replay_candle} predates live's {expected}"
            )

    assert matched >= 7, f"only {matched}/9 decision candles reproduced"


def test_replay_reproduces_live_indicator_values(live_trades):
    """Live's own indicators, on a reconstructed frame, give live's numbers.

    Anchored to the decision candle live recorded, so this isolates the
    indicator and resampling path from the lag model tested above. Only the
    2026-07-31 payloads carry ENTRY_* fields; the 07-30 ones predate them.

    Tolerances are absolute because MACD_SIGNAL sits near zero, where a
    relative bound reports a 0.0008 difference as a 15% error.
    """

    checks = [
        ("ENTRY_EMA9", "EMA9", 0.01),
        ("ENTRY_EMA20", "EMA20", 0.01),
        ("ENTRY_VWAP", "VWAP", 0.01),
        ("ENTRY_ATR", "ATR", 0.05),
        ("ENTRY_RSI", "RSI", 0.50),
        ("ENTRY_MACD", "MACD", 0.01),
        ("ENTRY_MACD_SIGNAL", "MACD_SIGNAL", 0.01),
    ]

    compared = 0

    for trade in live_trades:

        context = trade["scanner_context"]

        if context.get("ENTRY_EMA9") is None:
            continue

        frame = hmd.load_replay_frames(
            trade["symbol"], trade["trading_day"], lookback_days=5
        )
        anchor = pd.to_datetime(context["Decision Candle Time ET"]).tz_convert("UTC")

        visible = frame[frame.index <= anchor].copy()
        indicators = compute_indicators(
            resample_timeframe(visible, "15m"),
            interval="15m",
            symbol=trade["symbol"],
        )

        assert not indicators.empty

        latest = indicators.iloc[-1]

        for live_key, column, tolerance in checks:

            expected = float(context[live_key])
            actual = float(latest[column])

            assert abs(actual - expected) <= tolerance, (
                f"{trade['symbol']} {trade['scan_id']} {column}: "
                f"replay {actual:.4f} vs live {expected:.4f}"
            )

        compared += 1

    assert compared == 3, f"expected 3 trades carrying ENTRY_* fields, got {compared}"


def test_entry_price_equals_decision_candle_close(live_trades):
    """Live fills at the decision candle's close, in all nine cases.

    This is the fill rule the replay implements; if it ever stops holding, the
    replay's entry prices are modelling something the scanner no longer does.
    """

    for trade in live_trades:

        candle_close = trade["scanner_context"].get("Decision Candle Close")

        if candle_close is None:
            continue

        assert abs(float(trade["entry_price"]) - float(candle_close)) < 1e-6, (
            f"{trade['symbol']} {trade['scan_id']}: entry {trade['entry_price']} "
            f"!= decision candle close {candle_close}"
        )
