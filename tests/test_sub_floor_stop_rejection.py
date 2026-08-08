"""A stop that had to be invented is not a stop.

MIN_STOP_DISTANCE_PCT rescues a setup whose structure gives less distance than
the option spread needs, by widening the stop to the floor. What the floor
actually detects is a setup with no usable stop distance, and over 5-7 Aug 2026
every one of the seven alerted trades whose stop landed on it lost -- holds of
6, 10, 13, 20, 22 and 32 minutes. REJECT_SUB_FLOOR_STOPS declines those instead.
"""

import pandas as pd
import pytest

from app.risk.risk_manager import calculate_risk


def frame(atr, close=100.0, ema9=99.98, vwap=99.97):
    """Bars whose structure sits far inside any sensible floor.

    A tiny ATR with the EMA almost touching price is the shape that produced
    0.13%-0.36% stops: the pullback is real but there is nowhere to put a stop.
    """

    return pd.DataFrame([
        {
            "High": close + atr / 2,
            "Low": close - atr / 2,
            "Close": close,
            "ATR": atr,
            "EMA9": ema9,
            "VWAP": vwap,
            "ROLLING_RESISTANCE": close + 4.0,
            "ROLLING_SUPPORT": close - 4.0,
            "PREV_HIGH": close + 3.0,
            "PREV_LOW": close - 3.0,
        }
    ] * 20)


def run(df, **env):

    return calculate_risk(
        df=df,
        analysis={"signal": "BULLISH", "market_regime": "TRENDING_BULL"},
        entry_setup={
            "entry_type": "EMA_PULLBACK",
            "entry_quality": "HIGH",
            "avoid_chasing": False,
        },
    )


@pytest.fixture
def reject_on(monkeypatch):

    monkeypatch.setenv("REJECT_SUB_FLOOR_STOPS", "true")


def sub_floor_frame():
    """Structure stop well under 0.50% of a $100 price."""

    return frame(atr=0.08, ema9=99.90, vwap=99.88)


def test_the_flag_is_off_by_default():
    """Existing behaviour is untouched until the archive says otherwise."""

    result = run(sub_floor_frame())

    assert result["trade_allowed"] is True
    assert not any(
        "floor would have invented" in reason for reason in result["reasons"]
    )


def test_a_sub_floor_stop_is_declined_when_enabled(reject_on):

    result = run(sub_floor_frame())

    assert result["trade_allowed"] is False
    assert any(
        "floor would have invented" in reason for reason in result["reasons"]
    )


def test_a_real_structure_stop_is_untouched(reject_on):
    """The floor only fires where structure gave nothing; this gave 0.70%.

    Kept between the 0.50% floor and the 0.95% "stop too wide" cap for
    TRENDING, so the only rule that could reject it is the one under test.
    """

    result = run(frame(atr=1.00, ema9=99.40, vwap=99.35))

    assert result["trade_allowed"] is True
    assert not any(
        "floor would have invented" in reason for reason in result["reasons"]
    )
    # And it is genuinely wider than the floor it was compared against.
    assert abs(100.0 - result["stop_loss"]) > 100.0 * 0.005


def test_the_floor_threshold_is_the_one_that_decides(monkeypatch):
    """Rejection follows MIN_STOP_DISTANCE_PCT rather than a second constant."""

    monkeypatch.setenv("REJECT_SUB_FLOOR_STOPS", "true")
    monkeypatch.setenv("MIN_STOP_DISTANCE_PCT", "0.01")

    # A floor this low is below the structure distance, so nothing is invented.
    result = run(sub_floor_frame())

    assert result["trade_allowed"] is True
