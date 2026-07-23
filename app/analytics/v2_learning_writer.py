from __future__ import annotations

import pandas as pd

from app.storage.daily_paths import daily_path


def append_learning_record(record):
    if not record:
        return None
    trading_day = record.get("trading_day")
    csv_path = daily_path(trading_day, "v2_learning_dataset.csv")
    parquet_path = daily_path(trading_day, "v2_learning_dataset.parquet")
    existing = pd.read_csv(csv_path) if csv_path.exists() and csv_path.stat().st_size else pd.DataFrame()
    dataset = pd.concat([existing, pd.DataFrame([record])], ignore_index=True, sort=False)
    dataset.drop_duplicates(subset=["learning_id"], keep="last").to_csv(csv_path, index=False)
    try:
        dataset.drop_duplicates(subset=["learning_id"], keep="last").to_parquet(parquet_path, index=False)
    except Exception:
        pass
    return {"path": str(csv_path), "rows": 1}


def summarize_learning_dataset(dataset):
    if dataset is None or dataset.empty:
        return {
            "Completed learning records": 0,
            "Avg Trend Age": None,
            "Avg Entry Efficiency": None,
            "Avg Exit Trend Health": None,
            "Avg MFE R": None,
            "Avg MAE R": None,
            "Avg Trend Capture %": None,
            "Avg TES": None,
        }
    def average(column):
        values = pd.to_numeric(dataset.get(column), errors="coerce")
        return round(float(values.mean()), 2) if values.notna().any() else None
    return {
        "Completed learning records": int(len(dataset)),
        "Avg Trend Age": average("trend_age"),
        "Avg Entry Efficiency": average("entry_efficiency_score"),
        "Avg Exit Trend Health": average("trend_health_at_exit"),
        "Avg MFE R": average("max_mfe_r"),
        "Avg MAE R": average("max_mae_r"),
        "Avg Trend Capture %": average("trend_capture_pct"),
        "Avg TES": average("tes"),
    }