"""Point-in-time option pricing for replays.

The system trades options but the previous backtester priced the underlying and
hardcoded ``Option Quality Score: 100``, ``Option Spread %: 0``,
``Affordable: True``. Every cost that actually decides whether an intraday
options strategy makes money -- the spread paid on entry and again on exit,
theta over the hold, the strike being less liquid than the underlying -- was
assumed away. This module supplies the real numbers.

Two Polygon sources, used for different jobs:

*Aggregates* (``/v2/aggs/ticker/O:...``) give the contract's price path. Cheap,
cacheable per contract-day, and enough to evaluate an exit rule bar by bar.

*NBBO quotes* (``/v3/quotes/O:...``) give the bid and ask at one instant. A full
day of ticks for one contract runs to tens of thousands of rows, so they are
never bulk-loaded; a fill needs exactly one quote, fetched with
``timestamp.lte`` and ``limit=1``.

The fill rule is the one live had to be corrected into: a long option is bought
at the **ask** and sold at the **bid**. Reading both sides from one mid, or
from a single live-refreshed key, returns minus the spread on every trade --
which is the defect ``eb56f75`` fixed in ``option_pnl_pct_net``. Note that
``paper_trades.payload``'s ``option_bid``/``option_ask`` are still overwritten
by each scan's refresh, so they are the *latest* quote rather than the entry
quote; only ``option_mid``/``option_entry_mid`` is a faithful entry record, and
it is the only field this module validates against.
"""

import os
import time
from datetime import timedelta

import pandas as pd
import requests

from app.backtesting.historical_market_data import (
    HistoricalDataError,
    POLYGON_BASE_URL,
    fetch_bars,
)

MARKET_TZ = "America/New_York"

# Contracts whose quoted spread exceeds this are untradeable in practice; a
# backtest that fills them anyway is manufacturing edge out of a price nobody
# could have transacted at.
DEFAULT_MAX_SPREAD_PCT = 25.0


def _api_key():

    key = os.getenv("POLYGON_API_KEY")

    if not key:

        raise HistoricalDataError(
            "POLYGON_API_KEY is not set; option replay cannot run"
        )

    return key


def _to_utc(moment):

    stamp = pd.Timestamp(moment)

    if stamp.tzinfo is None:

        stamp = stamp.tz_localize(MARKET_TZ)

    return stamp.tz_convert("UTC")


def list_contracts_as_of(
    underlying,
    as_of,
    contract_type=None,
    limit=250,
    strike_min=None,
    strike_max=None,
    expiry_min=None,
    expiry_max=None,
    timeout=60,
):
    """Contracts listed for ``underlying`` as they stood on ``as_of``.

    ``as_of`` is what keeps strike selection honest: querying today's chain for
    a trade in the past silently drops contracts that have since expired --
    which for short-DTE selection is most of the ones that mattered.

    The strike and expiry bounds are pushed into the request rather than
    applied to the response. Unbounded the endpoint paginates through thousands
    of rows per scan and times out; bounded, it is a single page. The caller
    knows the window it wants, so there is no reason to transfer the rest.

    **Do not add ``expired=true`` here.** It reads like the right flag for
    querying a historical chain and it is not: combined with ``as_of`` it
    returns zero results, and unbounded it also stops honouring
    ``underlying_ticker`` -- a request for NVDA came back with NAX contracts
    expiring in 2010. ``as_of`` alone already returns the chain as it stood on
    that date, including contracts that have since expired, which is exactly
    what a replay needs.
    """

    day = pd.Timestamp(as_of).date().isoformat()

    params = {
        "apiKey": _api_key(),
        "underlying_ticker": underlying,
        "as_of": day,
        "limit": limit,
    }

    if contract_type:

        params["contract_type"] = contract_type.lower()

    if strike_min is not None:

        params["strike_price.gte"] = strike_min

    if strike_max is not None:

        params["strike_price.lte"] = strike_max

    if expiry_min is not None:

        params["expiration_date.gte"] = pd.Timestamp(expiry_min).date().isoformat()

    if expiry_max is not None:

        params["expiration_date.lte"] = pd.Timestamp(expiry_max).date().isoformat()

    url = f"{POLYGON_BASE_URL}/v3/reference/options/contracts"

    contracts = []
    next_url = url

    while next_url:

        response = requests.get(next_url, params=params, timeout=timeout)

        if response.status_code != 200:

            raise HistoricalDataError(
                f"Polygon returned {response.status_code} listing contracts for "
                f"{underlying} as of {day}: {response.text[:200]}"
            )

        payload = response.json()
        contracts.extend(payload.get("results") or [])

        next_url = payload.get("next_url")
        params = {"apiKey": _api_key()} if next_url else params

    return contracts


def quote_at(ticker, moment, lookback_minutes=30, max_retries=4):
    """The last NBBO for ``ticker`` at or before ``moment``.

    Returns ``{"bid", "ask", "mid", "spread_pct", "quote_time", "age_seconds"}``
    or ``None`` when the contract had no quote in the lookback window -- which
    is itself a finding, not a glitch: an option nobody was quoting is one the
    strategy could not have traded.

    ``lookback_minutes`` bounds how stale a quote may be. Illiquid strikes go
    minutes between updates, and filling against an hour-old quote would be
    fiction.
    """

    end = _to_utc(moment)
    start = end - timedelta(minutes=lookback_minutes)

    params = {
        "apiKey": _api_key(),
        "timestamp.lte": int(end.value),
        "timestamp.gte": int(start.value),
        "order": "desc",
        "sort": "timestamp",
        "limit": 1,
    }

    url = f"{POLYGON_BASE_URL}/v3/quotes/{ticker}"

    payload = None

    for attempt in range(max_retries):

        response = requests.get(url, params=params, timeout=30)

        if response.status_code == 200:

            payload = response.json()
            break

        if response.status_code != 429:

            raise HistoricalDataError(
                f"Polygon returned {response.status_code} quoting {ticker} at "
                f"{end}: {response.text[:200]}"
            )

        time.sleep(2 ** attempt)

    if payload is None:

        raise HistoricalDataError(
            f"Polygon rate limit not cleared quoting {ticker}"
        )

    results = payload.get("results") or []

    if not results:

        return None

    quote = results[0]
    bid = quote.get("bid_price")
    ask = quote.get("ask_price")

    if not bid or not ask or bid <= 0 or ask <= 0:

        return None

    mid = (bid + ask) / 2
    quote_time = pd.to_datetime(quote["sip_timestamp"], unit="ns", utc=True)

    return {
        "bid": float(bid),
        "ask": float(ask),
        "mid": float(mid),
        "spread_pct": float((ask - bid) / mid * 100) if mid else None,
        "quote_time": quote_time,
        "age_seconds": float((end - quote_time).total_seconds()),
    }


def option_bars(ticker, trading_day, lookback_days=5, use_cache=True):
    """5m bars for one contract, ending on ``trading_day``."""

    day = pd.Timestamp(trading_day).date()
    start = day - timedelta(days=lookback_days)

    return fetch_bars(
        ticker,
        start.isoformat(),
        day.isoformat(),
        multiplier=5,
        timespan="minute",
        use_cache=use_cache,
    )


def price_at(bars, moment, bar_minutes=5):
    """Last completed option bar close at or before ``moment``.

    Same no-lookahead rule as the underlying: a bar is readable only once its
    interval has finished. Used for marking an open position between fills,
    where a quote request per symbol per scan would be prohibitive.
    """

    if bars is None or bars.empty:

        return None

    cutoff = _to_utc(moment) - timedelta(minutes=bar_minutes)
    visible = bars[bars.index <= cutoff]

    if visible.empty:

        return None

    return float(visible["Close"].iloc[-1])


def fill_price(quote, side):
    """What a marketable order actually pays.

    ``side`` is ``BUY`` or ``SELL``. Buying lifts the ask, selling hits the
    bid. This is the whole reason an options backtest priced off mids reads
    better than the account ever will.
    """

    if not quote:

        return None

    direction = str(side or "").upper()

    if direction == "BUY":

        return quote["ask"]

    if direction == "SELL":

        return quote["bid"]

    raise ValueError(f"side must be BUY or SELL, got {side!r}")


def round_trip_cost_pct(quote):
    """Spread cost of entering and exiting once, as a percent of mid.

    Buying the ask and selling the bid costs the full spread, so a contract
    quoted 3.45/3.80 starts the position 9.7% down against its own mid. Against
    the ~0.3-1.8% option moves in the 2026-07-30/31 trades, that is not a
    detail at the edges -- it is the dominant term.
    """

    if not quote or not quote.get("mid"):

        return None

    return float((quote["ask"] - quote["bid"]) / quote["mid"] * 100)


def is_tradeable(quote, max_spread_pct=DEFAULT_MAX_SPREAD_PCT):

    if not quote:

        return False, "NO_QUOTE"

    spread = quote.get("spread_pct")

    if spread is None:

        return False, "NO_SPREAD"

    if spread > max_spread_pct:

        return False, f"SPREAD_{spread:.1f}PCT"

    return True, None
