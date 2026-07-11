from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from app.analytics.market_coverage import _read_csv, _safe_numeric
from app.analytics.scanner_profiler import load_latest_stage_profile
from app.storage.daily_paths import DATA_DIR, daily_path


@dataclass
class EngineHealth:

    scan_runtime_sec: float | None = None
    scanner_runtime: float | None = None
    worker_count: int | None = None
    polygon_calls: int | None = None
    polygon_failures: int = 0
    cache_hits: int | None = None
    cache_misses: int | None = None
    scanner_errors: int = 0
    exceptions: int = 0
    average_symbol_time: float | None = None
    average_symbol_runtime: float | None = None
    symbols_completed: int = 0
    symbols_failed: int = 0
    fresh_quotes: int = 0
    stale_quotes: int = 0
    delayed_quotes: int = 0
    health_score: int | None = None
    stage_profile: pd.DataFrame | None = None

    @property
    def cache_hit_rate(self):

        if self.cache_hits is None or self.cache_misses is None:

            return None

        total = self.cache_hits + self.cache_misses

        if total <= 0:

            return None

        return round(self.cache_hits / total * 100, 1)

    @property
    def fresh_quote_rate(self):

        total = self.fresh_quotes + self.stale_quotes + self.delayed_quotes

        if total <= 0:

            return None

        return round(self.fresh_quotes / total * 100, 1)


def calculate_health_score(health: EngineHealth):

    score = 100
    score -= (health.exceptions or 0) * 10
    score -= (health.polygon_failures or 0) * 5
    score -= (health.stale_quotes or 0) * 2
    score -= (health.delayed_quotes or 0)

    runtime = health.scan_runtime_sec or health.scanner_runtime

    if runtime and runtime > 40:

        score -= 5

    return max(int(score), 0)


def append_engine_health_history(report_date: str, metrics: dict):

    history_paths = [
        daily_path(report_date, "engine_health_history.csv"),
        DATA_DIR / "engine_health_history.csv",
    ]
    row = {
        "timestamp": metrics.get("timestamp"),
        "trading_day": report_date,
        "runtime": metrics.get("scan_runtime_sec"),
        "health_score": metrics.get("health_score"),
        "cache_hit": metrics.get("cache_hit_rate"),
        "workers": metrics.get("worker_count"),
        "requests": metrics.get("polygon_calls"),
        "exceptions": metrics.get("exceptions"),
        "symbols_completed": metrics.get("symbols_completed"),
        "symbols_failed": metrics.get("symbols_failed"),
        "average_symbol_runtime": metrics.get("average_symbol_runtime"),
    }

    for path in history_paths:

        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists() or path.stat().st_size == 0
        pd.DataFrame([row]).to_csv(
            path,
            mode="a",
            header=write_header,
            index=False
        )

    return row


def load_latest_engine_health(report_date: str):

    history = _read_csv(daily_path(report_date, "engine_health_history.csv"))

    if history.empty:

        history = _read_csv(DATA_DIR / "engine_health_history.csv")

    if history.empty:

        return {}

    if "trading_day" in history.columns:

        rows = history[history["trading_day"].astype(str).eq(report_date)]

    else:

        rows = pd.DataFrame()

    if rows.empty:

        rows = history

    return rows.tail(1).iloc[0].to_dict()


def build_engine_health(report_date: str):

    scanner = _read_csv(daily_path(report_date, "scanner_output_close.csv"))
    decisions = _read_csv(daily_path(report_date, "auto_paper_decisions.csv"))
    latest_metrics = load_latest_engine_health(report_date)
    health = EngineHealth()
    health.stage_profile = load_latest_stage_profile(report_date)

    if latest_metrics:

        health.scan_runtime_sec = latest_metrics.get("runtime")
        health.scanner_runtime = latest_metrics.get("runtime")
        health.worker_count = latest_metrics.get("workers")
        health.polygon_calls = latest_metrics.get("requests")
        health.exceptions = int(latest_metrics.get("exceptions") or 0)
        health.average_symbol_runtime = latest_metrics.get("average_symbol_runtime")
        health.average_symbol_time = latest_metrics.get("average_symbol_runtime")
        health.symbols_completed = int(latest_metrics.get("symbols_completed") or 0)
        health.symbols_failed = int(latest_metrics.get("symbols_failed") or 0)
        health.health_score = latest_metrics.get("health_score")

    if scanner is not None and not scanner.empty:

        symbol_column = "Symbol" if "Symbol" in scanner.columns else "symbol" if "symbol" in scanner.columns else None

        if symbol_column:

            health.symbols_completed = int(scanner[symbol_column].dropna().nunique())

        action = scanner.get("Action Status", pd.Series(dtype=object)).astype(str).str.upper()
        blocked = scanner.get("Blocked By", pd.Series(dtype=object)).astype(str).str.upper()
        health.scanner_errors = int(action.eq("ERROR").sum() + blocked.eq("SCANNER_ERROR").sum())

        freshness = scanner.get("Option Quote Freshness", pd.Series(dtype=object)).astype(str).str.upper()
        health.fresh_quotes = int(freshness.eq("LIVE_QUOTE").sum())
        health.stale_quotes = int(freshness.eq("STALE_QUOTE").sum())
        health.delayed_quotes = int(freshness.eq("DELAYED_QUOTE").sum())

    if decisions is not None and not decisions.empty:

        minutes_from_open = _safe_numeric(decisions.get("minutes_from_open", pd.Series(dtype=object))).dropna()
        minutes_to_close = _safe_numeric(decisions.get("minutes_to_close", pd.Series(dtype=object))).dropna()

        if not minutes_from_open.empty and not minutes_to_close.empty:

            span = minutes_from_open.max() - minutes_from_open.min()
            health.scanner_runtime = round(float(max(span, 0)), 2)

    if health.symbols_completed and health.scanner_runtime:

        health.average_symbol_time = round(health.scanner_runtime / health.symbols_completed, 2)

    if health.health_score is None:

        health.health_score = calculate_health_score(health)

    return health
