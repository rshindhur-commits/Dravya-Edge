"""The rule that decides which candidates can exist, made testable.

`avoid_chasing` refuses any setup where price sits more than 1.2% from EMA9 or
1.5% from VWAP. It is not a score penalty -- `calculate_risk` turns it into
`trade_allowed = False` -- so those two numbers are the outer boundary of what
this strategy is permitted to trade, and until 2026-08-14 neither could be
varied.

Why that matters: on 2026-08-13 MU travelled 5.67% and SMCI 7.33% and neither
produced a tradeable candidate, because a liquid megacap leaves a 1.2% band
within minutes of starting to move. TRADE_QUALITY_PLAN §2.2a attributes the
sub-percent ceiling to the stop anchor; this sits upstream of the anchor and
refuses the trade before the anchor ever shapes it.

Two properties are pinned. **Defaults reproduce the old constants exactly**, so
adding the switch changed no behaviour. And **the flag is still computed when the
block is lifted**, so an arm running without the refusal still records which
candidates would have been called chased -- the same shape as
`SETUP_GATE_ENABLED`, and what keeps the arm comparable to its control.

Frame shape is borrowed from tests/test_directional_symmetry.py, which already
exercises `detect_entry` and carries the columns it reads.
"""

import unittest

import pandas as pd
import pytest

from app.risk.risk_manager import avoid_chasing_blocks, calculate_risk
from app.strategies.entry_engine import (
    detect_entry,
    max_ema_distance_pct,
    max_vwap_distance_pct,
)


def _row(close, vwap, ema9, **overrides):
    row = {
        "Open": close, "High": close, "Low": close, "Close": close,
        "Volume": 1_000_000, "VWAP": vwap, "EMA9": ema9, "EMA20": ema9,
        "ATR": 1.0, "RSI": 50.0, "REL_VOLUME": 1.0, "BODY_STRENGTH": 0.4,
        "BREAKDOWN": False, "LOWER_HIGH": False, "MACD": 0.0,
        "MACD_SIGNAL": 0.0, "EMA9_SLOPE": 0.0,
    }
    row.update(overrides)
    return row


def _frame(row):
    index = pd.date_range(
        "2026-07-30 14:00", periods=12, freq="15min", tz="America/New_York"
    )
    return pd.DataFrame([row] * 12, index=index)


BULLISH = {"signal": "BULLISH", "score": 5, "market_regime": "TRENDING_BULLISH"}
BEARISH = {"signal": "BEARISH", "score": -5, "market_regime": "TRENDING_BEARISH"}


def _risk_frame():
    """A frame `calculate_risk` can price, independent of detect_entry."""

    return pd.DataFrame([
        {
            "High": 100.5, "Low": 99.5, "Close": 100.0,
            "ATR": 1.0, "EMA9": 99.4, "VWAP": 99.35,
            "ROLLING_RESISTANCE": 108.0, "ROLLING_SUPPORT": 92.0,
            "PREV_HIGH": 106.0, "PREV_LOW": 94.0,
        }
    ] * 20)


def _risk_for(avoid_chasing):

    return calculate_risk(
        df=_risk_frame(),
        analysis={"signal": "BULLISH", "market_regime": "TRENDING_BULL"},
        entry_setup={
            "entry_type": "EMA_PULLBACK",
            "entry_quality": "HIGH",
            "avoid_chasing": avoid_chasing,
        },
    )


class DefaultsUnchangedTests(unittest.TestCase):
    """Adding the switch must not have moved anything on its own."""

    def test_the_thresholds_still_default_to_the_old_constants(self):

        self.assertEqual(max_vwap_distance_pct(), 1.5)
        self.assertEqual(max_ema_distance_pct(), 1.2)

    def test_the_block_is_on_by_default(self):

        self.assertTrue(avoid_chasing_blocks())

    def test_a_chased_setup_is_still_refused(self):

        result = _risk_for(avoid_chasing=True)

        self.assertFalse(result["trade_allowed"])
        self.assertIn(
            "Avoid chasing extended move",
            " ".join(result.get("reasons", [])),
        )

    def test_an_unchased_setup_is_unaffected(self):

        result = _risk_for(avoid_chasing=False)

        self.assertNotIn(
            "Avoid chasing extended move",
            " ".join(result.get("reasons", [])),
        )


def test_lifting_the_block_allows_the_same_setup(monkeypatch):
    """The arm this switch exists to make possible."""

    monkeypatch.setenv("AVOID_CHASING_BLOCKS", "false")

    reasons = " ".join(_risk_for(avoid_chasing=True).get("reasons", []))

    assert "Avoid chasing extended move" not in reasons


def test_lifting_the_block_does_not_suppress_the_flag(monkeypatch):
    """Measurement survives the arm, or the arm cannot be compared to control."""

    monkeypatch.setenv("AVOID_CHASING_BLOCKS", "false")

    setup = detect_entry(_frame(_row(close=103.0, vwap=100.0, ema9=100.0)), BULLISH)

    assert setup["avoid_chasing"] is True, (
        "the flag must still be recorded so the archive stays comparable"
    )


def test_a_wider_band_admits_a_move_the_default_refuses(monkeypatch):
    """What the Phase B arms will actually vary."""

    frame = _frame(_row(close=102.0, vwap=100.0, ema9=100.0))   # 2% out on both

    assert detect_entry(frame, BULLISH)["avoid_chasing"] is True

    monkeypatch.setenv("AVOID_CHASING_MAX_EMA_DISTANCE_PCT", "5.0")
    monkeypatch.setenv("AVOID_CHASING_MAX_VWAP_DISTANCE_PCT", "5.0")

    assert detect_entry(frame, BULLISH)["avoid_chasing"] is False


def test_widening_one_band_alone_leaves_the_other_binding(monkeypatch):
    """The failure mode that made three earlier arms identical to their control.

    Both thresholds refuse independently, so moving one and not the other
    changes nothing for a candidate outside both.
    """

    frame = _frame(_row(close=102.0, vwap=100.0, ema9=100.0))

    monkeypatch.setenv("AVOID_CHASING_MAX_EMA_DISTANCE_PCT", "5.0")

    assert detect_entry(frame, BULLISH)["avoid_chasing"] is True, (
        "VWAP at 1.5 still binds at 2% out"
    )


def test_the_band_is_symmetric():
    """It was long-broken for shorts; distance is measured with abs()."""

    below = detect_entry(_frame(_row(close=97.0, vwap=100.0, ema9=100.0)), BEARISH)

    assert below["avoid_chasing"] is True, "a chased short must be caught too"


@pytest.mark.parametrize("distance,expected", [(1.0, False), (1.4, True)])
def test_the_ema_band_binds_before_the_vwap_one(distance, expected):
    """EMA9 at 1.2 is tighter than VWAP at 1.5, so it is the real boundary.

    At 1.4% out, VWAP would still permit the trade and EMA9 refuses it. That is
    why widening only the VWAP band would look like a null result.
    """

    close = 100.0 * (1 + distance / 100.0)

    setup = detect_entry(_frame(_row(close=close, vwap=100.0, ema9=100.0)), BULLISH)

    assert setup["avoid_chasing"] is expected


if __name__ == "__main__":
    unittest.main()
