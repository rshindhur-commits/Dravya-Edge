"""Promoting the tightest fully-qualified contract, and the traps it must avoid.

From the archive: on scans where the app bought nothing, 18.3% had a contract
sitting in the chain that passed every gate already enforced -- median spread
1.71% while the app reported 9.76%. AMD on 2026-08-14 is the worked case: it
chose rank #82 at 8.33% and refused it, with eighteen sub-3% contracts available.

The two ways this could go wrong are what most of these tests are about. Ranking
on spread alone reaches for deep-ITM LEAPS, tight because they are expensive, so
the cost cap must bind first. And some contracts are tight because nobody trades
them, so the liquidity floors must bind too.
"""

import os
from unittest import mock

from app.options.contract_ranker import prefer_tightest_qualified


def contract(ticker, spread, cost, oi=1000, volume=500, score=50.0):
    return {
        "ticker": ticker,
        "spread_pct": spread,
        "contract_cost": cost,
        "open_interest": oi,
        "volume": volume,
        "ranking_score": score,
    }


ENV = {
    "OPTION_MAX_SPREAD_PCT": "3",
    "OPTION_MAX_CONTRACT_COST": "500",
    "OPTION_PREFER_TIGHTEST_QUALIFIED": "true",
}


class TestPromotion:

    def test_the_amd_case(self):
        """A wide top-ranked contract loses its place to a tight qualified one."""
        ranked = [
            contract("WIDE_TOP_RANKED", spread=8.33, cost=400, score=99.0),
            contract("TIGHT_QUALIFIED", spread=1.53, cost=450, score=10.0),
        ]
        with mock.patch.dict(os.environ, ENV):
            out = prefer_tightest_qualified(ranked)
        assert out[0]["ticker"] == "TIGHT_QUALIFIED"

    def test_tightest_of_several_qualified_wins(self):
        ranked = [
            contract("A", spread=2.9, cost=300, score=99.0),
            contract("B", spread=1.1, cost=300, score=50.0),
            contract("C", spread=2.0, cost=300, score=80.0),
        ]
        with mock.patch.dict(os.environ, ENV):
            out = prefer_tightest_qualified(ranked)
        assert [c["ticker"] for c in out[:3]] == ["B", "C", "A"]

    def test_nothing_qualified_leaves_order_untouched(self):
        """The other 81.7% of scans must behave exactly as before."""
        ranked = [
            contract("A", spread=8.0, cost=300, score=99.0),
            contract("B", spread=9.0, cost=300, score=50.0),
        ]
        with mock.patch.dict(os.environ, ENV):
            out = prefer_tightest_qualified(ranked)
        assert [c["ticker"] for c in out] == ["A", "B"]

    def test_no_contract_is_lost(self):
        ranked = [
            contract("A", spread=1.0, cost=300),
            contract("B", spread=9.0, cost=300),
            contract("C", spread=2.0, cost=9000),
        ]
        with mock.patch.dict(os.environ, ENV):
            out = prefer_tightest_qualified(ranked)
        assert sorted(c["ticker"] for c in out) == ["A", "B", "C"]
        assert len(out) == 3


class TestTheTrapsItMustAvoid:

    def test_a_tight_leap_over_the_cap_is_not_promoted(self):
        """Tight *because* expensive. §14 measured that arm at $3,696."""
        ranked = [
            contract("AFFORDABLE_WIDE", spread=2.9, cost=400, score=10.0),
            contract("LEAP_TIGHT", spread=0.5, cost=3696, score=99.0),
        ]
        with mock.patch.dict(os.environ, ENV):
            out = prefer_tightest_qualified(ranked)
        assert out[0]["ticker"] == "AFFORDABLE_WIDE"

    def test_a_tight_illiquid_contract_is_not_promoted(self):
        """Tight because nobody trades it -- three of AMD's eight were these."""
        ranked = [
            contract("LIQUID", spread=2.9, cost=400, oi=1000, volume=500, score=10.0),
            contract("DEAD", spread=0.4, cost=400, oi=3, volume=0, score=99.0),
        ]
        with mock.patch.dict(os.environ, ENV):
            out = prefer_tightest_qualified(ranked)
        assert out[0]["ticker"] == "LIQUID"

    def test_a_contract_at_exactly_the_ceiling_qualifies(self):
        ranked = [
            contract("AT_LIMIT", spread=3.0, cost=500, score=1.0),
            contract("OVER", spread=3.1, cost=500, score=99.0),
        ]
        with mock.patch.dict(os.environ, ENV):
            out = prefer_tightest_qualified(ranked)
        assert out[0]["ticker"] == "AT_LIMIT"

    def test_zero_or_missing_spread_never_qualifies(self):
        ranked = [
            contract("REAL", spread=2.0, cost=300, score=1.0),
            contract("ZERO", spread=0.0, cost=300, score=99.0),
            contract("MISSING", spread=None, cost=300, score=99.0),
        ]
        with mock.patch.dict(os.environ, ENV):
            out = prefer_tightest_qualified(ranked)
        assert out[0]["ticker"] == "REAL"


class TestSwitch:

    def test_disabled_leaves_the_ranker_alone(self):
        ranked = [
            contract("WIDE_TOP", spread=8.0, cost=300, score=99.0),
            contract("TIGHT", spread=1.0, cost=300, score=10.0),
        ]
        with mock.patch.dict(os.environ, dict(ENV, OPTION_PREFER_TIGHTEST_QUALIFIED="false")):
            out = prefer_tightest_qualified(ranked)
        assert out[0]["ticker"] == "WIDE_TOP"

    def test_empty_input_is_safe(self):
        with mock.patch.dict(os.environ, ENV):
            assert prefer_tightest_qualified([]) == []
