"""The base score is the whole difference between two competing patterns.

`_entry_score` adds an analysis score, a regime bonus, a volume bonus and an
extension penalty, and every one of those reads the same `analysis` and the same
bar whichever pattern is being scored. So when two patterns qualify on one bar,
the gap between them is exactly the gap between their base scores, and
`detect_entry` keeps the larger.

BREAKOUT is 80 and EMA_PULLBACK is 85, which means BREAKOUT cannot win when both
qualify -- not on any symbol, in any regime, ever. A 21-day replay produced 167
EMA_PULLBACK entries against 2 BREAKOUT, and that is the reason: the one pattern
built for a stock running hard is outranked by construction, so a 13% session
never triggers on it.

These tests pin the arithmetic and pin the defaults. The knob exists so a replay
can price a different ordering; until someone sets it, nothing moves.
"""

import os
import unittest
from unittest.mock import patch

from app.strategies.entry_engine import (
    ENTRY_BASE_SCORES,
    _base_score,
    _entry_score,
)


def _latest(rel_volume=1.0):

    return {"REL_VOLUME": rel_volume}


def _analysis(score=0, regime="RANGE_BOUND"):

    return {"score": score, "market_regime": regime}


class BaseScoreTests(unittest.TestCase):

    def test_defaults_are_unchanged_when_nothing_is_set(self):

        with patch.dict(os.environ, {}, clear=False):

            for setup_type, expected in ENTRY_BASE_SCORES.items():

                os.environ.pop(f"ENTRY_BASE_SCORE_{setup_type}", None)

                self.assertEqual(_base_score(setup_type), expected)

    def test_an_unknown_pattern_falls_back_rather_than_raising(self):

        self.assertEqual(_base_score("NOT_A_PATTERN"), 70)

    def test_the_knob_moves_one_pattern_and_leaves_the_others(self):

        with patch.dict(os.environ,
                        {"ENTRY_BASE_SCORE_BREAKOUT": "85"}, clear=False):

            self.assertEqual(_base_score("BREAKOUT"), 85)
            self.assertEqual(_base_score("EMA_PULLBACK"), 85)
            self.assertEqual(_base_score("VWAP_REJECTION"), 88)


class PatternRankingTests(unittest.TestCase):
    """The finding itself, stated as a test so it cannot quietly change."""

    def _score(self, setup_type, **kwargs):

        return _entry_score(
            setup_type,
            _analysis(**kwargs.pop("analysis", {})),
            _latest(**kwargs.pop("latest", {})),
            kwargs.pop("avoid_chasing", False),
            "CALL",
        )

    def test_breakout_always_loses_to_the_pullback_by_exactly_five(self):
        """Every other term is shared, so the gap is the base-score gap."""

        for analysis in ({}, {"score": 12}, {"regime": "TRENDING_BULL"}):

            for latest in ({}, {"rel_volume": 1.3}, {"rel_volume": 2.0}):

                for chasing in (False, True):

                    breakout = self._score(
                        "BREAKOUT", analysis=analysis,
                        latest=latest, avoid_chasing=chasing,
                    )
                    pullback = self._score(
                        "EMA_PULLBACK", analysis=analysis,
                        latest=latest, avoid_chasing=chasing,
                    )

                    self.assertEqual(
                        pullback - breakout, 5,
                        f"gap should be constant, got {pullback - breakout} "
                        f"for {analysis} {latest} chasing={chasing}",
                    )

    def test_levelling_the_base_score_removes_the_handicap(self):

        with patch.dict(os.environ,
                        {"ENTRY_BASE_SCORE_BREAKOUT": "85"}, clear=False):

            breakout = self._score("BREAKOUT", latest={"rel_volume": 1.6})
            pullback = self._score("EMA_PULLBACK", latest={"rel_volume": 1.6})

        self.assertEqual(breakout, pullback)


if __name__ == "__main__":
    unittest.main()
