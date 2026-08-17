"""Relative strength had no benchmark: it compared the symbol's move against 0.

So "Strong relative strength" meant "up more than 0.5% today". On a session
where the sector reference is up 1.5%, a name up 0.6% is lagging it badly and
was scored bullish. main.py has computed the sector reference move all along in
_sector_strength and recorded it as telemetry, never feeding it back.

The switch defaults off, and the first test here is the one that lets this ship
into a live session: with no benchmark passed, the score is byte-identical to
what it was before.
"""

import pandas as pd
import pytest

from app.strategies import momentum_strategy
from app.strategies.momentum_strategy import (
    analyze_setup,
    relative_strength_benchmark_enabled,
)


def _frame(symbol_move_pct):
    """A frame carrying every column analyze_setup reads.

    Deliberately neutral everywhere except SYMBOL_MOVE_PCT: the flags are all
    False and the oscillators mid-range, so the only thing moving between two
    calls is the relative-strength arm under test.
    """

    rows = 30
    close = [100.0 + i * 0.1 for i in range(rows)]

    flags = {
        name: [False] * rows
        for name in (
            "BREAKOUT", "BREAKDOWN", "ORB_BREAKOUT", "ORB_BREAKDOWN",
            "CONSOLIDATING", "FAILED_BREAKOUT", "HIGHER_HIGH", "HIGHER_LOW",
            "LOWER_HIGH", "LOWER_LOW", "LOWER_HIGH_SEQUENCE", "VOLUME_SPIKE",
        )
    }

    return pd.DataFrame({
        "Open": close,
        "High": [c + 0.2 for c in close],
        "Low": [c - 0.2 for c in close],
        "Close": close,
        "Volume": [1_000_000] * rows,
        "EMA9": close,
        "EMA20": close,
        "EMA9_SLOPE": [0.0] * rows,
        "RSI": [50.0] * rows,
        "RSI_SLOPE": [0.0] * rows,
        "MACD": [0.0] * rows,
        "MACD_SIGNAL": [0.0] * rows,
        "VWAP": close,
        "VWAP_DISTANCE": [0.0] * rows,
        "ATR": [0.5] * rows,
        "ATR_PCT": [0.5] * rows,
        "REL_VOLUME": [1.0] * rows,
        "VOLUME_TREND": [1.0] * rows,
        "BODY_STRENGTH": [0.5] * rows,
        "DISTANCE_TO_RESISTANCE": [1.0] * rows,
        "DISTANCE_TO_SUPPORT": [1.0] * rows,
        "TREND_PHASE": ["NEUTRAL"] * rows,
        "SYMBOL_MOVE_PCT": [symbol_move_pct] * rows,
        **flags,
    })


class TestTheDefaultIsUnchangedBehaviour:
    """What makes the switch safe to deploy mid-plan."""

    def test_the_switch_is_off_unless_asked_for(self, monkeypatch):
        monkeypatch.delenv("RELATIVE_STRENGTH_BENCHMARK_ENABLED", raising=False)

        assert relative_strength_benchmark_enabled() is False

    @pytest.mark.parametrize("move", [-2.0, -0.4, 0.0, 0.6, 3.0])
    def test_omitting_the_benchmark_scores_exactly_as_before(self, move):
        """No benchmark means compare against zero, which is the old code."""

        assert analyze_setup(_frame(move)) == analyze_setup(_frame(move), None)

    def test_a_missing_reference_move_is_not_an_error(self):
        """_sector_strength yields None whenever the reference is unavailable."""

        assert analyze_setup(_frame(1.0), None)["score"] is not None


def _score(symbol_move_pct, benchmark=None):
    return analyze_setup(_frame(symbol_move_pct), benchmark)["score"]


class TestWithABenchmarkItMeasuresSomethingReal:
    """Asserted on the score, not on the reason text.

    `reasons` only carries the bullish or bearish list once the signal resolves
    directional, and this fixture is deliberately neutral so that nothing but
    the relative-strength arm differs between two calls. The score is where that
    arm lands either way -- the reason string is appended in the same branch, so
    a score that moves by the full ±1 is the branch changing sides.
    """

    def test_a_name_lagging_a_strong_sector_is_no_longer_scored_bullish(self):
        """The case the bug got backwards.

        Up 0.6% while the sector is up 1.5% is underperformance. The old code
        compared 0.6 against 0, scored +1 and appended "Strong relative
        strength". Against the sector it is -1, a full 2 points lower.
        """

        assert _score(0.6) - _score(0.6, 1.5) == 2.0

    def test_a_name_falling_less_than_its_sector_gains_the_point(self):
        """The mirror: down 0.3% while the sector is down 1.4% is leadership.

        Against zero, -0.3 sits inside the ±0.5 band and scores nothing.
        """

        assert _score(-0.3, -1.4) - _score(-0.3) == 1.0

    def test_the_half_point_band_travels_with_the_benchmark(self):
        """Up 1.2% against a sector up 1.0% is inside the band: no contribution.

        Against zero the same 1.2% would have scored +1.
        """

        assert _score(1.2, 1.0) == _score(0.0)
        assert _score(1.2) - _score(1.2, 1.0) == 1.0

    def test_a_zero_benchmark_is_the_old_behaviour_stated_explicitly(self):
        assert analyze_setup(_frame(0.9), 0.0) == analyze_setup(_frame(0.9))


def test_analyze_setup_is_importable_with_the_flag_helper():
    """main.py imports both from this module; keep them together."""

    assert callable(momentum_strategy.analyze_setup)
    assert callable(momentum_strategy.relative_strength_benchmark_enabled)
