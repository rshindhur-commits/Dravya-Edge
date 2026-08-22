"""Trend health must be read the trade's own way.

`build_trade_snapshot` built every health input unconditionally bullish --
`close > vwap`, `ema9 > ema20`, `macd > macd_signal`, HIGHER_HIGH/HIGHER_LOW --
and handed them to `evaluate_trend_health`, which scores each as a point of
health. For a PUT all of them are backwards.

Ten of the fifteen trades closed 2026-08-19 to 08-21 were PUTs. NVDA's 08-21
exit recorded 12/12 STRONG on inputs that read short are 1/12 BROKEN, and
`classify_exit_verdict` wrote "Trend remained strong after exit" off that.
"""

import pytest

from app.analytics.trade_snapshot import build_trade_snapshot
from app.analytics.trend_health import evaluate_trend_health, trend_health_state


# A textbook short: price under both averages, averages stacked bearish, MACD
# bearish, lower highs and lows, RSI weak. Every reading a PUT wants.
WORKING_SHORT = {
    "Close": 95.0, "EMA9": 98.0, "EMA20": 100.0, "VWAP": 99.0,
    "MACD": -0.5, "MACD_SIGNAL": -0.2, "RSI": 32.0, "REL_VOLUME": 1.4,
    "HIGHER_HIGH": False, "HIGHER_LOW": False, "LOWER_HIGH": True, "LOWER_LOW": True,
}

# The same chart, which is a textbook long.
WORKING_LONG = {
    "Close": 105.0, "EMA9": 102.0, "EMA20": 100.0, "VWAP": 101.0,
    "MACD": 0.5, "MACD_SIGNAL": 0.2, "RSI": 68.0, "REL_VOLUME": 1.4,
    "HIGHER_HIGH": True, "HIGHER_LOW": True, "LOWER_HIGH": False, "LOWER_LOW": False,
}


def _score(bar, direction):
    snapshot = build_trade_snapshot({"direction": direction}, bar, {}, {})
    return evaluate_trend_health(snapshot)["score"]


def test_a_working_short_scores_strong_not_broken():
    """The regression. This returned 0 and called a perfect short BROKEN."""

    score = _score(WORKING_SHORT, "PUT")

    assert score == 12, f"a textbook short scored {score}/12"
    assert trend_health_state(score) == "STRONG"


def test_a_failing_short_scores_broken_not_strong():
    """The mirror, and the one that did real damage: NVDA read 12/12 here."""

    score = _score(WORKING_LONG, "PUT")

    assert score <= 1, f"a short with everything against it scored {score}/12"
    assert trend_health_state(score) == "BROKEN"


def test_longs_are_unchanged():
    """Five of the fifteen were CALLs and their scores must not move.

    A long looking at the short's chart scores 1, not 0: `relative_volume` is
    the one check with no direction to it -- heavy volume is heavy volume -- so
    it keeps its point on both sides. Everything directional reads zero.
    """

    assert _score(WORKING_LONG, "CALL") == 12
    assert _score(WORKING_SHORT, "CALL") == 1


def test_the_same_chart_scores_opposite_for_the_two_directions():

    assert _score(WORKING_SHORT, "PUT") == _score(WORKING_LONG, "CALL")
    assert _score(WORKING_SHORT, "CALL") == _score(WORKING_LONG, "PUT")


def test_raw_readings_stay_raw():
    """Only the health inputs are oriented. A short's chart is still a chart.

    `Price Above VWAP` is reported to a human and must keep meaning what it says,
    so the reported booleans are not flipped -- only `trend_inputs` is.
    """

    snapshot = build_trade_snapshot({"direction": "PUT"}, WORKING_SHORT, {}, {})

    assert snapshot["price_above_vwap"] is False, "95 is not above a VWAP of 99"
    assert snapshot["ema_alignment"] is False, "ema9 98 is not above ema20 100"
    assert snapshot["trend_inputs"]["price_above_vwap"] is True, "but the short is healthy"


def test_missing_direction_is_treated_as_long():
    """The historical default. Nothing regresses for rows without a direction."""

    assert _score(WORKING_LONG, None) == 12


def test_archived_flat_snapshots_still_resolve():
    """44 rows were written before `trend_inputs` existed."""

    legacy = {
        "ema_alignment": True, "price_above_ema9": True, "price_above_vwap": True,
        "higher_high": True, "higher_low": True, "macd_bullish": True,
        "rsi": 65, "relative_volume": 1.5,
    }

    assert evaluate_trend_health(legacy)["score"] == 12
