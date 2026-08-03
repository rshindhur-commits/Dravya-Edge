"""Regression guards for the contract-selection input contract.

Three ways the chain silently emptied while this was being built, each of which
produced "no trade" rather than an error -- the worst failure mode for a
backtest, because a run that takes no trades looks like a strategy with no
signals rather than a harness with a bug.

Hermetic; no Polygon access.
"""

import inspect

import app.backtesting.historical_options as historical_options
from app.backtesting.contract_selector import _session_volume
from app.options.contract_ranker import rank_option_contracts


def _contract(**overrides):
    """A contract the live ranker should accept."""

    base = {
        "ticker": "O:NVDA260807C00197500",
        "symbol": "NVDA",
        "strike": 197.5,
        "type": "call",
        "expiration_date": "2026-08-07",
        "dte": 7,
        "expiration_bucket": "WEEKLY",
        "mid_price": 4.70,
        "bid": 4.65,
        "ask": 4.75,
        "spread_pct": 2.1,
        "delta": 0.5186,
        "gamma": 0.0368,
        "theta": -0.317,
        "iv": 39.5,
        "volume": 1200.0,
        "open_interest": 1,
        "option_quality_score": 0,
    }
    base.update(overrides)

    return base


def test_baseline_contract_is_accepted_by_the_live_ranker():

    ranked = rank_option_contracts(
        [_contract()], underlying_price=198.24, direction="CALL", paper_mode=True
    )

    assert len(ranked) == 1
    assert ranked[0]["ticker"] == "O:NVDA260807C00197500"


def test_uppercase_contract_type_empties_the_chain():
    """The ranker compares ``c["type"]`` against lowercase literals.

    Passing "CALL" -- which is what ``parse_occ_ticker`` returns -- rejects
    every contract on the direction filter and yields an empty chain.
    """

    ranked = rank_option_contracts(
        [_contract(type="CALL")],
        underlying_price=198.24,
        direction="CALL",
        paper_mode=True,
    )

    assert ranked == []


def test_zero_open_interest_empties_the_chain():
    """``oi < 1`` is a hard reject.

    Open interest has no point-in-time history, so the selector substitutes
    ``ASSUMED_OPEN_INTEREST``. This gate is only the first of the OI rules it
    has to clear -- the liquidity gate wants 500 and the quality scorer docks
    20 below it -- which is why that constant is the configured minimum rather
    than the 1 that satisfies this one. Zero would drop the whole chain.
    """

    assert rank_option_contracts(
        [_contract(open_interest=0)],
        underlying_price=198.24,
        direction="CALL",
        paper_mode=True,
    ) == []

    assert len(
        rank_option_contracts(
            [_contract(open_interest=1)],
            underlying_price=198.24,
            direction="CALL",
            paper_mode=True,
        )
    ) == 1


def test_thin_bar_volume_is_rejected_where_session_volume_is_not():
    """``volume < 5`` is a hard reject.

    A single 5m bar can show 2 contracts traded on a strike that has done
    thousands since the open, so the selector accumulates session volume rather
    than reading the last bar.
    """

    assert rank_option_contracts(
        [_contract(volume=2.0)],
        underlying_price=198.24,
        direction="CALL",
        paper_mode=True,
    ) == []


def test_session_volume_accumulates_only_the_current_session_up_to_now():

    import pandas as pd

    index = pd.to_datetime(
        [
            "2026-07-30 18:00",  # previous session, must not count
            "2026-07-31 13:30",
            "2026-07-31 13:35",
            "2026-07-31 19:00",  # after the moment, must not count
        ],
        utc=True,
    )
    bars = pd.DataFrame({"Volume": [500.0, 10.0, 15.0, 900.0]}, index=index)

    # 09:40 ET on 2026-07-31 == 13:40 UTC.
    total = _session_volume(bars, pd.Timestamp("2026-07-31 09:40", tz="America/New_York"))

    assert total == 25.0


def test_contract_listing_never_sends_the_expired_flag():
    """``expired=true`` looks right for a historical chain and is not.

    With ``as_of`` it returns zero results, and unbounded it also stops
    honouring ``underlying_ticker`` -- a request for NVDA came back with NAX
    contracts expiring in 2010. ``as_of`` alone already returns the chain as it
    stood that day. Asserted on the source so it cannot be reintroduced as an
    obvious-looking fix.
    """

    source = inspect.getsource(historical_options.list_contracts_as_of)

    assert '"expired"' not in source
    assert "'expired'" not in source
