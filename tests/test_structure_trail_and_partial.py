"""The swing a trend has to hold, and a partial that needs something to sell.

Both come out of 2026-08-19.

The ATR trail is a volatility *distance*, not a level. AMZN #343's 15-minute ATR
was 1.32-1.44 against a 1R of 1.31, so `price - 1x ATR` sat a full R below the
high and the ladder governed instead -- the trail contributed nothing.

The partial-profit branch set a flag and raised a "Position: Partial closed /
Runner: Still Open" alert on every trade reaching 1.5R, while
MAX_CONTRACTS_PER_TRADE is 1 and nothing beneath it ever closed part of anything.
"""

import os
from unittest import mock

import pandas as pd
import pytest

from app.exit.exit_engine import evaluate_exit, structure_trail_stop


def _bars(lows, highs):
    return pd.DataFrame([{"High": h, "Low": l} for l, h in zip(lows, highs)])


class TestStructureTrail:

    def test_a_long_trails_under_the_swing_low(self):
        df = _bars([106, 104, 105, 107, 108], [110, 109, 111, 112, 113])
        df = pd.concat([df, _bars([101], [114])], ignore_index=True)

        level = structure_trail_stop(df, is_short=False)

        assert level == pytest.approx(104 * 0.9995, abs=0.01)

    def test_a_short_trails_over_the_swing_high(self):
        df = _bars([106, 104, 105, 107, 108], [110, 109, 111, 112, 113])
        df = pd.concat([df, _bars([101], [114])], ignore_index=True)

        level = structure_trail_stop(df, is_short=True)

        assert level == pytest.approx(113 * 1.0005, abs=0.01)

    def test_the_forming_bar_is_excluded(self):
        """Its Low is still moving. A stop from an unfinished bar is the same
        defect `_stop_trigger_price` exists to prevent."""

        df = _bars([106, 105, 107], [110, 111, 112])
        settled = structure_trail_stop(df, is_short=False)

        # the forming bar prints a much lower low; the level must not follow it
        df.loc[len(df) - 1, "Low"] = 90.0

        assert structure_trail_stop(df, is_short=False) == settled

    def test_the_buffer_sits_beyond_the_swing(self):
        """Price touching the level exactly must not stop out a trend that is
        still holding it."""

        df = _bars([100, 100, 100], [110, 110, 110])

        assert structure_trail_stop(df, is_short=False) < 100.0
        assert structure_trail_stop(df, is_short=True) > 110.0

    def test_it_degrades_quietly(self):
        assert structure_trail_stop(None, False) is None
        assert structure_trail_stop(pd.DataFrame([{"High": 1, "Low": 1}]), False) is None

    def test_a_zero_lookback_disables_it(self):
        df = _bars([106, 104, 105], [110, 109, 111])

        assert structure_trail_stop(df, False, lookback=0) is None


def _frame(close):
    return pd.DataFrame([{
        "Close": close, "High": close + 0.2, "Low": close - 0.2, "Open": close,
        "Volume": 1_000_000, "EMA9": close, "EMA20": close, "VWAP": close,
        "RSI": 55.0, "MACD": 0.5, "MACD_SIGNAL": 0.4, "ATR": 1.0, "ATR_PCT": 0.5,
    }] * 6)


def _run(contracts):
    state = {
        "entry_type": "EMA_PULLBACK", "holding_profile": "INTRADAY",
        "bars_in_trade": 5, "initial_stop_loss": 90.0, "mfe_r": 1.6,
    }

    if contracts is not None:
        state["option_contracts"] = contracts

    return evaluate_exit(
        _frame(116.0),
        {"trend_regime": "TRENDING_BULL"},
        {"entry_price": 100.0, "stop_loss": 90.0, "take_profit": 130.0,
         "initial_stop_loss": 90.0},
        entry_setup={"entry_type": "EMA_PULLBACK"},
        trade_state=state,
    )


class TestPartialNeedsSomethingToSell:

    def test_one_contract_never_claims_a_partial(self):
        """AMZN #343: announced as partially closed at 1.91R holding a single
        contract, with close_price and r_multiple both null, then closed in full
        at its target. The partial and the runner were the same contract."""

        assert _run(1)["partial_profit_taken"] is False

    def test_an_absent_size_is_treated_as_one(self):
        assert _run(None)["partial_profit_taken"] is False

    def test_two_contracts_can_partial(self):
        """The threshold was never the false part -- the claim of an execution
        was. With real size behind it the branch is legitimate again."""

        assert _run(2)["partial_profit_taken"] is True
