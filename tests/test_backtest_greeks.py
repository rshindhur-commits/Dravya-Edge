"""Greek reconstruction, and the ranker input contract.

``rank_option_contracts`` needs Greeks Polygon only serves for *now*, so a
replay recovers them from the contract's observed price. These tests pin that
against the six 2026-07-30/31 trades where live recorded both, and pin the
input-shape requirements that silently emptied the chain when first got wrong.

Hermetic: the live Greeks are inlined rather than read from the database.
"""

import pytest

from app.backtesting.contract_selector import _expiration_bucket
from app.backtesting.historical_greeks import (
    compute_greeks,
    greeks_for_contract,
    implied_volatility,
    parse_occ_ticker,
    years_to_expiry,
)

# symbol, scan, ticker, option mid, underlying, then live's own delta / gamma /
# theta / iv as recorded in paper_trades.payload.
LIVE_GREEKS = [
    ("NVDA", "2026-07-30_142304", "O:NVDA260814P00190000", 4.675, 193.30,
     -0.383906, 0.024263, -0.195079, 0.402943),
    ("NVDA", "2026-07-30_144250", "O:NVDA260814P00190000", 4.675, 193.22,
     -0.388908, 0.024794, -0.192122, 0.396298),
    ("ORCL", "2026-07-30_144858", "O:ORCL260821C00135000", 4.95, 126.36,
     0.376999, 0.018815, -0.181569, 0.659052),
    ("NVDA", "2026-07-31_105846", "O:NVDA260807C00197500", 4.70, 198.24,
     0.518584, 0.036781, -0.316983, 0.395458),
    ("CRWD", "2026-07-31_113633", "O:CRWD260807C00195000", 3.45, 188.03,
     0.361194, 0.024799, -0.407577, 0.575787),
    ("NVDA", "2026-07-31_125759", "O:NVDA260807C00197500", 4.65, 197.96,
     0.561765, 0.036510, -0.310820, 0.390030),
]


def test_parse_occ_ticker_reads_strike_in_thousandths():
    """The part that bites: 00197500 is 197.5, not 197500."""

    spec = parse_occ_ticker("O:NVDA260807C00197500")

    assert spec["underlying"] == "NVDA"
    assert spec["strike"] == 197.5
    assert spec["contract_type"] == "CALL"
    assert spec["expiry"].isoformat() == "2026-08-07"

    put = parse_occ_ticker("O:CRWD260814P00187500")

    assert put["contract_type"] == "PUT"
    assert put["strike"] == 187.5

    with pytest.raises(ValueError):

        parse_occ_ticker("NVDA")


@pytest.mark.parametrize(
    "symbol,scan,ticker,mid,spot,delta,gamma,theta,iv", LIVE_GREEKS
)
def test_reconstructed_greeks_agree_with_live(
    symbol, scan, ticker, mid, spot, delta, gamma, theta, iv
):
    """Tolerances are set from measured agreement, not from hope.

    Polygon's Greeks come from a model whose dividend and rate assumptions are
    not published, so exact equality is not achievable. What matters is that
    the disagreement stays inside the ranker's resolution: delta drives its
    largest term at ``abs(delta - 0.55) * 100``, so 0.03 of delta is 3 points
    of score against a spread of roughly 100.
    """

    from datetime import datetime

    moment = datetime.strptime(scan, "%Y-%m-%d_%H%M%S")
    greeks = greeks_for_contract(ticker, mid, spot, moment)

    assert greeks is not None, f"{symbol} {scan}: IV could not be recovered"

    assert abs(greeks["delta"] - delta) < 0.035
    assert abs(greeks["gamma"] - gamma) < 0.002
    assert abs(greeks["theta"] - theta) < 0.02
    assert abs(greeks["iv"] - iv) < 0.02

    # Sign convention, which no tolerance would catch: puts are negative.
    assert (greeks["delta"] < 0) == (delta < 0)


def test_theta_is_per_day_not_annualised():
    """The ranker gates on ``abs(theta) <= 0.12``.

    An annualised theta is ~365x larger and fails that gate for every contract
    ever, which would silently strip the entire chain of its theta bonus.
    """

    years = years_to_expiry("2026-08-07", "2026-07-31 12:00")
    greeks = compute_greeks(198.24, 197.5, years, 0.395, contract_type="CALL")

    assert greeks is not None
    assert -1.0 < greeks["theta"] < 0.0


def test_implied_volatility_refuses_impossible_prices():
    """Returns None rather than a confident wrong number.

    A price below intrinsic cannot be produced by the model at any volatility.
    Coercing that to a number would put a fabricated delta into the ranker.
    """

    years = years_to_expiry("2026-08-07", "2026-07-31 12:00")

    # Deep ITM call quoted below its own intrinsic value.
    assert implied_volatility(0.50, 198.0, 150.0, years, contract_type="CALL") is None

    assert implied_volatility(None, 198.0, 197.5, years) is None
    assert implied_volatility(-1.0, 198.0, 197.5, years) is None
    assert implied_volatility(4.70, 198.0, 197.5, 0.0) is None


def test_implied_volatility_round_trips_through_the_pricer():

    from app.backtesting.historical_greeks import black_scholes_price

    years = years_to_expiry("2026-08-21", "2026-07-31 12:00")
    price = black_scholes_price(100.0, 100.0, years, 0.04, 0.35, "CALL")

    recovered = implied_volatility(price, 100.0, 100.0, years, contract_type="CALL")

    assert recovered is not None
    assert abs(recovered - 0.35) < 1e-3


def test_expiration_buckets_match_the_labels_the_ranker_switches_on():

    assert _expiration_bucket(0) == "0DTE"
    assert _expiration_bucket(1) == "1DTE"
    assert _expiration_bucket(7) == "WEEKLY"
    assert _expiration_bucket(21) == "SHORT_TERM"
    assert _expiration_bucket(30) == "LONG_TERM"
