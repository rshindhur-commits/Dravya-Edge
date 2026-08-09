"""The stop's anchor, which is what decides how big a move the app can hunt.

The 15m anchor produces 0.5-0.75% stops against option round trips of 1.5-3.4%,
so the strategy cannot reach a move that pays for its own instrument. These
tests hold the higher-timeframe anchor to the thing that has to be true for it
to matter -- a materially wider stop -- and to the three ways an arm like this
silently becomes its own control.
"""

import pandas as pd
import pytest

from app.risk import swing_anchor
from app.risk.risk_manager import calculate_risk


def frame(atr=1.0, close=100.0, rows=20):
    """A 15m frame whose bar sits a fraction of a percent from price."""

    return pd.DataFrame([
        {
            "High": close + atr / 2, "Low": close - atr / 2, "Close": close,
            "ATR": atr, "EMA9": close - 0.6, "VWAP": close - 0.65,
            "ROLLING_RESISTANCE": close + 8.0, "ROLLING_SUPPORT": close - 8.0,
            "PREV_HIGH": close + 6.0, "PREV_LOW": close - 6.0,
        }
    ] * rows)


def hourly(
    close=100.0,
    swing_low=97.0,
    swing_high=103.0,
    atr=1.6,
    rows=12,
    resistance=115.0,
    support=85.0,
):
    """A 1h frame carrying a swing 3% away -- what a 1h pivot looks like."""

    rows_out = [
        {
            "High": close + 0.5, "Low": close - 0.5, "Close": close, "ATR": atr,
            "EMA9": close - 0.4,
            "ROLLING_RESISTANCE": resistance, "ROLLING_SUPPORT": support,
        }
        for _ in range(rows)
    ]

    # The bar that actually made the swing. Placed inside the lookback window
    # on purpose: put it outside and the pivot degenerates to the flat bars,
    # the stop falls back to the 1% floor, and every assertion below reads as a
    # code failure when it is a fixture failure. That is worth stating, because
    # it is the same mistake as running the arm with a lookback too short to
    # contain any real swing.
    swing_bar = rows - 4
    rows_out[swing_bar]["Low"] = swing_low
    rows_out[swing_bar]["High"] = swing_high

    return pd.DataFrame(rows_out)


def run(htf=None, regime="TRENDING_BULL", signal="BULLISH", entry_type="EMA_PULLBACK"):

    return calculate_risk(
        df=frame(),
        analysis={"signal": signal, "market_regime": regime},
        entry_setup={
            "entry_type": entry_type,
            "entry_quality": "HIGH",
            "avoid_chasing": False,
        },
        htf=htf,
    )


def stop_pct(result):

    return abs(100.0 - result["stop_loss"]) / 100.0 * 100.0


def test_off_by_default_and_the_intraday_path_is_untouched():
    """Passing a 1h frame must change nothing until the mode is turned on."""

    without = run()
    with_htf = run(htf=hourly())

    assert without["stop_loss"] == with_htf["stop_loss"]
    assert without["take_profit"] == with_htf["take_profit"]
    assert stop_pct(with_htf) <= 0.95


def test_the_anchor_widens_the_stop_past_the_option_spread(monkeypatch):
    """The point of the change: a stop that a 2-3% round trip can be paid out of."""

    baseline = stop_pct(run())

    monkeypatch.setenv("SWING_STRUCTURE_ENABLED", "true")

    result = run(htf=hourly(swing_low=97.0))

    assert result["trade_allowed"] is True
    assert stop_pct(result) > 2.5
    assert stop_pct(result) > baseline * 3


def test_the_band_moves_with_the_anchor(monkeypatch):
    """The failure mode that made three earlier arms identical to their controls.

    A 3% stop against a 0.75% ceiling is not a wider trade, it is zero trades
    reported as "no signal". The band is replaced by the mode rather than left
    to a second env var someone has to remember.
    """

    monkeypatch.setenv("SWING_STRUCTURE_ENABLED", "true")

    result = run(htf=hourly(swing_low=97.0))

    assert not any("Stop too wide" in reason for reason in result["reasons"])


def test_reward_scales_with_the_wider_risk(monkeypatch):
    """RR is the gate, so a wider stop must not arrive as a worse-looking trade."""

    monkeypatch.setenv("SWING_STRUCTURE_ENABLED", "true")
    monkeypatch.setenv("SWING_TARGET_RR", "2.0")

    result = run(htf=hourly(swing_low=97.0))

    risk = 100.0 - result["stop_loss"]
    reward = result["take_profit"] - 100.0

    assert reward == pytest.approx(risk * 2.0, rel=0.01)
    assert result["risk_reward"] >= 1.5


def test_a_missing_higher_frame_rejects_rather_than_falls_back(monkeypatch):
    """A silent fallback would blend treatment and control in one arm.

    compute_indicators returns an EMPTY frame below its minimum bar count rather
    than raising, so this is a state that occurs early every session -- common
    enough that a fallback would quietly carry a large share of the run.
    """

    monkeypatch.setenv("SWING_STRUCTURE_ENABLED", "true")

    for unusable in (None, pd.DataFrame()):

        result = run(htf=unusable)

        assert result["trade_allowed"] is False
        assert any("Swing anchor unavailable" in r for r in result["reasons"])
        assert result["stop_loss"] is None


def test_the_short_side_mirrors(monkeypatch):

    monkeypatch.setenv("SWING_STRUCTURE_ENABLED", "true")

    result = run(
        htf=hourly(swing_high=103.0),
        regime="TRENDING_BEAR",
        signal="BEARISH",
        entry_type="EMA_REJECTION_SHORT",
    )

    assert result["stop_loss"] > 100.0 > result["take_profit"]
    assert stop_pct(result) > 2.5


def test_price_through_the_pivot_still_yields_a_stop_on_the_right_side(monkeypatch):
    """A long whose 1h swing low sits above entry -- the pivot alone inverts."""

    monkeypatch.setenv("SWING_STRUCTURE_ENABLED", "true")

    result = run(htf=hourly(swing_low=101.0, swing_high=104.0))

    assert result["stop_loss"] < 100.0
    assert result["take_profit"] > 100.0


def test_the_intraday_atr_floor_cannot_narrow_a_swing_stop(monkeypatch):
    """The 15m ATR floor is smaller by construction and must not apply."""

    monkeypatch.setenv("SWING_STRUCTURE_ENABLED", "true")

    result = run(htf=hourly(swing_low=97.0))

    assert not any("ATR floor adjusted stop" in r for r in result["reasons"])


def test_the_rr_gate_is_inert_in_swing_mode(monkeypatch):
    """Pinned because it is a loss of filtering, not a feature.

    Reward is a fixed multiple of risk here, so every trade reports the same RR
    and the 1.5 floor can never reject one. Anything relying on that gate to do
    quality work has to be told.
    """

    monkeypatch.setenv("SWING_STRUCTURE_ENABLED", "true")
    monkeypatch.setenv("SWING_TARGET_RR", "2.0")

    near = run(htf=hourly(swing_low=99.2))
    far = run(htf=hourly(swing_low=95.0))

    assert near["risk_reward"] == far["risk_reward"] == 2.0


def test_headroom_rejects_a_target_that_runs_into_resistance(monkeypatch):
    """What replaces the RR gate: is there space to reach the target at all."""

    monkeypatch.setenv("SWING_STRUCTURE_ENABLED", "true")
    monkeypatch.setenv("SWING_HEADROOM_MULTIPLE", "2.0")

    # Stop ~3% away, so a 2R target needs ~6% of room. Resistance at 1% does not
    # give it; resistance at 15% does.
    blocked = run(htf=hourly(swing_low=97.0, resistance=101.0))
    clear = run(htf=hourly(swing_low=97.0, resistance=115.0))

    assert blocked["trade_allowed"] is False
    assert any("No headroom" in r for r in blocked["reasons"])
    assert clear["trade_allowed"] is True


def test_headroom_is_off_by_default_so_the_two_arms_separate(monkeypatch):

    monkeypatch.setenv("SWING_STRUCTURE_ENABLED", "true")

    blocked = run(htf=hourly(swing_low=97.0, resistance=101.0))

    assert blocked["trade_allowed"] is True


def test_headroom_mirrors_on_the_short_side(monkeypatch):

    monkeypatch.setenv("SWING_STRUCTURE_ENABLED", "true")
    monkeypatch.setenv("SWING_HEADROOM_MULTIPLE", "2.0")

    blocked = run(
        htf=hourly(swing_high=103.0, support=99.0),
        regime="TRENDING_BEAR",
        signal="BEARISH",
        entry_type="EMA_REJECTION_SHORT",
    )

    assert blocked["trade_allowed"] is False
    assert any("No headroom" in r for r in blocked["reasons"])


def test_describe_mode_names_the_exit_setting(monkeypatch):
    """A wide stop with momentum exits on measures the control.

    The position is closed by a nine-period EMA within minutes, so the anchor is
    paid for and never used. The arm's own output has to say which it ran.
    """

    monkeypatch.setenv("SWING_STRUCTURE_ENABLED", "true")
    monkeypatch.setenv("EXIT_MOMENTUM_ENABLED", "false")

    described = swing_anchor.describe_mode()

    assert described["swing_structure_enabled"] is True
    assert described["momentum_exits_enabled"] is False
