from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.analytics.market_coverage import _first_existing, _safe_numeric, load_daily_inputs


@dataclass
class OpportunityFunnel:

    watchlist_symbols: int = 0
    bullish_candidates: int = 0
    bearish_candidates: int = 0
    momentum_pass: int = 0
    entry_pass: int = 0
    risk_pass: int = 0
    option_pass: int = 0
    affordability_pass: int = 0
    realtime_ready: int = 0
    entered: int = 0
    profitable: int = 0

    def as_rows(self):

        return [
            {"stage": "Watchlist", "count": self.watchlist_symbols},
            {"stage": "Bullish", "count": self.bullish_candidates},
            {"stage": "Bearish", "count": self.bearish_candidates},
            {"stage": "Momentum", "count": self.momentum_pass},
            {"stage": "Entry", "count": self.entry_pass},
            {"stage": "Risk", "count": self.risk_pass},
            {"stage": "Options", "count": self.option_pass},
            {"stage": "Affordability", "count": self.affordability_pass},
            {"stage": "Realtime", "count": self.realtime_ready},
            {"stage": "Entered", "count": self.entered},
            {"stage": "Profitable", "count": self.profitable},
        ]


def _truthy(series):

    return series.astype(str).str.lower().isin(["true", "1", "yes"])


def build_opportunity_funnel(report_date: str):

    inputs = load_daily_inputs(report_date)
    scanner = inputs.get("scanner", pd.DataFrame())
    audit = inputs.get("audit", pd.DataFrame())
    paper_events = inputs.get("paper_events", pd.DataFrame())
    source = scanner if scanner is not None and not scanner.empty else audit
    funnel = OpportunityFunnel()

    if source is None or source.empty:

        return funnel, pd.DataFrame(funnel.as_rows())

    rows = source.copy()
    symbol_column = _first_existing(rows, ["Symbol", "symbol"])

    if not symbol_column:

        return funnel, pd.DataFrame(funnel.as_rows())

    latest = rows.dropna(subset=[symbol_column]).copy()
    latest["_symbol"] = latest[symbol_column].astype(str).str.upper().str.strip()
    latest = latest[latest["_symbol"].ne("")].groupby("_symbol").tail(1).copy()
    funnel.watchlist_symbols = len(latest)

    direction_column = _first_existing(latest, ["Candidate Direction", "direction"])
    signal_column = _first_existing(latest, ["Final Signal", "Signal", "final_signal"])
    score_column = _first_existing(latest, ["Setup %", "score", "15m Score"])
    setup_column = _first_existing(latest, ["Setup Valid", "setup_valid"])
    action_column = _first_existing(latest, ["Action Status", "action"])
    option_quality_column = _first_existing(latest, ["Option Quality Score", "option_quality_score"])
    affordable_column = _first_existing(latest, ["Affordable", "affordable"])
    realtime_column = _first_existing(latest, ["Realtime Ready", "realtime_ready"])

    direction = latest[direction_column].astype(str).str.upper() if direction_column else pd.Series("", index=latest.index)
    signal = latest[signal_column].astype(str).str.upper() if signal_column else pd.Series("", index=latest.index)
    action = latest[action_column].astype(str).str.upper() if action_column else pd.Series("", index=latest.index)

    funnel.bullish_candidates = int((direction.eq("CALL") | signal.str.contains("BULLISH", na=False)).sum())
    funnel.bearish_candidates = int((direction.eq("PUT") | signal.str.contains("BEARISH", na=False)).sum())

    if score_column:

        score = _safe_numeric(latest[score_column]).fillna(0)
        funnel.momentum_pass = int((score >= 70).sum())

    if setup_column:

        funnel.entry_pass = int(_truthy(latest[setup_column]).sum())
    else:

        setup_text = latest.get("setup", latest.get("Entry", pd.Series("", index=latest.index))).astype(str).str.upper()
        funnel.entry_pass = int(~setup_text.isin(["", "NO_ENTRY", "NO_SETUP", "NONE", "NAN"]).sum())

    blocked = latest.get("Blocked By", latest.get("blocked_reason", pd.Series("", index=latest.index))).astype(str).str.upper()
    funnel.risk_pass = int(~blocked.str.contains("RISK|RR|GEOMETRY|STOP|TARGET", regex=True, na=False).sum())

    if option_quality_column:

        option_quality = _safe_numeric(latest[option_quality_column]).fillna(0)
        funnel.option_pass = int((option_quality >= 65).sum())
    else:

        funnel.option_pass = int(~blocked.str.contains("OPTION|QUOTE|SPREAD|LIQUID", regex=True, na=False).sum())

    if affordable_column:

        funnel.affordability_pass = int(_truthy(latest[affordable_column]).sum())
    else:

        funnel.affordability_pass = int(~blocked.str.contains("AFFORD|EXPENSIVE|CAPITAL", regex=True, na=False).sum())

    if realtime_column:

        funnel.realtime_ready = int(_truthy(latest[realtime_column]).sum())
    else:

        funnel.realtime_ready = int(~blocked.str.contains("STALE|DELAY|REALTIME", regex=True, na=False).sum())

    entered_symbols = set()
    profitable_symbols = set()

    if paper_events is not None and not paper_events.empty and "symbol" in paper_events.columns:

        event_symbols = paper_events["symbol"].astype(str).str.upper().str.strip()
        event_type = paper_events.get("event_type", pd.Series("", index=paper_events.index)).astype(str).str.upper()
        r_multiple = _safe_numeric(paper_events.get("r_multiple", pd.Series(index=paper_events.index, dtype=object)))
        entered_symbols = set(event_symbols[event_type.eq("OPEN")])
        profitable_symbols = set(event_symbols[r_multiple > 0])

    funnel.entered = int(latest["_symbol"].isin(entered_symbols).sum() or action.isin(["ENTER", "ENTER_PAPER", "OPENED"]).sum())
    funnel.profitable = int(latest["_symbol"].isin(profitable_symbols).sum())

    return funnel, pd.DataFrame(funnel.as_rows())
