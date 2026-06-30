import requests
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:

    sys.path.insert(0, str(ROOT_DIR))

from app.config.settings import settings
from app.indicators.technical_indicators import get_polygon_data
from app.options.option_metrics import calculate_spread_pct, classify_quote_freshness
from app.options.live_options_chain import fetch_latest_option_quote
from app.options.options_recommender import recommend_live_option_bundle


def _status(label, passed, detail=""):

    result = "PASS" if passed else "FAIL"
    print(f"{label}: {result} {detail}".strip())


def _age_minutes(timestamp):

    try:

        freshness = classify_quote_freshness(
            timestamp,
            delayed_minutes=settings.option_delayed_quote_minutes,
            max_quote_age_minutes=settings.option_max_quote_age_minutes
        )
        return freshness.get("quote_age_minutes"), freshness.get("quote_freshness")

    except Exception:

        return None, "UNKNOWN"


def _latest_underlying_price(symbol="SPY"):

    df = get_polygon_data(
        symbol,
        5,
        "minute",
        1
    )

    if df.empty:

        return None, None

    latest = df.index[-1]

    if latest.tzinfo is None:

        latest = latest.tz_localize("UTC")

    age = (datetime.now(timezone.utc) - latest.tz_convert("UTC")).total_seconds() / 60
    price = float(df["Close"].iloc[-1])

    return price, age


def check_stock_aggregates(symbol="SPY"):

    price, age = _latest_underlying_price(symbol)

    if price is None:

        _status("Stocks real-time aggregate", False, "no candles returned")
        return None

    passed = age <= settings.max_stock_data_delay_minutes + 5

    _status(
        "Stocks real-time aggregate",
        passed,
        f"price={price} age_minutes={round(age, 2)}"
    )

    return price


def check_stock_snapshot(symbol="SPY"):

    url = f"{settings.polygon_base_url}/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}"
    response = requests.get(
        url,
        params={"apiKey": settings.polygon_api_key},
        timeout=10
    )

    passed = response.status_code == 200
    _status(
        "Stock snapshot entitlement",
        passed,
        f"status={response.status_code}"
    )


def check_option_quotes(symbol="SPY", underlying_price=None):

    if underlying_price is None:

        underlying_price, _ = _latest_underlying_price(symbol)

    if underlying_price is None:

        underlying_price = 700

    bundle = recommend_live_option_bundle(
        symbol,
        underlying_price,
        "BEARISH",
        "BREAKDOWN_SHORT"
    )

    contract = bundle.get("primary")

    if not contract:

        _status("Options ranked primary", False, "no ranked contract returned")
        return

    ticker = contract.get("ticker")

    _status(
        "Options ranked primary",
        True,
        f"ticker={ticker} dte={contract.get('dte')} bucket={contract.get('expiration_bucket')}"
    )

    snapshot_quote_ok = (
        contract.get("bid", 0) > 0
        and contract.get("ask", 0) > 0
    )

    _status(
        "Options snapshot last_quote",
        snapshot_quote_ok,
        f"ticker={ticker} bid={contract.get('bid')} ask={contract.get('ask')} source={contract.get('quote_source')} timeframe={contract.get('quote_timeframe')}"
    )

    quote = fetch_latest_option_quote(ticker)
    endpoint_ok = bool(quote and quote.get("bid", 0) > 0 and quote.get("ask", 0) > 0)

    _status(
        "Options quotes endpoint",
        endpoint_ok,
        f"bid={quote.get('bid') if quote else None} ask={quote.get('ask') if quote else None} source={quote.get('quote_source') if quote else None}"
    )

    source_quote = quote if endpoint_ok else contract
    spread = calculate_spread_pct(
        source_quote.get("bid"),
        source_quote.get("ask")
    )
    quote_time = source_quote.get("quote_time")
    age, freshness = _age_minutes(quote_time)

    _status(
        "Option bid/ask",
        source_quote.get("bid", 0) > 0 and source_quote.get("ask", 0) > 0,
        f"bid={source_quote.get('bid')} ask={source_quote.get('ask')}"
    )
    _status(
        "Option spread",
        spread is not None and spread <= settings.option_max_spread_pct,
        f"spread_pct={spread}"
    )
    _status(
        "Quote freshness",
        freshness == "LIVE_QUOTE",
        f"freshness={freshness} age_minutes={age}"
    )


def main():

    print("=== Realtime Entitlement Diagnostic ===")
    print(f"base_url={settings.polygon_base_url}")
    print(f"max_stock_data_delay_minutes={settings.max_stock_data_delay_minutes}")
    underlying_price = check_stock_aggregates("SPY")
    check_stock_snapshot("SPY")
    check_option_quotes("SPY", underlying_price)


if __name__ == "__main__":

    main()
