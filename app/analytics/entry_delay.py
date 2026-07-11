from __future__ import annotations

import pandas as pd

from app.analytics.market_coverage import _first_existing, _normalize_symbol, _safe_numeric, load_daily_inputs


def _parse_time(value):

    return pd.to_datetime(value, errors="coerce", utc=True)


def build_entry_delay(report_date: str):

    inputs = load_daily_inputs(report_date)
    audit = inputs.get("audit", pd.DataFrame())

    if audit is None or audit.empty:

        return pd.DataFrame(), {}

    rows = audit.copy()
    symbol_column = _first_existing(rows, ["symbol", "Symbol"])
    setup_column = _first_existing(rows, ["setup", "Entry"])
    time_column = _first_existing(rows, ["observed_at", "scan_timestamp", "timestamp"])
    action_column = _first_existing(rows, ["action", "Action Status"])
    score_column = _first_existing(rows, ["score", "15m Score"])

    if not symbol_column or not setup_column or not time_column:

        return pd.DataFrame(), {}

    rows["symbol"] = rows[symbol_column].map(_normalize_symbol)
    rows["setup"] = rows[setup_column].astype(str).str.upper()
    rows["_time"] = rows[time_column].map(_parse_time)
    rows["_score"] = _safe_numeric(rows[score_column]) if score_column else None
    rows = rows[rows["symbol"].ne("") & rows["setup"].ne("") & rows["_time"].notna()].copy()

    if rows.empty:

        return pd.DataFrame(), {}

    action = rows[action_column].astype(str).str.upper() if action_column else pd.Series("", index=rows.index)
    rows["_entered"] = action.isin(["ENTER", "ENTER_PAPER", "OPENED"])
    records = []

    for (symbol, setup), group in rows.sort_values("_time").groupby(["symbol", "setup"]):

        first = group.iloc[0]
        entered = group[group["_entered"]]

        if entered.empty:

            continue

        entry = entered.iloc[0]
        delay_minutes = round((entry["_time"] - first["_time"]).total_seconds() / 60, 2)
        score_first = first.get("_score")
        score_entry = entry.get("_score")
        records.append({
            "symbol": symbol,
            "setup": setup,
            "delay_minutes": delay_minutes,
            "score_first": score_first,
            "score_entry": score_entry,
            "score_change": (
                round(score_entry - score_first, 2)
                if pd.notna(score_entry) and pd.notna(score_first)
                else None
            ),
        })

    delay_df = pd.DataFrame(records)

    if delay_df.empty:

        return delay_df, {}

    delays = _safe_numeric(delay_df["delay_minutes"]).dropna()
    summary = {
        "average_minutes": round(float(delays.mean()), 2) if not delays.empty else None,
        "median_minutes": round(float(delays.median()), 2) if not delays.empty else None,
        "longest_minutes": round(float(delays.max()), 2) if not delays.empty else None,
        "best_minutes": round(float(delays.min()), 2) if not delays.empty else None,
        "count": int(len(delays)),
        "bucket_0_2": int(((delays >= 0) & (delays < 2)).sum()),
        "bucket_2_5": int(((delays >= 2) & (delays < 5)).sum()),
        "bucket_5_10": int(((delays >= 5) & (delays < 10)).sum()),
        "bucket_10_plus": int((delays >= 10).sum()),
    }

    return delay_df, summary
