from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from html import escape
import json
import os
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from app.state.holding_policy import derive_holding_profile, holding_policy
from app.storage.daily_paths import DATA_DIR, daily_path, get_daily_dir


@dataclass(frozen=True)
class RegressionContext:
    trading_day: str
    snapshot_folder: Path
    baseline_folder: Path
    results_folder: Path
    current_strategy_version: str
    baseline_version: str
    readonly: bool = True


def _snapshot_folder(trading_day: str) -> Path:
    return get_daily_dir(trading_day) / "scanner_snapshots"


def _baseline_folder(trading_day: str) -> Path:
    return DATA_DIR / "regression" / trading_day / "baseline"


def _results_folder(trading_day: str) -> Path:
    return DATA_DIR / "regression" / trading_day


def _timestamp_text(scan_timestamp: Any) -> str:
    parsed = pd.to_datetime(scan_timestamp, errors="coerce")
    if pd.isna(parsed):
        parsed = pd.Timestamp.now()
    return parsed.strftime("%H%M%S")


def write_scan_snapshot(df: pd.DataFrame, trading_day: str, scan_id: str, scan_timestamp: Any) -> dict | None:
    """Persist one immutable raw scanner frame for historical regression."""
    if df is None or df.empty:
        return None

    folder = _snapshot_folder(trading_day)
    folder.mkdir(parents=True, exist_ok=True)
    date_prefix = str(trading_day).replace("-", "")
    stem = f"{date_prefix}_{_timestamp_text(scan_timestamp)}_{str(scan_id).replace(':', '_')}"
    parquet_path = folder / f"{stem}.parquet"
    csv_path = folder / f"{stem}.csv"

    if parquet_path.exists() or csv_path.exists():
        return {"path": str(parquet_path if parquet_path.exists() else csv_path), "created": False}

    snapshot = df.copy()
    snapshot["Regression Scan ID"] = scan_id
    snapshot["Regression Scan Timestamp"] = str(scan_timestamp)
    try:
        snapshot.to_parquet(parquet_path, index=False)
        path = parquet_path
        output_format = "parquet"
    except Exception:
        snapshot.to_csv(csv_path, index=False)
        path = csv_path
        output_format = "csv"

    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {
        "trading_day": trading_day,
        "readonly": True,
        "snapshots": [],
        "scanner_version": os.getenv("SCANNER_VERSION", "unknown"),
        "configuration": {
            "entry_engine": os.getenv("ENTRY_ENGINE", "v1"),
            "exit_engine": os.getenv("EXIT_ENGINE", "v1"),
            "strategy_version": os.getenv("SCANNER_VERSION", "unknown"),
        },
        "watchlist": [],
        "timezone": "America/New_York",
    }
    symbols = snapshot.get("Symbol", snapshot.get("symbol", pd.Series(dtype=object))).dropna().astype(str).tolist()
    manifest["watchlist"] = sorted(set(manifest.get("watchlist", [])) | set(symbols))
    manifest["snapshots"].append({
        "scan_id": scan_id,
        "scan_timestamp": str(scan_timestamp),
        "path": path.name,
        "rows": len(snapshot),
        "format": output_format,
        "strategy_version": os.getenv("SCANNER_VERSION", "unknown"),
    })
    manifest["scan_count"] = len(manifest["snapshots"])
    scan_times = pd.to_datetime(
        [item["scan_timestamp"] for item in manifest["snapshots"]],
        errors="coerce",
    ).dropna().sort_values()
    if len(scan_times) > 1:
        intervals = scan_times.to_series().diff().dropna().dt.total_seconds()
        manifest["scan_interval_seconds"] = int(intervals.median()) if not intervals.empty else None
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return {"path": str(path), "created": True, "rows": len(snapshot), "format": output_format}


def _read_frame(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)


def _first(row: dict, *names: str, default=None):
    for name in names:
        value = row.get(name)
        if value is None or str(value).strip().lower() in {"", "nan", "none"}:
            continue
        return value
    return default


def _number(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _snapshot_timestamp(frame: pd.DataFrame, path: Path) -> pd.Timestamp:
    for column in ("Regression Scan Timestamp", "Data Timestamp ET", "Current ET", "scan_timestamp"):
        if column in frame.columns:
            value = pd.to_datetime(frame[column].iloc[0], errors="coerce")
            if pd.notna(value):
                return value
    return pd.to_datetime(path.name.split("_")[0], format="%H%M%S", errors="coerce")


def _snapshot_frames(context: RegressionContext) -> list[tuple[pd.DataFrame, pd.Timestamp]]:
    from app.db.scanner_snapshot_repository import ScannerSnapshotRepository

    durable_snapshots = ScannerSnapshotRepository().load_day(context.trading_day)
    if durable_snapshots:
        grouped = {}
        for snapshot in durable_snapshots:
            scan_id = snapshot.get("scan_id")
            grouped.setdefault(scan_id, []).append(snapshot)
        return [
            (
                pd.DataFrame([
                    {
                        **(snapshot.get("decision_payload") or snapshot.get("payload") or {}),
                        "__Regression Market Snapshot": snapshot.get("market_payload") or {},
                    }
                    for snapshot in rows
                ]),
                pd.to_datetime(rows[0].get("scan_timestamp"), errors="coerce"),
            )
            for _, rows in sorted(grouped.items(), key=lambda item: item[1][0].get("scan_timestamp"))
        ]

    snapshots = sorted(list(context.snapshot_folder.glob("*.parquet")) + list(context.snapshot_folder.glob("*.csv")))
    frames = []
    for path in snapshots:
        frame = _read_frame(path)
        frames.append((frame, _snapshot_timestamp(frame, path)))
    return frames


def _default_evaluator(row: dict, _context: RegressionContext) -> dict:
    """Pure evaluator adapter; it never calls data, transport, runtime, or state services."""
    from app.decision.decision_engine import evaluate_candidate

    decision = evaluate_candidate(row)
    return {
        "action": decision.action,
        "holding_profile": decision.holding_profile,
        "setup": _first(row, "Entry", "entry", "setup_type"),
        "entry": _number(_first(row, "Candidate Entry Price", "entry_price", "Price")),
        "stop": _number(_first(row, "Candidate Stop Price", "stop_price", "Stop Loss")),
        "target": _number(_first(row, "Candidate Target Price", "target_price", "Take Profit")),
        "direction": _first(row, "Candidate Direction", "direction"),
    }


def _trade_identity(symbol, direction, setup):
    return "|".join([str(symbol), str(direction), str(setup)])


def reconstruct_trades(context: RegressionContext, evaluator: Callable | None = None) -> pd.DataFrame:
    evaluator = evaluator or _default_evaluator
    snapshots = _snapshot_frames(context)
    open_trades: dict[str, dict] = {}
    completed: list[dict] = []

    for frame, timestamp in snapshots:
        for _, series in frame.iterrows():
            row = series.to_dict()
            symbol = _first(row, "Symbol", "symbol")
            if not symbol:
                continue
            evaluation = evaluator(row, context) or {}
            direction = str(evaluation.get("direction") or "").upper()
            setup = evaluation.get("setup") or "UNKNOWN"
            price = _number(_first(row, "Price", "entry_price", "Candidate Entry Price"))
            existing = open_trades.get(symbol)
            if existing is None and str(evaluation.get("action") or "").upper() in {"ENTER", "ENTER_PAPER"}:
                entry = evaluation.get("entry")
                stop = evaluation.get("stop")
                target = evaluation.get("target")
                if None in {entry, stop, target} or entry == stop:
                    continue
                open_trades[symbol] = {
                    "trade_key": _trade_identity(symbol, direction, setup),
                    "symbol": symbol,
                    "direction": direction,
                    "setup": setup,
                    "entry_time": str(timestamp),
                    "entry_price": entry,
                    "stop_price": stop,
                    "target_price": target,
                    "holding_profile": evaluation.get("holding_profile") or derive_holding_profile(row).value,
                    "status": "OPEN",
                }
                continue
            if existing is None or price is None:
                continue

            is_short = direction in {"PUT", "SHORT"}
            hit_target = price <= existing["target_price"] if is_short else price >= existing["target_price"]
            hit_stop = price >= existing["stop_price"] if is_short else price <= existing["stop_price"]
            if not hit_target and not hit_stop:
                continue
            risk = abs(existing["entry_price"] - existing["stop_price"])
            existing["exit_time"] = str(timestamp)
            existing["exit_price"] = price
            existing["exit_reason"] = "TARGET_HIT" if hit_target else "STOP_HIT"
            existing["r_multiple"] = round(abs(existing["target_price"] - existing["entry_price"]) / risk, 2) if hit_target else -1.0
            existing["outcome"] = "WIN" if hit_target else "LOSS"
            existing["status"] = "CLOSED"
            completed.append(existing)
            del open_trades[symbol]

    return pd.DataFrame(completed + list(open_trades.values()))


def _metrics(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "total_r": 0.0, "average_r": 0.0, "profit_factor": None}
    r_values = pd.to_numeric(trades.get("r_multiple"), errors="coerce").dropna()
    wins = int((r_values > 0).sum())
    losses = int((r_values < 0).sum())
    gains = r_values[r_values > 0].sum()
    losses_r = abs(r_values[r_values < 0].sum())
    return {
        "trades": int(len(trades)),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(r_values) * 100, 1) if len(r_values) else 0.0,
        "total_r": round(float(r_values.sum()), 2),
        "average_r": round(float(r_values.mean()), 2) if len(r_values) else 0.0,
        "profit_factor": round(float(gains / losses_r), 2) if losses_r else None,
    }


def freeze_baseline(trading_day: str, trades: pd.DataFrame | None = None, baseline_version="frozen") -> Path | None:
    from app.db.scanner_snapshot_repository import RegressionBaselineRepository

    folder = _baseline_folder(trading_day)
    trades_path = folder / "baseline_trades.csv"
    durable_baseline = RegressionBaselineRepository().load(trading_day)
    if durable_baseline:
        folder.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(durable_baseline.get("payload") or []).to_csv(trades_path, index=False)
        return trades_path
    if trades_path.exists():
        return trades_path
    if trades is None:
        context = RegressionContext(
            trading_day=trading_day,
            snapshot_folder=_snapshot_folder(trading_day),
            baseline_folder=folder,
            results_folder=_results_folder(trading_day),
            current_strategy_version="baseline",
            baseline_version=baseline_version,
        )
        if context.snapshot_folder.exists():
            trades = reconstruct_trades(context)

        if trades is None or trades.empty:
            events_path = daily_path(trading_day, "paper_trade_events.csv")
            if not events_path.exists():
                return None
            events = pd.read_csv(events_path)
            if events.empty:
                return None
            events["event_type"] = events["event_type"].astype(str).str.upper()
            trades = events[events["event_type"].isin({"AUTO_EXIT", "MANUAL_CLOSE"})].copy()
            trades = trades.rename(columns={"event_time": "exit_time", "entry_price": "entry_price"})
            trades["trade_key"] = trades.get("trade_key")
            trades["setup"] = "ARCHIVED"
            trades["holding_profile"] = "INTRADAY"
            trades["outcome"] = trades.get("r_multiple", pd.Series(dtype=float)).map(lambda value: "WIN" if _number(value, 0) > 0 else "LOSS")
    folder.mkdir(parents=True, exist_ok=True)
    trades.to_csv(trades_path, index=False)
    (folder / "manifest.json").write_text(json.dumps({"trading_day": trading_day, "baseline_version": baseline_version, "frozen_at": datetime.now().isoformat(), "trades": len(trades)}, indent=2), encoding="utf-8")
    RegressionBaselineRepository().freeze(
        trading_day,
        baseline_version,
        trades.to_dict("records"),
    )
    return trades_path


def _compare_trades(baseline: pd.DataFrame, current: pd.DataFrame) -> dict:
    baseline = baseline.copy() if baseline is not None else pd.DataFrame()
    current = current.copy() if current is not None else pd.DataFrame()
    for frame in (baseline, current):
        if frame.empty:
            continue
        if "trade_key" not in frame:
            frame["trade_key"] = frame.apply(lambda row: _trade_identity(row.get("symbol"), row.get("direction"), row.get("setup")), axis=1)
        frame["r_multiple"] = pd.to_numeric(frame.get("r_multiple"), errors="coerce")
    baseline_index = baseline.set_index("trade_key", drop=False) if not baseline.empty else pd.DataFrame()
    current_index = current.set_index("trade_key", drop=False) if not current.empty else pd.DataFrame()
    baseline_keys = set(baseline_index.index) if not baseline.empty else set()
    current_keys = set(current_index.index) if not current.empty else set()
    changed = []
    for key in baseline_keys & current_keys:
        old_r = _number(baseline_index.loc[key].get("r_multiple"))
        new_r = _number(current_index.loc[key].get("r_multiple"))
        if old_r is not None and new_r is not None and old_r != new_r:
            changed.append({"trade_key": key, "old_r": old_r, "new_r": new_r, "delta_r": round(new_r - old_r, 2)})
    return {"new": sorted(current_keys - baseline_keys), "removed": sorted(baseline_keys - current_keys), "changed": changed}


def _report_html(summary: dict) -> str:
    baseline = summary["baseline"]
    current = summary["current"]
    comparison = summary["comparison"]
    verdict = summary["verdict"]
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Historical Regression</title><style>body{{font-family:system-ui;margin:2rem;max-width:900px}}table{{border-collapse:collapse;width:100%}}th,td{{padding:.45rem;border-bottom:1px solid #ddd;text-align:left}}.good{{color:#087f23}}.bad{{color:#b42318}}</style></head><body><h1>Historical Scanner Regression</h1><p>Trading day: <strong>{escape(summary['trading_day'])}</strong></p><h2>Baseline</h2><pre>{escape(json.dumps(baseline, indent=2))}</pre><h2>Current Code</h2><pre>{escape(json.dumps(current, indent=2))}</pre><h2>Comparison</h2><table><tr><th>New Trades</th><th>Removed Trades</th><th>Changed Trades</th><th>Net Gain</th></tr><tr><td>{len(comparison['new'])}</td><td>{len(comparison['removed'])}</td><td>{len(comparison['changed'])}</td><td>{summary['net_gain_r']:+.2f}R</td></tr></table><h2 class='{ 'good' if summary['verdict'].startswith('✅') else 'bad' }'>{escape(verdict)}</h2></body></html>"""


def run_historical_regression(trading_day: str, evaluator: Callable | None = None, current_strategy_version="current") -> dict:
    from app.db.scanner_snapshot_repository import RegressionBaselineRepository, RegressionRunRepository

    context = RegressionContext(
        trading_day=trading_day,
        snapshot_folder=_snapshot_folder(trading_day),
        baseline_folder=_baseline_folder(trading_day),
        results_folder=_results_folder(trading_day),
        current_strategy_version=current_strategy_version,
        baseline_version="frozen",
    )
    baseline_path = context.baseline_folder / "baseline_trades.csv"
    if not _snapshot_frames(context):
        raise FileNotFoundError(f"No scanner snapshots in Neon or local fallback for {trading_day}")
    durable_baseline = RegressionBaselineRepository().load(trading_day)
    if not baseline_path.exists() and not durable_baseline:
        raise FileNotFoundError(f"No frozen baseline for {trading_day}: {baseline_path}")
    current_trades = reconstruct_trades(context, evaluator)
    baseline_trades = pd.DataFrame(durable_baseline.get("payload") or []) if durable_baseline else pd.read_csv(baseline_path)
    comparison = _compare_trades(baseline_trades, current_trades)
    baseline_metrics = _metrics(baseline_trades)
    current_metrics = _metrics(current_trades)
    net_gain = round(current_metrics["total_r"] - baseline_metrics["total_r"], 2)
    summary = {
        "trading_day": trading_day,
        "context": {**asdict(context), "snapshot_folder": str(context.snapshot_folder), "baseline_folder": str(context.baseline_folder), "results_folder": str(context.results_folder)},
        "baseline": baseline_metrics,
        "current": current_metrics,
        "comparison": comparison,
        "net_gain_r": net_gain,
        "verdict": "✅ Strategy Improved" if net_gain > 0 else "⚠ Strategy Degraded" if net_gain < 0 else "➖ No Material Change",
    }
    context.results_folder.mkdir(parents=True, exist_ok=True)
    current_trades.to_csv(context.results_folder / "regression_trades.csv", index=False)
    (context.results_folder / "regression_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (context.results_folder / "regression_report.html").write_text(_report_html(summary), encoding="utf-8")
    summary["run_id"] = RegressionRunRepository().record(
        trading_day,
        current_strategy_version,
        summary,
        comparison,
        git_commit=os.getenv("GIT_COMMIT"),
    )
    return summary