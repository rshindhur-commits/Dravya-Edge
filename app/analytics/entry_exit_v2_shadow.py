from __future__ import annotations

import hashlib

import pandas as pd

from app.storage.daily_paths import daily_path


SHADOW_COLUMNS = [
    "shadow_id",
    "trading_day",
    "scan_id",
    "observed_at",
    "symbol",
    "v1_entry_type",
    "v1_entry_quality",
    "v2_suggested_entry",
    "v2_entry_efficiency_score",
    "v2_trend_age_bars",
    "v2_pullback_number",
    "v2_bars_since_breakout",
    "v2_ema9_extension_atr",
    "v2_vwap_extension_atr",
    "v2_entry_reason",
    "v2_shadow_trade_status",
    "v2_shadow_entry_time",
    "v2_shadow_entry_price",
    "v2_shadow_risk_reward",
    "v1_exit_signal",
    "v1_exit_reason",
    "v2_exit_signal",
    "v2_exit_phase",
    "v2_trend_health_score",
    "v2_trend_health_status",
    "v2_trend_failure_confirmed",
    "v2_mfe_r",
    "v2_rr_progress",
    "v1_v2_entry_disagrees",
    "engine_difference_reason",
    "v2_shadow_exit_signal",
    "v2_shadow_exit_phase",
    "v2_shadow_final_r",
]


def _value(row, name):

    value = row.get(name)

    if value is None:

        return None

    try:

        if pd.isna(value):

            return None

    except Exception:

        pass

    return value


def build_shadow_rows(rows, trading_day, scan_id, observed_at):

    records = []

    if rows is None:

        rows = []

    observed_at_value = (
        observed_at.isoformat()
        if hasattr(observed_at, "isoformat")
        else str(observed_at)
    )

    for row in rows:

        symbol = _value(row, "Symbol")

        if not symbol:

            continue

        source = f"{trading_day}|{scan_id}|{symbol}"
        v1_entry = str(_value(row, "Entry") or "NO_ENTRY").upper()
        v1_would_enter = v1_entry not in {"NO_ENTRY", "NO_SETUP", "ACTIVE_TRADE"}
        v2_would_enter = bool(_value(row, "V2 Entry Suggested"))
        entry_disagrees = v1_would_enter != v2_would_enter
        records.append({
            "shadow_id": hashlib.sha256(source.encode()).hexdigest()[:24],
            "trading_day": trading_day,
            "scan_id": scan_id,
            "observed_at": observed_at_value,
            "symbol": symbol,
            "v1_entry_type": v1_entry,
            "v1_entry_quality": _value(row, "Entry Quality"),
            "v2_suggested_entry": _value(row, "V2 Entry Suggested"),
            "v2_entry_efficiency_score": _value(row, "V2 Entry Efficiency Score"),
            "v2_trend_age_bars": _value(row, "V2 Trend Age Bars"),
            "v2_pullback_number": _value(row, "V2 Pullback Number"),
            "v2_bars_since_breakout": _value(row, "V2 Bars Since Breakout"),
            "v2_ema9_extension_atr": _value(row, "V2 EMA9 Extension ATR"),
            "v2_vwap_extension_atr": _value(row, "V2 VWAP Extension ATR"),
            "v2_entry_reason": _value(row, "V2 Entry Reason"),
            "v2_shadow_trade_status": _value(row, "V2 Shadow Trade Status"),
            "v2_shadow_entry_time": _value(row, "V2 Shadow Entry Time"),
            "v2_shadow_entry_price": _value(row, "V2 Shadow Entry Price"),
            "v2_shadow_risk_reward": _value(row, "V2 Shadow Risk Reward"),
            "v1_exit_signal": _value(row, "Live Exit Signal"),
            "v1_exit_reason": _value(row, "Live Exit Reason"),
            "v2_exit_signal": _value(row, "V2 Exit Signal"),
            "v2_exit_phase": _value(row, "V2 Exit Phase"),
            "v2_trend_health_score": _value(row, "V2 Trend Health Score"),
            "v2_trend_health_status": _value(row, "V2 Trend Health Status"),
            "v2_trend_failure_confirmed": _value(row, "V2 Trend Failure Confirmed"),
            "v2_mfe_r": _value(row, "V2 MFE R"),
            "v2_rr_progress": _value(row, "V2 RR Progress"),
            "v2_shadow_exit_signal": _value(row, "V2 Shadow Exit Signal"),
            "v2_shadow_exit_phase": _value(row, "V2 Shadow Exit Phase"),
            "v2_shadow_final_r": _value(row, "V2 Shadow Final R"),
            "v1_v2_entry_disagrees": entry_disagrees,
            "engine_difference_reason": (
                _value(row, "V2 Entry Reason")
                if entry_disagrees
                else None
            ),
        })

    return pd.DataFrame(records, columns=SHADOW_COLUMNS)


def write_shadow_comparison(rows, trading_day, scan_id, observed_at):

    comparison = build_shadow_rows(
        rows,
        trading_day,
        scan_id,
        observed_at
    )

    if comparison.empty:

        return None

    path = daily_path(trading_day, "entry_exit_v2_shadow.csv")
    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )
    existing = (
        pd.read_csv(path)
        if path.exists() and path.stat().st_size
        else pd.DataFrame()
    )
    combined = pd.concat(
        [existing, comparison],
        ignore_index=True,
        sort=False
    )
    combined.drop_duplicates(
        subset=["shadow_id"],
        keep="last"
    ).to_csv(
        path,
        index=False
    )
    differences = build_engine_differences(comparison)
    differences_path = daily_path(trading_day, "engine_differences.csv")
    if not differences.empty:
        existing_differences = (
            pd.read_csv(differences_path)
            if differences_path.exists() and differences_path.stat().st_size
            else pd.DataFrame()
        )
        pd.concat(
            [existing_differences, differences],
            ignore_index=True,
            sort=False
        ).drop_duplicates(
            subset=["difference_id"],
            keep="last"
        ).to_csv(differences_path, index=False)

    return {
        "path": str(path),
        "rows": len(comparison),
        "differences_path": str(differences_path),
    }


def build_engine_differences(comparison):

    if comparison is None or comparison.empty:

        return pd.DataFrame()

    records = []

    for _, row in comparison.iterrows():

        if bool(row.get("v1_v2_entry_disagrees")):

            source = f"{row.get('shadow_id')}|ENTRY"
            records.append({
                "difference_id": hashlib.sha256(source.encode()).hexdigest()[:24],
                "trading_day": row.get("trading_day"),
                "scan_id": row.get("scan_id"),
                "symbol": row.get("symbol"),
                "stage": "ENTRY",
                "v1_decision": row.get("v1_entry_type"),
                "v2_decision": "ENTER" if bool(row.get("v2_suggested_entry")) else "WAIT",
                "reason": row.get("engine_difference_reason"),
                "confidence": row.get("v2_entry_efficiency_score"),
            })

        if bool(row.get("v1_exit_signal")) != bool(row.get("v2_exit_signal")):

            source = f"{row.get('shadow_id')}|EXIT"
            records.append({
                "difference_id": hashlib.sha256(source.encode()).hexdigest()[:24],
                "trading_day": row.get("trading_day"),
                "scan_id": row.get("scan_id"),
                "symbol": row.get("symbol"),
                "stage": "EXIT",
                "v1_decision": "EXIT" if bool(row.get("v1_exit_signal")) else "HOLD",
                "v2_decision": "EXIT" if bool(row.get("v2_exit_signal")) else "HOLD",
                "reason": row.get("v2_exit_phase"),
                "confidence": row.get("v2_trend_health_score"),
            })

    return pd.DataFrame(records)


def summarize_shadow_comparison(comparison):

    if comparison is None or comparison.empty:

        return {
            "Shadow rows": 0,
            "V2 entry suggestions": 0,
            "V1 exit signals": 0,
            "V2 exit signals": 0,
            "Exit disagreements": 0,
            "Avg V2 entry efficiency": None,
            "Avg V2 MFE R": None,
        }, pd.DataFrame()

    def bool_column(name):
        return comparison.get(
            name,
            pd.Series(False, index=comparison.index)
        ).astype(str).str.lower().isin({"true", "1", "yes"})

    v1_exit = bool_column("v1_exit_signal")
    v2_exit = bool_column("v2_exit_signal")
    v2_entry = bool_column("v2_suggested_entry")
    entry_efficiency = pd.to_numeric(
        comparison.get("v2_entry_efficiency_score"),
        errors="coerce"
    )
    mfe_r = pd.to_numeric(
        comparison.get("v2_mfe_r"),
        errors="coerce"
    )
    phase_counts = comparison.get(
        "v2_exit_phase",
        pd.Series("NO_DATA", index=comparison.index)
    ).fillna("NO_DATA").value_counts().reset_index()
    phase_counts.columns = ["V2 Exit Phase", "Count"]
    return {
        "Shadow rows": int(len(comparison)),
        "V2 entry suggestions": int(v2_entry.sum()),
        "V1 exit signals": int(v1_exit.sum()),
        "V2 exit signals": int(v2_exit.sum()),
        "Exit disagreements": int((v1_exit != v2_exit).sum()),
        "Avg V2 entry efficiency": (
            round(float(entry_efficiency.mean()), 2)
            if entry_efficiency.notna().any()
            else None
        ),
        "Avg V2 MFE R": (
            round(float(mfe_r.mean()), 2)
            if mfe_r.notna().any()
            else None
        ),
    }, phase_counts