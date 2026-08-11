"""The chain summary must describe the decision, never re-decide it."""

import json

from app.options.chain_quality import summarize_chain


def _attempt(**kw):
    base = {
        "ticker": "O:NVDA260821C00200000", "accepted": False, "code": "LOW_OPEN_INTEREST",
        "spread_pct": 5.0, "open_interest": 100, "contract_cost": 500.0, "delta": 0.35,
    }
    base.update(kw)
    return base


def test_nothing_examined_is_reported_as_nothing():
    summary = summarize_chain([])

    assert summary["CHAIN_EXAMINED"] == 0
    assert summary["CHAIN_ACCEPTED"] is False
    assert summary["CHAIN_BEST_SPREAD_PCT"] is None


def test_it_reads_the_json_string_the_row_actually_carries():
    """The row stores attempts serialised, which is the whole reason this exists."""

    raw = json.dumps([_attempt(spread_pct=2.0), _attempt(spread_pct=8.0)])

    assert summarize_chain(raw)["CHAIN_BEST_SPREAD_PCT"] == 2.0


def test_the_best_of_each_dimension_is_taken_across_all_contracts():
    """A chain with no tight spread is a different problem from one where a
    tight spread existed and something else refused it."""

    summary = summarize_chain([
        _attempt(spread_pct=9.0, open_interest=5000, contract_cost=2400.0),
        _attempt(spread_pct=1.5, open_interest=50, contract_cost=310.0),
    ])

    assert summary["CHAIN_BEST_SPREAD_PCT"] == 1.5
    assert summary["CHAIN_BEST_OPEN_INTEREST"] == 5000
    assert summary["CHAIN_CHEAPEST_COST"] == 310.0


def test_near_miss_is_the_tightest_refused_contract_not_the_commonest_refusal():
    """'Why did we not take the best thing on offer' and 'what refused the most'
    are different questions and usually have different answers."""

    summary = summarize_chain([
        _attempt(spread_pct=9.0, code="LOW_OPEN_INTEREST"),
        _attempt(spread_pct=8.5, code="LOW_OPEN_INTEREST"),
        _attempt(spread_pct=1.2, code="OPTION_TOO_EXPENSIVE", contract_cost=1900.0, delta=0.5),
    ])

    assert summary["CHAIN_BINDING_CODE"] == "LOW_OPEN_INTEREST"
    assert summary["CHAIN_NEAR_MISS_CODE"] == "OPTION_TOO_EXPENSIVE"
    assert summary["CHAIN_NEAR_MISS_SPREAD_PCT"] == 1.2
    assert summary["CHAIN_NEAR_MISS_COST"] == 1900.0
    assert summary["CHAIN_NEAR_MISS_DELTA"] == 0.5


def test_an_accepted_contract_is_not_reported_as_a_near_miss():
    summary = summarize_chain([
        _attempt(spread_pct=1.0, accepted=True, code=None),
        _attempt(spread_pct=7.0, code="WIDE_SPREAD"),
    ])

    assert summary["CHAIN_ACCEPTED"] is True
    assert summary["CHAIN_NEAR_MISS_CODE"] == "WIDE_SPREAD"


def test_malformed_input_summarises_to_nothing_rather_than_raising():
    """This runs inside row assembly on every scan; it must never be the fault."""

    for junk in ("not json", None, 42, [{"spread_pct": "abc"}], [None, "x"]):
        summary = summarize_chain(junk)
        assert summary["CHAIN_BEST_SPREAD_PCT"] is None
