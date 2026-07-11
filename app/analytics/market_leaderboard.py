from __future__ import annotations

import pandas as pd

from app.analytics.loss_attribution import build_loss_attribution
from app.analytics.market_coverage import _first_existing, _safe_numeric, load_daily_inputs


def build_market_leaderboard(report_date: str, limit: int = 15):

    inputs = load_daily_inputs(report_date)
    audit = inputs.get("audit", pd.DataFrame())
    paper_events = inputs.get("paper_events", pd.DataFrame())
    attribution = build_loss_attribution(report_date)

    if audit is None or audit.empty:

        return pd.DataFrame()

    rows = audit.copy()
    symbol_column = _first_existing(rows, ["symbol", "Symbol"])
    move_column = _first_existing(rows, ["market_move_pct", "Symbol Move %"])

    if not symbol_column or not move_column:

        return pd.DataFrame()

    rows["Symbol"] = rows[symbol_column].astype(str).str.upper().str.strip()
    rows["Move %"] = _safe_numeric(rows[move_column]).fillna(0)
    rows = rows[rows["Symbol"].ne("")].sort_index().groupby("Symbol").tail(1).copy()
    rows["Detected"] = rows.get("action", pd.Series("", index=rows.index)).astype(str).str.upper().ne("WAIT")
    rows["Direction"] = rows.get("setup", pd.Series("", index=rows.index)).astype(str)
    rows["Entered"] = rows.get("action", pd.Series("", index=rows.index)).astype(str).str.upper().isin(["ENTER", "ENTER_PAPER", "OPENED"])
    rows["Result"] = None

    if paper_events is not None and not paper_events.empty and "symbol" in paper_events.columns:

        event_symbols = paper_events["symbol"].astype(str).str.upper().str.strip()
        r_multiple = _safe_numeric(paper_events.get("r_multiple", pd.Series(index=paper_events.index, dtype=object)))

        for symbol in rows["Symbol"]:

            values = r_multiple[event_symbols.eq(symbol)].dropna()

            if not values.empty:

                rows.loc[rows["Symbol"].eq(symbol), "Result"] = values.iloc[-1]

    rows["Miss Reason"] = None

    if attribution is not None and not attribution.empty:

        reason_by_symbol = attribution.drop_duplicates("symbol", keep="last").set_index("symbol")["reason"].to_dict()
        rows["Miss Reason"] = rows["Symbol"].map(reason_by_symbol)

    rows = rows.sort_values("Move %", key=lambda series: series.abs(), ascending=False)

    return rows[["Symbol", "Move %", "Detected", "Direction", "Entered", "Result", "Miss Reason"]].head(limit)
