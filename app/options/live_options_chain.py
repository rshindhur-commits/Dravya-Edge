import requests
import os

import re
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.config.settings import settings
from app.options.option_metrics import enrich_option_metrics
from app.utils.runtime_logging import debug_print


POLYGON_API_KEY = settings.polygon_api_key
USE_MOCK_OPTIONS = settings.use_mock_options


def _api_key():

    return (
        settings.polygon_api_key
        or os.getenv("POLYGON_API_KEY", "")
    ).strip()


def _base_url():

    return settings.polygon_base_url.rstrip("/")


def _extract_quote_fields(last_quote, source):

    last_quote = last_quote or {}
    bid = last_quote.get(
        "bid",
        last_quote.get("bid_price", 0)
    ) or 0
    ask = last_quote.get(
        "ask",
        last_quote.get("ask_price", 0)
    ) or 0
    midpoint = last_quote.get("midpoint")

    if not midpoint and bid and ask:

        midpoint = (bid + ask) / 2

    quote_time = None
    quote_timestamp_field = None

    for field in ["last_updated", "sip_timestamp", "timestamp"]:

        if last_quote.get(field) not in [None, ""]:

            quote_time = last_quote[field]
            quote_timestamp_field = field
            break

    return {
        "bid": bid,
        "ask": ask,
        "midpoint": midpoint,
        "quote_time": quote_time,
        "quote_timestamp_field": quote_timestamp_field,
        "quote_timeframe": last_quote.get("timeframe"),
        "quote_source": source,
        "bid_size": last_quote.get("bid_size"),
        "ask_size": last_quote.get("ask_size")
    }


def fetch_latest_option_quote(option_ticker):

    if not option_ticker:

        return None

    try:

        url = f"{_base_url()}/v3/quotes/{option_ticker}"

        response = requests.get(
            url,
            params={
                "order": "desc",
                "limit": 1,
                "sort": "timestamp",
                "apiKey": _api_key()
            },
            timeout=10
        )
        data = response.json()
        results = data.get("results") or []

        if not results:

            return None

        return _extract_quote_fields(
            results[0],
            "quotes_endpoint"
        )

    except Exception as e:

        print(f"[OPTION QUOTE FETCH ERROR] {e}")
        return None


def refresh_contract_quote(contract):

    if not contract:

        return contract

    quote = fetch_latest_option_quote(
        contract.get("ticker")
    )

    if not quote:

        return contract

    updated = dict(contract)
    updated.update({
        "bid": quote.get("bid", 0),
        "ask": quote.get("ask", 0),
        "quote_midpoint": quote.get("midpoint"),
        "quote_timeframe": quote.get("quote_timeframe"),
        "quote_source": quote.get("quote_source"),
        "quote_timestamp_field": quote.get("quote_timestamp_field"),
        "bid_size": quote.get("bid_size"),
        "ask_size": quote.get("ask_size"),
        "quote_time": quote.get("quote_time")
    })

    updated["quote_status"] = _classify_quote_status(
        200,
        quote,
        updated.get("bid", 0),
        updated.get("ask", 0)
    )
    updated["quote_status_reason"] = _quote_status_reason(
        updated["quote_status"]
    )

    return _enrich_contract(updated)


def _enrich_contract(contract):

    enriched = enrich_option_metrics(
        contract,
        delayed_minutes=settings.option_delayed_quote_minutes,
        max_quote_age_minutes=settings.option_max_quote_age_minutes,
        min_volume=settings.option_min_volume,
        min_open_interest=settings.option_min_open_interest,
        max_spread_pct=settings.option_max_spread_pct,
        allow_0dte=settings.option_allow_0dte,
        allow_1dte=settings.option_allow_1dte,
        min_dte=settings.option_min_dte,
        preferred_min_dte=settings.option_preferred_min_dte,
        preferred_max_dte=settings.option_preferred_max_dte,
        max_dte=settings.option_max_dte
    )

    freshness = enriched.get("quote_freshness")

    if enriched.get("quote_timeframe") == "DELAYED":

        enriched["quote_status"] = "DELAYED_QUOTE"
        enriched["quote_status_reason"] = _quote_status_reason(
            "DELAYED_QUOTE"
        )

    if (
        enriched.get("quote_status") == "QUOTE_OK"
        and freshness in [
            "DELAYED_QUOTE",
            "STALE_QUOTE"
        ]
    ):

        enriched["quote_status"] = freshness
        enriched["quote_status_reason"] = _quote_status_reason(
            freshness
        )

    return enriched


def _is_option_market_open():

    current_et = datetime.now(
        ZoneInfo("America/New_York")
    )

    if current_et.weekday() >= 5:

        return False

    return (
        time(9, 30)
        <= current_et.time()
        <= time(16, 0)
    )


def _quote_status_reason(status):

    reasons = {
        "QUOTE_OK": "Live option bid/ask available",
        "NO_BID_ASK": "No valid bid/ask returned by provider",
        "OPTION_MARKET_CLOSED": "Option market closed; live bid/ask unavailable",
        "RATE_LIMITED": "Polygon option endpoint rate limited",
        "PROVIDER_ERROR": "Polygon option endpoint returned an error",
        "NO_QUOTE_SNAPSHOT": "No option quote snapshot returned",
        "DELAYED_QUOTE": "Option quote appears delayed; confirm broker premium",
        "STALE_QUOTE": "Option quote is too stale for intraday execution"
    }

    return reasons.get(
        status,
        "Option quote unavailable"
    )


def _classify_quote_status(
    response_status_code,
    last_quote,
    bid,
    ask
):

    if response_status_code == 429:

        return "RATE_LIMITED"

    if response_status_code >= 500:

        return "PROVIDER_ERROR"

    if not last_quote:

        if not _is_option_market_open():

            return "OPTION_MARKET_CLOSED"

        return "NO_QUOTE_SNAPSHOT"

    if bid <= 0 or ask <= 0:

        if not _is_option_market_open():

            return "OPTION_MARKET_CLOSED"

        return "NO_BID_ASK"

    return "QUOTE_OK"


def extract_dte(option_symbol):

    try:

        import re
        from datetime import datetime

        symbol = option_symbol.replace(
            "O:",
            ""
        )

        match = re.search(
            r'(\d{6})[CP]',
            symbol
        )

        if match is None:

            return 999

        expiry_str = match.group(1)

        debug_print(
            f"[DTE DEBUG] "
            f"{option_symbol} -> {expiry_str}"
        )

        expiry_date = datetime.strptime(
            expiry_str,
            "%y%m%d"
        )

        dte = (
            expiry_date.date()
            - datetime.now().date()
        ).days

        return max(dte, 0)

    except Exception as e:

        print(
            f"[DTE PARSE ERROR] "
            f"{option_symbol} "
            f"{e}"
        )

        return 999


def fetch_options_chain(
    symbol,
    latest_price,
    limit=250,
    direction=None
):

    """
    Fetch live options chain snapshot
    """

    if USE_MOCK_OPTIONS:

        mock_contracts = [

            {
                "ticker":
                    "O:QQQ260522P00715000",

                "type":
                    "put",

                "strike":
                    715,

                "expiration":
                    "2026-05-22",

                "volume":
                    4200,

                "close":
                    4.25,

                "bid":
                    4.1,

                "ask":
                    4.3,

                "open_interest":
                    8500,

                "iv":
                    28.4,

                "delta":
                    -0.54,

                "gamma":
                    0.031,

                "theta":
                    -0.18,

                "vega":
                    0.11

            },

            {
                "ticker":
                    "O:QQQ260522P00710000",

                "type":
                    "put",

                "strike":
                    710,

                "expiration":
                    "2026-05-22",

                "volume":
                    2200,

                "close":
                    3.10,

                "bid":
                    3.0,

                "ask":
                    3.2,

                "open_interest":
                    5200,

                "iv":
                    26.8,

                "delta":
                    -0.48,

                "gamma":
                    0.028,

                "theta":
                    -0.15,

                "vega":
                    0.09

            },

            {
                "ticker":
                    "O:QQQ260522P00720000",

                "type":
                    "put",

                "strike":
                    720,

                "expiration":
                    "2026-05-22",

                "volume":
                    6000,

                "close":
                    5.80,

                "bid":
                    5.65,

                "ask":
                    5.95,

                "open_interest":
                    12000,

                "iv":
                    29.1,

                "delta":
                    -0.66,

                "gamma":
                    0.035,

                "theta":
                    -0.24,

                "vega":
                    0.14

            }

        ]

        return [
            _enrich_contract(contract)
            for contract in mock_contracts
        ]

    try:

        strike_window = max(
            10,
            latest_price * 0.015
        )

        lower_strike = round(
            latest_price - strike_window
        )

        upper_strike = round(
            latest_price + strike_window
        )

        contract_type = None

        if direction == "CALL":

            contract_type = "call"

        elif direction == "PUT":

            contract_type = "put"

        contract_type_query = (
            f"&contract_type={contract_type}"
            if contract_type
            else ""
        )

        url = (

            f"{_base_url()}/"
            f"v3/snapshot/options/"
            f"{symbol}"

            f"?strike_price.gte="
            f"{lower_strike}"

            f"&strike_price.lte="
            f"{upper_strike}"

            f"{contract_type_query}"

            f"&limit={limit}"

            f"&sort=expiration_date"

            f"&apiKey={_api_key()}"

        )

        response = requests.get(
            url,
            timeout=10
        )

        response_status_code = response.status_code

        data = response.json()

        results = data.get(
            "results",
            []
        )

        contracts = []

        for item in results:

            underlying = item.get(
                "underlying_asset",
                {}
            ).get(
                "ticker"
            )

            if underlying != symbol:
                continue            

            details = item.get(
                "details",
                {}
            )

            greeks = item.get(
                "greeks",
                {}
            )

            day = item.get(
                "day",
                {}
            )

            quote_fields = _extract_quote_fields(
                item.get("last_quote", {}),
                "snapshot_last_quote"
            )

            quote_status = _classify_quote_status(
                response_status_code,
                quote_fields,
                quote_fields["bid"],
                quote_fields["ask"]
            )

            contract = _enrich_contract({

                "ticker":
                    details.get(
                        "ticker"
                    ),

                "type":
                    details.get(
                        "contract_type"
                    ),

                "strike":
                    details.get(
                        "strike_price",
                        0
                    ),

                "expiration":
                    details.get(
                        "expiration_date"
                    ),

                "dte":
                    extract_dte(

                        details.get(
                            "ticker",
                            ""
                        )

                    ),                    

                "volume":
                    day.get(
                        "volume",
                        0
                    ),

                "close":
                    day.get(
                        "close",
                        0
                    ),

                "bid":
                    quote_fields["bid"],

                "ask":
                    quote_fields["ask"],

                "quote_midpoint":
                    quote_fields.get("midpoint"),

                "quote_timeframe":
                    quote_fields.get("quote_timeframe"),

                "quote_source":
                    quote_fields.get("quote_source"),

                "quote_timestamp_field":
                    quote_fields.get("quote_timestamp_field"),

                "bid_size":
                    quote_fields.get("bid_size"),

                "ask_size":
                    quote_fields.get("ask_size"),

                "quote_status":
                    quote_status,

                "quote_status_reason":
                    _quote_status_reason(
                        quote_status
                    ),

                "quote_time":
                    quote_fields.get("quote_time"),

                "open_interest":
                    item.get(
                        "open_interest",
                        0
                    ),

                "iv":
                    item.get(
                        "implied_volatility",
                        0
                    ),

                "delta":
                    greeks.get(
                        "delta",
                        0
                    ),

                "gamma":
                    greeks.get(
                        "gamma",
                        0
                    ),

                "theta":
                    greeks.get(
                        "theta",
                        0
                    ),

                "vega":
                    greeks.get(
                        "vega",
                        0
                    )

            })

            contracts.append(
                contract
            )

            debug_print(
                f"[OPTION CONTRACT] "
                f"{symbol} -> "
                f"{contract['ticker']}"
            )

        return contracts

    except Exception as e:

        print(
            f"[OPTIONS FETCH ERROR] {e}"
        )

        return []


def fetch_option_snapshot(symbol, option_ticker):

    if not option_ticker:

        return None

    if USE_MOCK_OPTIONS:

        contracts = fetch_options_chain(
            symbol,
            0,
            limit=250
        )

        for contract in contracts:

            if contract.get("ticker") == option_ticker:

                return contract

        return None

    try:

        url = (
            f"{_base_url()}/"
            f"v3/snapshot/options/"
            f"{symbol}/"
            f"{option_ticker}"
            f"?apiKey={_api_key()}"
        )

        response = requests.get(
            url,
            timeout=10
        )

        response_status_code = response.status_code
        data = response.json()
        item = data.get("results") or {}

        if not item:

            return None

        details = item.get("details", {})
        greeks = item.get("greeks", {})
        day = item.get("day", {})
        quote_fields = _extract_quote_fields(
            item.get("last_quote", {}),
            "snapshot_last_quote"
        )

        if quote_fields["bid"] <= 0 or quote_fields["ask"] <= 0:

            fallback_quote = fetch_latest_option_quote(option_ticker)

            if fallback_quote:

                quote_fields = fallback_quote

        quote_status = _classify_quote_status(
            response_status_code,
            quote_fields,
            quote_fields["bid"],
            quote_fields["ask"]
        )

        return _enrich_contract({
            "ticker": details.get("ticker", option_ticker),
            "type": details.get("contract_type"),
            "strike": details.get("strike_price", 0),
            "expiration": details.get("expiration_date"),
            "volume": day.get("volume", 0),
            "close": day.get("close", 0),
            "bid": quote_fields["bid"],
            "ask": quote_fields["ask"],
            "quote_midpoint": quote_fields.get("midpoint"),
            "quote_timeframe": quote_fields.get("quote_timeframe"),
            "quote_source": quote_fields.get("quote_source"),
            "quote_timestamp_field": quote_fields.get("quote_timestamp_field"),
            "bid_size": quote_fields.get("bid_size"),
            "ask_size": quote_fields.get("ask_size"),
            "quote_status": quote_status,
            "quote_status_reason": _quote_status_reason(quote_status),
            "quote_time": quote_fields.get("quote_time"),
            "open_interest": item.get("open_interest", 0),
            "iv": item.get("implied_volatility", 0),
            "delta": greeks.get("delta", 0),
            "gamma": greeks.get("gamma", 0),
            "theta": greeks.get("theta", 0),
            "vega": greeks.get("vega", 0)
        })

    except Exception as e:

        print(
            f"[OPTION SNAPSHOT ERROR] {e}"
        )

        return None