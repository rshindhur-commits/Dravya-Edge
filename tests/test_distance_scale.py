"""The two knobs that decide how big a move this strategy hunts.

Stops are clamped between a 0.50% floor and a 0.75-1.15% cap, so with 2R targets
the largest move ever aimed at is roughly 1.5-2.3% of price, against an option
round trip of 1.5-3.4%. Mean peak across 21 archived sessions is 0.39R, about
0.3% of price.

ATR_DISTANCE_SCALE widens the trade; MAX_STOP_DISTANCE_SCALE stops the cap
rejecting the result. Each looks sufficient alone and neither is -- which is the
failure mode that made three earlier arms in this project byte-identical to
their baselines, so both directions are tested here.
"""

import pandas as pd
import pytest

from app.risk.risk_manager import calculate_risk


def frame(atr=1.0, close=100.0):

    return pd.DataFrame([
        {
            "High": close + atr / 2, "Low": close - atr / 2, "Close": close,
            "ATR": atr, "EMA9": close - 0.6, "VWAP": close - 0.65,
            "ROLLING_RESISTANCE": close + 8.0, "ROLLING_SUPPORT": close - 8.0,
            "PREV_HIGH": close + 6.0, "PREV_LOW": close - 6.0,
        }
    ] * 20)


def run(regime="TRENDING_BULL"):

    return calculate_risk(
        df=frame(),
        analysis={"signal": "BULLISH", "market_regime": regime},
        entry_setup={
            "entry_type": "EMA_PULLBACK",
            "entry_quality": "HIGH",
            "avoid_chasing": False,
        },
    )


def stop_pct(result):

    return abs(100.0 - result["stop_loss"]) / 100.0 * 100.0


def test_defaults_are_unchanged():

    result = run()

    assert result["trade_allowed"] is True
    assert stop_pct(result) <= 0.95


def test_widening_alone_is_rejected_by_the_cap(monkeypatch):
    """The failure mode: one knob moved, the arm comes back identical."""

    monkeypatch.setenv("ATR_DISTANCE_SCALE", "8")

    result = run()

    assert result["trade_allowed"] is False
    assert any("Stop too wide" in reason for reason in result["reasons"])


def test_raising_the_cap_alone_changes_nothing(monkeypatch):
    """The other half of the same failure mode."""

    baseline = stop_pct(run())

    monkeypatch.setenv("MAX_STOP_DISTANCE_SCALE", "4")

    assert stop_pct(run()) == pytest.approx(baseline)


def test_both_together_widen_the_stop(monkeypatch):

    baseline = stop_pct(run())

    monkeypatch.setenv("ATR_DISTANCE_SCALE", "4")
    monkeypatch.setenv("MAX_STOP_DISTANCE_SCALE", "6")

    result = run()

    assert result["trade_allowed"] is True
    assert stop_pct(result) > baseline


def test_the_scale_is_a_weak_lever_because_the_bar_dominates(monkeypatch):
    """The measurement that matters more than the knob.

    The stop is min(bar low - atr*0.15*s, EMA9 - atr*0.10*s). Only the offset
    scales; the bar's own low does not, and on a 15m frame that low sits a
    fraction of a percent from price. So a fourfold scale buys roughly a
    1.5-fold stop -- 0.70% to 1.10% here -- and reaching the 3% stop a
    multi-percent move needs would take a scale near fourteen.

    Hunting larger moves therefore needs the anchor moved to a higher
    timeframe, not this offset enlarged. Pinned so that conclusion is not
    quietly lost.
    """

    monkeypatch.setenv("MAX_STOP_DISTANCE_SCALE", "6")
    baseline = stop_pct(run())

    monkeypatch.setenv("ATR_DISTANCE_SCALE", "4")
    widened = stop_pct(run())

    assert widened / baseline < 2.0, "a 4x offset must not be read as a 4x stop"


def test_a_wider_stop_against_a_structure_target_costs_reward_to_risk(monkeypatch):
    """The target is max(resistance, entry + atr*1.8*s), so resistance can pin it.

    When it does, widening the stop lowers RR rather than preserving it -- which
    is what the entry gate's min_rr will see.
    """

    before = run()["risk_reward"]

    monkeypatch.setenv("ATR_DISTANCE_SCALE", "3")
    monkeypatch.setenv("MAX_STOP_DISTANCE_SCALE", "6")

    after = run()["risk_reward"]

    assert after < before
    assert after >= 2.0
