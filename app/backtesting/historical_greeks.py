"""Reconstruct option Greeks for historical contracts.

The live chain gets Greeks from Polygon's snapshot, which only ever describes
*now* -- there is no historical endpoint for them. But ``rank_option_contracts``
leans on them heavily: ``abs(delta - 0.55) * 100`` is its single largest term,
delta below 0.25 or above 0.75 is rejected outright, and IV drives a band worth
45 points. A replay that fed it zeros would not be choosing contracts the way
production does, so a replay-only contract selector has to supply them.

They are recoverable. Polygon does serve the contract's historical *price*, and
price plus (spot, strike, expiry, rate) determines implied volatility, which in
turn determines every Greek. So the reconstruction is: back IV out of the
observed mid, then evaluate Black-Scholes at that IV.

Deliberately no scipy -- it is not in requirements.txt, and the normal CDF via
``math.erf`` plus a bracketed solver is exact enough here. Validated against the
six 2026-07-30/31 trades where live recorded Greeks; see
``tests/test_backtest_greeks.py``.

Two caveats worth stating rather than burying. Polygon's own Greeks come from a
model whose dividend and rate assumptions are not published, so agreement is
close but not to the last decimal. And American options on dividend-paying
underlyings are not exactly Black-Scholes; for the short-dated equity calls and
puts this system trades the error is well inside the option's own bid-ask
spread, which is the precision that actually matters for ranking.
"""

import math
import re
from datetime import datetime

# Short-dated US rate. The Greeks this feeds are far more sensitive to IV and
# time-to-expiry than to r at these maturities -- moving it a whole point moves
# delta in the third decimal -- so it is a constant rather than a curve lookup.
DEFAULT_RISK_FREE_RATE = 0.04

TRADING_DAYS_PER_YEAR = 252.0
CALENDAR_DAYS_PER_YEAR = 365.0

_OCC_PATTERN = re.compile(
    r"^O:(?P<root>[A-Z]+)(?P<yy>\d{2})(?P<mm>\d{2})(?P<dd>\d{2})"
    r"(?P<kind>[CP])(?P<strike>\d{8})$"
)


def parse_occ_ticker(ticker):
    """Split an OCC symbol into its parts.

    ``O:NVDA260807C00197500`` -> underlying NVDA, expiry 2026-08-07, CALL,
    strike 197.5. The strike is in thousandths, which is the part that bites:
    read naively it is 197500.
    """

    match = _OCC_PATTERN.match(str(ticker or "").strip().upper())

    if not match:

        raise ValueError(f"not an OCC option ticker: {ticker!r}")

    parts = match.groupdict()

    return {
        "underlying": parts["root"],
        "expiry": datetime(
            2000 + int(parts["yy"]), int(parts["mm"]), int(parts["dd"])
        ).date(),
        "contract_type": "CALL" if parts["kind"] == "C" else "PUT",
        "strike": int(parts["strike"]) / 1000.0,
    }


def _norm_cdf(x):

    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x):

    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1_d2(spot, strike, years, rate, sigma):

    if years <= 0 or sigma <= 0 or spot <= 0 or strike <= 0:

        return None, None

    d1 = (
        math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * years
    ) / (sigma * math.sqrt(years))

    return d1, d1 - sigma * math.sqrt(years)


def black_scholes_price(spot, strike, years, rate, sigma, contract_type="CALL"):

    if years <= 0:

        intrinsic = (
            spot - strike if contract_type == "CALL" else strike - spot
        )

        return max(0.0, intrinsic)

    d1, d2 = _d1_d2(spot, strike, years, rate, sigma)

    if d1 is None:

        return None

    discount = math.exp(-rate * years)

    if contract_type == "CALL":

        return spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)

    return strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_volatility(
    price,
    spot,
    strike,
    years,
    rate=DEFAULT_RISK_FREE_RATE,
    contract_type="CALL",
    tolerance=1e-6,
    max_iterations=100,
):
    """Back IV out of an observed price by bisection.

    Bisection rather than Newton on purpose: vega collapses toward zero for
    deep in- or out-of-the-money contracts, where Newton diverges and returns a
    confident nonsense number. Bisection cannot, and 100 iterations over a
    [1e-4, 5.0] bracket is far cheaper than a wrong Greek.

    Returns ``None`` when the price is outside what the model can produce --
    below intrinsic, or above the bracket. That is a real condition for stale
    or crossed quotes and must not be silently coerced to a number.
    """

    if price is None or price <= 0 or years <= 0 or spot <= 0 or strike <= 0:

        return None

    intrinsic = (
        max(0.0, spot - strike)
        if contract_type == "CALL"
        else max(0.0, strike - spot)
    )

    if price < intrinsic - tolerance:

        return None

    low, high = 1e-4, 5.0

    price_low = black_scholes_price(spot, strike, years, rate, low, contract_type)
    price_high = black_scholes_price(spot, strike, years, rate, high, contract_type)

    if price_low is None or price_high is None:

        return None

    if not (price_low - tolerance <= price <= price_high + tolerance):

        return None

    for _ in range(max_iterations):

        mid = 0.5 * (low + high)
        value = black_scholes_price(spot, strike, years, rate, mid, contract_type)

        if value is None:

            return None

        if abs(value - price) < tolerance:

            return mid

        if value > price:

            high = mid

        else:

            low = mid

    return 0.5 * (low + high)


def compute_greeks(
    spot,
    strike,
    years,
    sigma,
    rate=DEFAULT_RISK_FREE_RATE,
    contract_type="CALL",
):
    """Delta, gamma, theta and vega at a given IV.

    Theta is returned **per calendar day**, not per year, because that is the
    convention Polygon reports and therefore the scale ``rank_option_contracts``
    compares against (`abs(theta) <= 0.12`). Returning the annualised figure
    would be 365x too large and would fail every theta gate in the ranker.

    Delta is signed: negative for puts, matching what live records.
    """

    d1, d2 = _d1_d2(spot, strike, years, rate, sigma)

    if d1 is None:

        return None

    discount = math.exp(-rate * years)
    pdf = _norm_pdf(d1)

    if contract_type == "CALL":

        delta = _norm_cdf(d1)
        theta_annual = -(spot * pdf * sigma) / (2 * math.sqrt(years)) - (
            rate * strike * discount * _norm_cdf(d2)
        )

    else:

        delta = _norm_cdf(d1) - 1.0
        theta_annual = -(spot * pdf * sigma) / (2 * math.sqrt(years)) + (
            rate * strike * discount * _norm_cdf(-d2)
        )

    gamma = pdf / (spot * sigma * math.sqrt(years))
    vega = spot * pdf * math.sqrt(years) / 100.0

    return {
        "delta": delta,
        "gamma": gamma,
        "theta": theta_annual / CALENDAR_DAYS_PER_YEAR,
        "vega": vega,
        "iv": sigma,
    }


def years_to_expiry(expiry, moment):
    """Calendar-year fraction from ``moment`` to the 16:00 ET expiry close.

    Calendar rather than trading time because that is what an option's clock
    actually runs on -- it decays over a weekend. The floor keeps expiry-day
    contracts from dividing by zero; they are rejected by the ranker's
    ``dte <= 1`` penalty long before it matters.
    """

    import pandas as pd

    close = pd.Timestamp(expiry).tz_localize("America/New_York") + pd.Timedelta(
        hours=16
    )
    now = pd.Timestamp(moment)

    if now.tzinfo is None:

        now = now.tz_localize("America/New_York")

    seconds = (close - now).total_seconds()

    return max(seconds / (CALENDAR_DAYS_PER_YEAR * 24 * 3600), 1e-6)


def greeks_for_contract(
    ticker,
    option_price,
    underlying_price,
    moment,
    rate=DEFAULT_RISK_FREE_RATE,
):
    """Full Greek set for one contract at one instant, from its own price.

    Returns ``None`` when IV cannot be recovered, which the caller must treat
    as "this contract is not rankable" rather than substituting defaults --
    a zero delta scores as if it were 0.55 away from target and quietly
    reorders the chain.
    """

    spec = parse_occ_ticker(ticker)
    years = years_to_expiry(spec["expiry"], moment)

    sigma = implied_volatility(
        option_price,
        underlying_price,
        spec["strike"],
        years,
        rate=rate,
        contract_type=spec["contract_type"],
    )

    if sigma is None:

        return None

    greeks = compute_greeks(
        underlying_price,
        spec["strike"],
        years,
        sigma,
        rate=rate,
        contract_type=spec["contract_type"],
    )

    if greeks is None:

        return None

    greeks.update(
        {
            "strike": spec["strike"],
            "expiry": spec["expiry"],
            "contract_type": spec["contract_type"],
            "dte": max(0, (spec["expiry"] - _as_date(moment)).days),
        }
    )

    return greeks


def _as_date(moment):

    import pandas as pd

    stamp = pd.Timestamp(moment)

    if stamp.tzinfo is not None:

        stamp = stamp.tz_convert("America/New_York")

    return stamp.date()
