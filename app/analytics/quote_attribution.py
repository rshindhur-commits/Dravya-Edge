from __future__ import annotations

import hashlib

import pandas as pd

from app.storage.daily_paths import daily_path


QUOTE_ATTRIBUTION_COLUMNS = [
    "attribution_id",
    "trading_day",
    "scan_id",
    "scanner_timestamp",
    "symbol",
    "option_ticker",
    "quote_timestamp",
    "quote_age_seconds",
    "source_timestamp_field",
    "quote_source",
    "allowed_age_seconds",
    "final_classification",
    "reason",
]
NON_LIVE_CLASSIFICATIONS = {
    "STALE_QUOTE",
    "DELAYED_QUOTE",
    "UNKNOWN_QUOTE_TIME",
}


def _value(row, *names):

    for name in names:

        value = row.get(name)

        if value in [None, ""]:

            continue

        try:

            if pd.isna(value):

                continue

        except Exception:

            pass

        return value

    return None


def build_quote_attribution(rows, trading_day, scan_id, observed_at):

    records = []

    for row in rows or []:

        classification = str(
            _value(
                row,
                "Option Quote Freshness",
                "option_quote_freshness"
            )
            or ""
        ).upper()

        if classification not in NON_LIVE_CLASSIFICATIONS:

            continue

        symbol = _value(row, "Symbol", "symbol")
        option_ticker = _value(row, "Option Ticker", "option_ticker")
        scanner_timestamp = (
            _value(row, "Current ET", "scan_timestamp")
            or (
                observed_at.isoformat()
                if observed_at
                else None
            )
        )
        source = "|".join([
            str(scan_id),
            str(symbol),
            str(option_ticker),
            classification,
        ])
        records.append({
            "attribution_id": hashlib.sha256(source.encode()).hexdigest()[:24],
            "trading_day": trading_day,
            "scan_id": scan_id,
            "scanner_timestamp": scanner_timestamp,
            "symbol": symbol,
            "option_ticker": option_ticker,
            "quote_timestamp": _value(
                row,
                "Option Quote Timestamp",
                "option_quote_timestamp"
            ),
            "quote_age_seconds": _value(
                row,
                "Option Quote Age Seconds",
                "option_quote_age_seconds"
            ),
            "source_timestamp_field": _value(
                row,
                "Option Quote Timestamp Field",
                "option_quote_timestamp_field"
            ),
            "quote_source": _value(
                row,
                "Option Quote Source",
                "option_quote_source"
            ),
            "allowed_age_seconds": _value(
                row,
                "Option Quote Allowed Age Seconds",
                "option_quote_allowed_age_seconds"
            ),
            "final_classification": classification,
            "reason": _value(
                row,
                "Option Quote Freshness Reason",
                "option_quote_freshness_reason"
            ),
        })

    return pd.DataFrame(records, columns=QUOTE_ATTRIBUTION_COLUMNS)


def write_quote_attribution(rows, trading_day, scan_id, observed_at):

    attribution = build_quote_attribution(
        rows,
        trading_day,
        scan_id,
        observed_at
    )

    if attribution.empty:

        return None

    path = daily_path(trading_day, "quote_attribution.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        pd.read_csv(path)
        if path.exists() and path.stat().st_size
        else pd.DataFrame()
    )
    combined = pd.concat(
        [existing, attribution],
        ignore_index=True,
        sort=False
    )
    combined.drop_duplicates(
        subset=["attribution_id"],
        keep="last"
    ).to_csv(path, index=False)

    from app.db.quote_attribution_repository import QuoteAttributionRepository

    QuoteAttributionRepository().batch_upsert(
        attribution.to_dict("records")
    )
    return {
        "path": str(path),
        "rows": len(attribution)
    }