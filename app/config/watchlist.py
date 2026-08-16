import os

from app.utils.polygon_client import (
    get_polygon_api_key,
    get_polygon_base_url,
    safe_request
)


CORE_WATCHLIST = [
    "QQQ",
    "SPY",
    "NVDA",
    "AAPL",
    "MSFT",
]


# MU, META and ARM were removed 2026-08-16. Over 21 archived sessions they
# produced 431 candidates and, priced on the best contract their chains offer
# with the cost cap lifted entirely, a **0% win rate across 122 trades** -- not
# one winner. MU and META also carry the worst underlying signals measured
# anywhere in this project, -0.678R at an 8% win rate and -0.719R at 4%.
# Nothing reachable from any threshold fixes that. See
# docs/TRADE_QUALITY_PLAN.md section 7.3c.
#
# AVGO, SMH and GOOGL were kept for the opposite reason: they are among the best
# signals in the universe and were merely unaffordable. They trade through the
# per-symbol exception in OPTION_MAX_CONTRACT_COST_BY_SYMBOL.
#
# AMD, PANW and AMAT are kept and still expected to produce nothing. Their
# option economics are a positive mean over a negative median at a 45% win rate
# -- a few winners carrying a losing book -- so they are watched rather than
# funded. Remove them only on evidence, not on the fact that they are quiet.
WATCHLIST = [
    *CORE_WATCHLIST,
    "AMZN",
    "GOOGL",
    "TSLA",
    "AMD",
    "AVGO",
    "PLTR",
    "NFLX",
    "CRWD",
    "SMCI",
    "SPCX",
    "SMH",
    "TSM",
    "AMAT",
    "MRVL",
    "ORCL",
    "PANW",
    "JPM",
    "XOM"
]


MARKET_REFERENCE_SYMBOLS = [
    "SMH",
    "SOXX",
    "XLK",
    "XLF",
    "XLE",
    "VIX"
]


REFERENCE_FETCH_SYMBOLS = {
    "VIX": "VIXY"
}


def _dynamic_watchlist_enabled():

    return str(os.getenv("DYNAMIC_WATCHLIST_ENABLED", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on"
    }


def _snapshot_candidates(limit):

    api_key = get_polygon_api_key()

    if not api_key:

        return []

    try:

        response = safe_request(
            f"{get_polygon_base_url()}/v2/snapshot/locale/us/markets/stocks/tickers",
            params={
                "apiKey": api_key
            },
            timeout=20
        )
        tickers = response.json().get("tickers") or []

    except Exception as exc:

        print(f"[WATCHLIST WARNING] dynamic snapshot unavailable: {exc}")
        return []

    candidates = []

    for ticker in tickers:

        symbol = str(ticker.get("ticker") or "").strip().upper()

        if not symbol or "." in symbol or "-" in symbol:

            continue

        day = ticker.get("day") or {}
        previous_day = ticker.get("prevDay") or {}
        current_volume = day.get("v") or 0
        previous_volume = previous_day.get("v") or 0

        try:

            relative_volume = (
                float(current_volume) / float(previous_volume)
                if previous_volume
                else 0.0
            )
            change_pct = float(ticker.get("todaysChangePerc") or 0.0)

        except Exception:

            continue

        candidates.append(
            {
                "symbol": symbol,
                "change_pct": change_pct,
                "relative_volume": relative_volume,
                "volume": float(current_volume or 0),
            }
        )

    top_gainers = sorted(
        candidates,
        key=lambda item: item["change_pct"],
        reverse=True
    )[:limit]
    top_losers = sorted(
        candidates,
        key=lambda item: item["change_pct"]
    )[:limit]
    top_relative_volume = sorted(
        candidates,
        key=lambda item: item["relative_volume"],
        reverse=True
    )[:limit]

    symbols = []

    for bucket in [top_gainers, top_losers, top_relative_volume]:

        for candidate in bucket:

            symbols.append(candidate["symbol"])

    return symbols


def _dedupe_symbols(symbols, limit):

    deduped = []
    seen = set()

    for symbol in symbols:

        symbol = str(symbol or "").strip().upper()

        if not symbol or symbol in seen:

            continue

        deduped.append(symbol)
        seen.add(symbol)

        if len(deduped) >= limit:

            break

    return deduped


def get_scanner_watchlist():

    if not _dynamic_watchlist_enabled():

        return list(WATCHLIST)

    limit = int(os.getenv("DYNAMIC_WATCHLIST_SIZE", "25"))
    movers_per_bucket = int(os.getenv("DYNAMIC_WATCHLIST_MOVERS_PER_BUCKET", "12"))
    dynamic_symbols = _snapshot_candidates(movers_per_bucket)
    resolved = _dedupe_symbols(
        [
            *CORE_WATCHLIST,
            *dynamic_symbols,
            *WATCHLIST,
        ],
        limit
    )

    return resolved or list(WATCHLIST)