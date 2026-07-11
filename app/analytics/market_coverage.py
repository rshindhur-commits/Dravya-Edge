from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from app.storage.daily_paths import daily_path


@dataclass
class MarketCoverage:

    total_watchlist: int = 0
    bullish_movers: int = 0
    bearish_movers: int = 0
    detected: int = 0
    correct_direction: int = 0
    entered: int = 0
    profitable: int = 0
    early_exit: int = 0
    missed: int = 0
    correct_skip: int = 0

    @property
    def detection_rate(self):

        return _pct(self.detected, self.total_watchlist)

    @property
    def correct_direction_rate(self):

        return _pct(self.correct_direction, self.detected)

    @property
    def entry_rate(self):

        return _pct(self.entered, self.total_watchlist)

    @property
    def profitable_rate(self):

        return _pct(self.profitable, self.entered)

    @property
    def coverage_score(self):

        if self.total_watchlist <= 0:

            return None

        detected_component = self.detection_rate or 0
        direction_component = self.correct_direction_rate or 0
        entry_component = self.entry_rate or 0
        profit_component = self.profitable_rate or 0

        return round(
            detected_component * 0.35
            + direction_component * 0.25
            + entry_component * 0.20
            + profit_component * 0.20,
            1
        )


def _pct(numerator, denominator):

    if not denominator:

        return None

    return round(numerator / denominator * 100, 1)


def _read_csv(path: Path):

    try:

        if not path.exists() or path.stat().st_size == 0:

            return pd.DataFrame()

        return pd.read_csv(path)

    except Exception:

        return pd.DataFrame()


def _read_parquet(path: Path):

    try:

        if not path.exists() or path.stat().st_size == 0:

            return pd.DataFrame()

        return pd.read_parquet(path)

    except Exception:

        return pd.DataFrame()


def _first_existing(df, columns):

    for column in columns:

        if column in df.columns:

            return column

    return None


def _safe_numeric(series):

    return pd.to_numeric(series, errors="coerce")


def _normalize_symbol(value):

    return str(value or "").strip().upper()


def load_daily_inputs(report_date: str) -> dict[str, Any]:

    candidate_snapshot = _read_parquet(
        daily_path(report_date, "candidate_snapshots.parquet")
    )

    if candidate_snapshot.empty:

        candidate_snapshot = _read_csv(
            daily_path(report_date, "candidate_snapshots.csv")
        )

    return {
        "scanner": _read_csv(daily_path(report_date, "scanner_output_close.csv")),
        "candidate_snapshot": candidate_snapshot,
        "paper_events": _read_csv(daily_path(report_date, "paper_trade_events.csv")),
        "audit": _read_csv(daily_path(report_date, "market_opportunity_audit.csv")),
    }


def _combined_opportunity_rows(inputs):

    frames = []

    for name in ["audit", "candidate_snapshot", "scanner"]:

        frame = inputs.get(name, pd.DataFrame())

        if frame is None or frame.empty:

            continue

        current = frame.copy()
        current["_source"] = name
        frames.append(current)

    if not frames:

        return pd.DataFrame()

    rows = pd.concat(frames, ignore_index=True, sort=False)
    symbol_column = _first_existing(rows, ["symbol", "Symbol"])

    if not symbol_column:

        return pd.DataFrame()

    rows["_symbol"] = rows[symbol_column].map(_normalize_symbol)

    return rows[rows["_symbol"].ne("")].copy()


def _paper_event_summary(paper_events):

    if paper_events is None or paper_events.empty:

        return pd.DataFrame(columns=["_symbol", "entered", "profitable", "early_exit"])

    events = paper_events.copy()
    symbol_column = _first_existing(events, ["symbol", "Symbol"])

    if not symbol_column:

        return pd.DataFrame(columns=["_symbol", "entered", "profitable", "early_exit"])

    events["_symbol"] = events[symbol_column].map(_normalize_symbol)
    event_type = events.get(
        "event_type",
        pd.Series([""] * len(events), index=events.index)
    ).astype(str).str.upper()
    status = events.get(
        "status",
        pd.Series([""] * len(events), index=events.index)
    ).astype(str).str.upper()
    r_multiple = _safe_numeric(
        events.get("r_multiple", pd.Series([None] * len(events), index=events.index))
    )
    exit_reason = events.get(
        "exit_reason",
        pd.Series([""] * len(events), index=events.index)
    ).astype(str).str.lower()

    rows = []

    for symbol, group in events.groupby("_symbol"):

        group_indexes = group.index
        entered = bool((event_type.loc[group_indexes].eq("OPEN")).any())
        closed_mask = event_type.loc[group_indexes].isin([
            "AUTO_EXIT",
            "MANUAL_CLOSE",
            "CLOSE",
            "CLOSED",
            "EXIT",
        ]) | status.loc[group_indexes].eq("CLOSED")
        profitable = bool((r_multiple.loc[group_indexes][closed_mask] > 0).any())
        early_exit = bool(exit_reason.loc[group_indexes].str.contains("early|weak|guard", regex=True).any())
        rows.append({
            "_symbol": symbol,
            "entered": entered,
            "profitable": profitable,
            "early_exit": early_exit,
        })

    return pd.DataFrame(rows)


def build_market_coverage(report_date: str, move_threshold_pct: float = 2.0):

    inputs = load_daily_inputs(report_date)
    rows = _combined_opportunity_rows(inputs)
    paper_summary = _paper_event_summary(inputs.get("paper_events"))
    coverage = MarketCoverage()

    if rows.empty:

        return coverage, pd.DataFrame(), inputs

    move_column = _first_existing(rows, [
        "market_move_pct",
        "Symbol Move %",
        "symbol_move_pct",
    ])
    score_column = _first_existing(rows, ["score", "15m Score", "setup_percent", "Setup %"])
    action_column = _first_existing(rows, ["action", "Action Status", "current_action_status"])
    direction_column = _first_existing(rows, ["Candidate Direction", "direction"])
    signal_column = _first_existing(rows, ["Final Signal", "Signal", "final_signal"])

    if not move_column:

        return coverage, pd.DataFrame(), inputs

    rows["_move_pct"] = _safe_numeric(rows[move_column]).fillna(0)
    movers = rows[rows["_move_pct"].abs() >= move_threshold_pct].copy()

    if movers.empty:

        return coverage, pd.DataFrame(), inputs

    latest_movers = movers.sort_index().groupby("_symbol").tail(1).copy()
    coverage.total_watchlist = len(latest_movers)
    coverage.bullish_movers = int((latest_movers["_move_pct"] > 0).sum())
    coverage.bearish_movers = int((latest_movers["_move_pct"] < 0).sum())

    entered_symbols = set()
    profitable_symbols = set()
    early_exit_symbols = set()

    if not paper_summary.empty:

        entered_symbols = set(paper_summary.loc[paper_summary["entered"], "_symbol"])
        profitable_symbols = set(paper_summary.loc[paper_summary["profitable"], "_symbol"])
        early_exit_symbols = set(paper_summary.loc[paper_summary["early_exit"], "_symbol"])

    detail_rows = []

    for _, row in latest_movers.iterrows():

        symbol = row["_symbol"]
        score = row.get(score_column) if score_column else None
        action = str(row.get(action_column) or "").upper() if action_column else ""
        direction = str(row.get(direction_column) or "").upper() if direction_column else ""
        signal = str(row.get(signal_column) or "").upper() if signal_column else ""
        detected = bool(action and action not in {"", "WAIT", "AVOID", "NO_ENTRY"}) or pd.notna(score)
        move_pct = float(row["_move_pct"])
        correct_direction = (
            move_pct > 0 and ("CALL" in direction or "BULLISH" in signal)
        ) or (
            move_pct < 0 and ("PUT" in direction or "BEARISH" in signal)
        )
        entered = symbol in entered_symbols or action in {"ENTER", "ENTER_PAPER", "OPENED"}
        profitable = symbol in profitable_symbols
        early_exit = symbol in early_exit_symbols
        missed = detected and not entered and not profitable
        correct_skip = not detected and not profitable

        coverage.detected += int(detected)
        coverage.correct_direction += int(correct_direction)
        coverage.entered += int(entered)
        coverage.profitable += int(profitable)
        coverage.early_exit += int(early_exit)
        coverage.missed += int(missed)
        coverage.correct_skip += int(correct_skip)

        detail_rows.append({
            "symbol": symbol,
            "move_pct": move_pct,
            "detected": detected,
            "correct_direction": correct_direction,
            "entered": entered,
            "profitable": profitable,
            "early_exit": early_exit,
            "missed": missed,
            "action": action,
            "score": score,
        })

    return coverage, pd.DataFrame(detail_rows), inputs
