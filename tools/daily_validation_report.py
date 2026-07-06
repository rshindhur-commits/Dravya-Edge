import argparse
import html
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:

    sys.path.insert(0, str(ROOT_DIR))

from app.analytics.expectancy_report import build_grouped_expectancy_reports
from app.gates.entry_gate import price_geometry_error
from app.storage.daily_paths import (
    daily_path,
    get_daily_dir,
    live_path
)
from app.storage.session_manager import (
    finalize_daily_report,
    get_or_create_session_manifest,
    get_session_id
)

DEFAULT_INPUTS = {
    "scanner_output": ROOT_DIR / "scanner_output.xlsx",
    "telemetry": ROOT_DIR / "telemetry" / "trade_telemetry.csv",
    "paper_trade_state": ROOT_DIR / "app" / "state" / "paper_trade_state.json",
    "trade_state": ROOT_DIR / "app" / "state" / "trade_state.json",
    "auto_paper_decision_log": ROOT_DIR / "app" / "state" / "auto_paper_decision_log.json",
    "suggested_trade_state": ROOT_DIR / "app" / "state" / "suggested_trade_state.json",
    "paper_trade_events": ROOT_DIR / "paper_trade_events.csv",
}


DAILY_INPUT_NAMES = {
    "scanner_output": "scanner_output_close.xlsx",
    "telemetry": "trade_telemetry.csv",
    "paper_trade_state": "paper_trade_state.json",
    "trade_state": "trade_state.json",
    "auto_paper_decision_log": "auto_paper_decision_log.json",
    "suggested_trade_state": "suggested_trade_state.json",
    "paper_trade_events": "paper_trade_events.csv",
}


LIVE_INPUTS = {
    "scanner_output": live_path("scanner_output_latest.xlsx"),
    "paper_trade_state": live_path("paper_trade_state.json"),
    "trade_state": live_path("trade_state.json"),
    "auto_paper_decision_log": live_path("auto_paper_decision_log.json"),
    "suggested_trade_state": live_path("suggested_trade_state.json"),
}


def resolve_input_paths(report_date):

    resolved = {}

    for label, filename in DAILY_INPUT_NAMES.items():

        daily_file = daily_path(report_date, filename)

        if daily_file.exists():

            resolved[label] = daily_file
            continue

        live_file = LIVE_INPUTS.get(label)

        if live_file and live_file.exists():

            resolved[label] = live_file
            continue

        resolved[label] = DEFAULT_INPUTS[label]

    return resolved


def load_auto_paper_decisions(report_date, fallback_path):

    daily_csv_path = daily_path(report_date, "auto_paper_decisions.csv")
    daily_df = _read_csv(daily_csv_path)

    if not daily_df.empty:

        return (
            daily_df.to_dict(orient="records"),
            "full daily CSV",
            daily_csv_path,
            None,
        )

    decisions = _read_json(fallback_path, [])

    return (
        decisions,
        "recent state JSON only",
        fallback_path,
        "Auto-paper decision history is using the capped dashboard JSON. This may not include regular market hours.",
    )


def _today():

    return datetime.now().strftime("%Y-%m-%d")


def _read_json(path, default):

    try:

        if not path.exists() or path.stat().st_size == 0:

            return default

        return json.loads(path.read_text(encoding="utf-8"))

    except Exception as exc:

        print(f"[REPORT WARNING] Could not read {path}: {exc}")
        return default


def _read_csv(path):

    try:

        if not path.exists() or path.stat().st_size == 0:

            return pd.DataFrame()

        return pd.read_csv(path)

    except pd.errors.EmptyDataError:

        return pd.DataFrame()

    except Exception as exc:

        print(f"[REPORT WARNING] Could not read {path}: {exc}")
        return pd.DataFrame()


def _read_excel(path):

    try:

        if not path.exists() or path.stat().st_size == 0:

            return pd.DataFrame()

        return pd.read_excel(path)

    except Exception as exc:

        print(f"[REPORT WARNING] Could not read {path}: {exc}")
        return pd.DataFrame()


def _read_candidate_snapshots(report_date):

    parquet_path = daily_path(report_date, "candidate_snapshots.parquet")
    csv_path = daily_path(report_date, "candidate_snapshots.csv")

    try:

        if parquet_path.exists() and parquet_path.stat().st_size > 0:

            return pd.read_parquet(parquet_path)

    except Exception as exc:

        print(f"[REPORT WARNING] Could not read {parquet_path}: {exc}")

    return _read_csv(csv_path)


def _json_record_count(data):

    if isinstance(data, dict):

        return len(data)

    if isinstance(data, list):

        return len(data)

    return 0


def build_data_health(
    scanner_df,
    telemetry_df,
    paper_trade_state,
    trade_state,
    decision_log,
    decision_source,
    decision_source_path,
    suggested_trade_state,
    candidate_df,
    paper_events_df
):

    opened_count = 0

    if decision_log:

        decision_df = pd.DataFrame(decision_log)

        if "decision" in decision_df.columns:

            opened_count = int(
                decision_df["decision"]
                .astype(str)
                .str.upper()
                .eq("OPENED")
                .sum()
            )

    health = {
        "scanner_output rows": len(scanner_df),
        "candidate_snapshot rows": len(candidate_df),
        "trade_telemetry rows": len(telemetry_df),
        "paper_trade_state records": _json_record_count(paper_trade_state),
        "trade_state records": _json_record_count(trade_state),
        "paper_trade_events rows": len(paper_events_df),
        "auto-paper decision source": decision_source,
        "auto-paper decision source path": decision_source_path,
        "auto_paper_decision_log rows": _json_record_count(decision_log),
        "decision OPENED count": opened_count,
        "suggested_trade_state records": _json_record_count(suggested_trade_state),
    }
    warnings = []

    if (
        health["paper_trade_state records"] == 0
        and health["paper_trade_events rows"] == 0
        and health["decision OPENED count"] == 0
    ):

        warnings.append(
            "No paper trades found. Report is based on blocked/skipped candidates only."
        )

    if health["auto_paper_decision_log rows"] > 0 and health["decision OPENED count"] == 0:

        warnings.append(
            "Auto-paper decisions exist, but none are OPENED. Suggested trades may not be actual paper trades."
        )

    if health["scanner_output rows"] == 0 and health["candidate_snapshot rows"] == 0:

        warnings.append(
            "No scanner output or candidate snapshots found for this report date."
        )

    return health, warnings


def paper_state_to_dataframe(paper_trade_state):

    if not isinstance(paper_trade_state, dict) or not paper_trade_state:

        return pd.DataFrame()

    return pd.DataFrame(list(paper_trade_state.values()))


def choose_trade_score_source(paper_events_df, telemetry_df, paper_trade_state):

    if not paper_events_df.empty:
        return paper_events_df

    telemetry_trades = _trade_rows(telemetry_df)

    if not telemetry_trades.empty:

        return telemetry_trades

    return paper_state_to_dataframe(paper_trade_state)


def _safe_numeric(series):

    return pd.to_numeric(series, errors="coerce")


def _first_existing(df, columns):

    for column in columns:

        if column in df.columns:

            return column

    return None


def _html_table(df, empty_message="No data available"):

    if df is None or df.empty:

        return f"<p>{html.escape(empty_message)}</p>"

    return df.to_html(index=False, escape=True, border=0)


def _is_blank(value):

    if value is None:

        return True

    try:

        if pd.isna(value):

            return True

    except Exception:

        pass

    return str(value).strip().lower() in {"", "nan", "none"}


def _option_type_from_ticker(value):

    text = str(value or "").upper()

    if not text:

        return None

    if "C" in text.split("O:")[-1]:

        marker_index = max(text.rfind("C"), text.rfind("P"))

        if marker_index >= 0:

            return text[marker_index]

    if text.endswith("CALL"):

        return "C"

    if text.endswith("PUT"):

        return "P"

    marker_index = max(text.rfind("C"), text.rfind("P"))

    if marker_index >= 0:

        return text[marker_index]

    return None


def _direction_option_mismatch(row):

    direction = str(
        row.get("Candidate Direction")
        or row.get("direction")
        or ""
    ).upper()
    option_type = _option_type_from_ticker(
        row.get("Option Ticker")
        or row.get("option_ticker")
    )

    if direction == "CALL":

        return option_type == "P"

    if direction == "PUT":

        return option_type == "C"

    return False



def _count_invalid_geometry(df):

    if df is None or df.empty:

        return 0

    try:

        return int(
            df.apply(price_geometry_error, axis=1)
            .notna()
            .sum()
        )

    except Exception:

        return 0



def _count_direction_option_mismatch(df):

    if df is None or df.empty:

        return 0

    try:

        return int(
            df.apply(_direction_option_mismatch, axis=1)
            .sum()
        )

    except Exception:

        return 0



def _count_high_setup_threshold_blocks(df):

    if df is None or df.empty:

        return 0

    setup_column = _first_existing(
        df,
        ["Setup %", "setup_percent", "current_setup_percent"]
    )
    rr_column = _first_existing(
        df,
        ["Candidate RR", "Risk Reward", "RR", "rr", "current_rr"]
    )
    reason_columns = [
        column for column in [
            "Blocked By",
            "blocked_by",
            "Action Reason",
            "action_reason",
            "Realtime Block Reason",
            "realtime_block_reason"
        ]
        if column in df.columns
    ]

    if not setup_column or not rr_column or not reason_columns:

        return 0

    setup = _safe_numeric(df[setup_column])
    rr = _safe_numeric(df[rr_column])
    reasons = df[reason_columns].fillna("").astype(str).agg(" ".join, axis=1)

    return int(
        (
            (setup >= 90)
            & (rr >= 2.0)
            & reasons.str.contains("SETUP_BELOW_THRESHOLD", case=False, regex=False)
        ).sum()
    )


def _count_review_missing_realtime_reason(df):

    if df is None or df.empty:

        return 0

    status_column = _first_existing(df, ["Action Status", "action_status"])
    ready_column = _first_existing(df, ["Realtime Ready", "realtime_ready"])
    reason_column = _first_existing(
        df,
        ["Realtime Block Reason", "realtime_block_reason", "Blocked By", "blocked_by"]
    )

    if not status_column or not ready_column:

        return 0

    status = df[status_column].astype(str).str.upper()
    ready = df[ready_column].astype(str).str.lower().isin({"true", "1", "yes"})

    if reason_column:

        missing_reason = df[reason_column].map(_is_blank)

    else:

        missing_reason = pd.Series([True] * len(df), index=df.index)

    return int(
        (
            status.eq("REVIEW_TV_CHART")
            & ~ready
            & missing_reason
        ).sum()
    )


def build_data_quality_checks(
    scanner_df,
    candidate_df,
    suggested_trade_state,
    decision_log,
    paper_events_df,
    paper_trade_state
):

    suggested_df = (
        pd.DataFrame(list(suggested_trade_state.values()))
        if isinstance(suggested_trade_state, dict) and suggested_trade_state
        else pd.DataFrame()
    )
    decision_df = pd.DataFrame(decision_log) if decision_log else pd.DataFrame()
    source_frames = [
        frame for frame in [scanner_df, candidate_df, suggested_df]
        if frame is not None and not frame.empty
    ]
    combined_df = (
        pd.concat(source_frames, ignore_index=True, sort=False)
        if source_frames
        else pd.DataFrame()
    )
    opened_decisions = 0

    if not decision_df.empty and "decision" in decision_df.columns:

        opened_decisions = int(
            decision_df["decision"]
            .astype(str)
            .str.upper()
            .eq("OPENED")
            .sum()
        )
    suggested_not_entered = 0

    if not suggested_df.empty and "status" in suggested_df.columns:

        suggested_not_entered = int(
            suggested_df["status"]
            .astype(str)
            .str.upper()
            .isin(["EXPIRED_NOT_ENTERED", "DO_NOT_CHASE", "WATCH_WEAKENING"])
            .sum()
        )
    actual_opened = max(
        opened_decisions,
        len(paper_events_df) if not paper_events_df.empty else 0,
        _json_record_count(paper_trade_state)
    )
    checks = {
        "Invalid price geometry count": _count_invalid_geometry(combined_df),
        "Direction/option mismatch count": _count_direction_option_mismatch(combined_df),
        "High setup but blocked by setup threshold count": _count_high_setup_threshold_blocks(combined_df),
        "Review rows realtime_ready=false and missing reason": _count_review_missing_realtime_reason(combined_df),
        "Actual opened trades count": actual_opened,
        "Suggested but not entered count": suggested_not_entered,
    }

    return pd.DataFrame(
        [
            {"Check": key, "Count": value}
            for key, value in checks.items()
        ]
    )


def _metric_row(label, value):

    return (
        "<tr>"
        f"<th>{html.escape(str(label))}</th>"
        f"<td>{html.escape(str(value))}</td>"
        "</tr>"
    )


def _normalize_telemetry(df):

    normalized_df = df.copy()
    rename_map = {
        "setup_category": "setup_type",
        "entry": "setup_type",
        "final_signal": "direction",
        "Candidate Direction": "direction",
        "Market Regime": "market_regime",
        "Replay Outcome": "replay_outcome",
        "Replay outcome": "replay_outcome",
    }

    for old_name, new_name in rename_map.items():

        if old_name in normalized_df.columns and new_name not in normalized_df.columns:

            normalized_df[new_name] = normalized_df[old_name]

    return normalized_df


def _trade_rows(telemetry_df):

    if telemetry_df.empty:

        return telemetry_df

    normalized_df = _normalize_telemetry(telemetry_df)

    if "run_type" in normalized_df.columns:

        paper_df = normalized_df[
            normalized_df["run_type"].astype(str).str.lower().eq("paper_trade")
        ].copy()

        if not paper_df.empty:

            return paper_df

    if "r_multiple" in normalized_df.columns:

        return normalized_df[normalized_df["r_multiple"].notna()].copy()

    return pd.DataFrame()


def build_trade_scorecard(telemetry_df):

    if not telemetry_df.empty and "event_type" in telemetry_df.columns:

        trades_df = telemetry_df.copy()

    else:

        trades_df = _trade_rows(telemetry_df)

    if trades_df.empty or "r_multiple" not in trades_df.columns:

        return {}, trades_df

    trades_df = trades_df.copy()
    trades_df["r_multiple"] = _safe_numeric(trades_df["r_multiple"])
    trades_df = trades_df[trades_df["r_multiple"].notna()].copy()

    if trades_df.empty:

        return {}, trades_df

    wins = trades_df[trades_df["r_multiple"] > 0]
    losses = trades_df[trades_df["r_multiple"] < 0]
    symbol_column = _first_existing(trades_df, ["symbol", "Symbol"])
    direction_column = _first_existing(trades_df, ["direction", "final_signal"])
    best_trade = trades_df.sort_values("r_multiple", ascending=False).iloc[0]
    worst_trade = trades_df.sort_values("r_multiple", ascending=True).iloc[0]

    hold_time = "N/A"

    if "opened_at" in trades_df.columns and "closed_at" in trades_df.columns:

        opened = pd.to_datetime(trades_df["opened_at"], errors="coerce")
        closed = pd.to_datetime(trades_df["closed_at"], errors="coerce")
        minutes = (closed - opened).dt.total_seconds().dropna() / 60

        if not minutes.empty:

            hold_time = round(minutes.mean(), 2)

    calls_vs_puts = "N/A"

    if direction_column:

        calls_vs_puts = trades_df[direction_column].astype(str).str.upper().value_counts().to_dict()

    metrics = {
        "Total paper trades": len(trades_df),
        "Wins": len(wins),
        "Losses": len(losses),
        "Win rate": f"{round((len(wins) / len(trades_df)) * 100, 2)}%",
        "Total R": round(trades_df["r_multiple"].sum(), 2),
        "Average R": round(trades_df["r_multiple"].mean(), 2),
        "Best trade": _format_trade(best_trade, symbol_column),
        "Worst trade": _format_trade(worst_trade, symbol_column),
        "Average hold time minutes": hold_time,
        "Calls vs puts": calls_vs_puts,
    }

    return metrics, trades_df


def _format_trade(row, symbol_column):

    symbol = row.get(symbol_column) if symbol_column else row.get("symbol", "UNKNOWN")

    return f"{symbol} ({row.get('r_multiple')}R)"


def build_gate_scorecard(decision_log):

    if not decision_log:

        return {}, pd.DataFrame()

    decisions_df = pd.DataFrame(decision_log)

    if decisions_df.empty or "decision" not in decisions_df.columns:

        return {}, decisions_df

    counts = decisions_df["decision"].fillna("UNKNOWN").astype(str).str.upper().value_counts()
    reason_column = _first_existing(
        decisions_df,
        ["reason", "blocked_by", "action_reason", "option_rejection_reason", "realtime_block_reason"]
    )
    top_reasons = pd.DataFrame()

    if reason_column:

        blocked_like = decisions_df[
            decisions_df["decision"].astype(str).str.upper().isin(["BLOCKED", "SKIPPED"])
        ].copy()
        top_reasons = (
            blocked_like[reason_column]
            .fillna("UNKNOWN")
            .astype(str)
            .value_counts()
            .head(12)
            .rename_axis("reason")
            .reset_index(name="count")
        )

    metrics = {
        "Decisions": len(decisions_df),
        "OPENED": int(counts.get("OPENED", 0)),
        "BLOCKED": int(counts.get("BLOCKED", 0)),
        "SKIPPED": int(counts.get("SKIPPED", 0)),
    }

    return metrics, top_reasons


def _decision_dataframe(decision_log):

    return pd.DataFrame(decision_log) if decision_log else pd.DataFrame()


def _truthy_series(series):

    return series.astype(str).str.strip().str.lower().isin({"true", "1", "yes"})


def filter_decisions_by_bucket(decision_log, bucket):

    decisions_df = _decision_dataframe(decision_log)

    if decisions_df.empty:

        return []

    if bucket == "AUTO_ENTRY_WINDOW" and "is_auto_entry_window" in decisions_df.columns:

        return decisions_df[_truthy_series(decisions_df["is_auto_entry_window"])].to_dict(orient="records")

    if bucket == "AFTER_CLOSE" and "is_after_close" in decisions_df.columns:

        return decisions_df[_truthy_series(decisions_df["is_after_close"])].to_dict(orient="records")

    bucket_column = _first_existing(decisions_df, ["decision_time_bucket", "market_session"])

    if not bucket_column:

        return []

    return decisions_df[
        decisions_df[bucket_column].astype(str).str.upper().eq(bucket)
    ].to_dict(orient="records")


def build_quote_freshness_table(decision_log):

    decisions_df = _decision_dataframe(decision_log)
    freshness_column = _first_existing(
        decisions_df,
        ["option_quote_freshness", "Option Quote Freshness"]
    )

    if decisions_df.empty or not freshness_column:

        return pd.DataFrame()

    freshness = (
        decisions_df[freshness_column]
        .fillna("missing")
        .astype(str)
        .str.strip()
        .replace({"": "missing", "nan": "missing", "None": "missing"})
        .str.upper()
    )

    return (
        freshness.value_counts()
        .rename_axis("quote_freshness")
        .reset_index(name="count")
    )


def _value_counts_with_pct(series, label):

    counts = series.fillna("missing").astype(str).str.strip().replace({"": "missing"}).value_counts()
    total = int(counts.sum())

    if total == 0:

        return pd.DataFrame()

    return pd.DataFrame(
        [
            {
                label: key,
                "count": int(value),
                "pct": round((int(value) / total) * 100, 2),
            }
            for key, value in counts.items()
        ]
    )


def build_signal_lifecycle_analysis(events_df, transitions_df, suggested_trade_state):

    quote_freshness_df = pd.DataFrame()
    review_metrics = {
        "avg REVIEW_TV_CHART minutes": "N/A",
        "median REVIEW_TV_CHART minutes": "N/A",
        "max REVIEW_TV_CHART minutes": "N/A",
        "count stuck > 15 min": 0,
        "count stuck > 30 min": 0,
    }
    expiry_metrics = {
        "expired in < 5 min": 0,
        "expired in 5-15 min": 0,
        "expired in 15-30 min": 0,
        "expired after 30+ min": 0,
        "expired while LIVE_QUOTE": 0,
        "expired while RR >= 1.8": 0,
        "expired while setup >= 70": 0,
    }

    if not events_df.empty:

        window_df = events_df.copy()

        if "is_auto_entry_window" in window_df.columns:

            window_df = window_df[_truthy_series(window_df["is_auto_entry_window"])].copy()

        elif "market_session" in window_df.columns:

            window_df = window_df[window_df["market_session"].astype(str).str.upper().eq("AUTO_ENTRY_WINDOW")].copy()

        freshness_column = _first_existing(
            window_df,
            ["option_quote_freshness", "Option Quote Freshness"]
        )

        if freshness_column:

            quote_freshness_df = _value_counts_with_pct(
                window_df[freshness_column],
                "quote_freshness"
            )

    if not transitions_df.empty:

        previous_state = transitions_df.get("previous_state", pd.Series(dtype=object)).astype(str)
        durations = _safe_numeric(
            transitions_df.loc[
                previous_state.str.startswith("REVIEW_TV_CHART", na=False),
                "duration_minutes"
            ]
        ).dropna()

        if not durations.empty:

            review_metrics = {
                "avg REVIEW_TV_CHART minutes": round(durations.mean(), 2),
                "median REVIEW_TV_CHART minutes": round(durations.median(), 2),
                "max REVIEW_TV_CHART minutes": round(durations.max(), 2),
                "count stuck > 15 min": int((durations > 15).sum()),
                "count stuck > 30 min": int((durations > 30).sum()),
            }

    suggestions_df = (
        pd.DataFrame(list(suggested_trade_state.values()))
        if isinstance(suggested_trade_state, dict) and suggested_trade_state
        else pd.DataFrame()
    )

    if not suggestions_df.empty and "status" in suggestions_df.columns:

        expired_df = suggestions_df[
            suggestions_df["status"].astype(str).str.upper().eq("EXPIRED_NOT_ENTERED")
        ].copy()

        if not expired_df.empty:

            lifetime = _safe_numeric(expired_df.get("lifetime_minutes", pd.Series(dtype=object)))
            setup = _safe_numeric(expired_df.get("current_setup_percent", pd.Series(dtype=object)))
            rr = _safe_numeric(expired_df.get("current_rr", pd.Series(dtype=object)))
            state_before = expired_df.get("last_state_before_expiry", pd.Series(dtype=object)).astype(str)
            quote_freshness = expired_df.get("option_quote_freshness", pd.Series(dtype=object)).astype(str)
            live_before_expiry = (
                state_before.str.contains("LIVE_QUOTE", case=False, regex=False)
                | quote_freshness.str.upper().eq("LIVE_QUOTE")
            )
            expiry_metrics = {
                "expired in < 5 min": int((lifetime < 5).sum()),
                "expired in 5-15 min": int(((lifetime >= 5) & (lifetime < 15)).sum()),
                "expired in 15-30 min": int(((lifetime >= 15) & (lifetime < 30)).sum()),
                "expired after 30+ min": int((lifetime >= 30).sum()),
                "expired while LIVE_QUOTE": int(live_before_expiry.sum()),
                "expired while RR >= 1.8": int((rr >= 1.8).sum()),
                "expired while setup >= 70": int((setup >= 70).sum()),
            }

    return quote_freshness_df, review_metrics, expiry_metrics


def build_replay_scorecard(telemetry_df):

    if telemetry_df.empty:

        return pd.DataFrame()

    normalized_df = _normalize_telemetry(telemetry_df)
    required = {"setup_type", "replay_outcome"}

    if not required.issubset(set(normalized_df.columns)):

        return pd.DataFrame()

    replay_df = normalized_df[normalized_df["replay_outcome"].notna()].copy()

    if replay_df.empty:

        return pd.DataFrame()

    for column in ["mfe", "mae"]:

        if column in replay_df.columns:

            replay_df[column] = _safe_numeric(replay_df[column])

    grouped = replay_df.groupby("setup_type", dropna=False)
    rows = []

    for setup_type, group in grouped:

        target_first = group["replay_outcome"].astype(str).eq("TARGET_HIT").sum()
        stop_first = group["replay_outcome"].astype(str).eq("STOP_HIT").sum()
        avg_mfe = round(group["mfe"].mean(), 2) if "mfe" in group.columns else "N/A"
        avg_mae = round(group["mae"].mean(), 2) if "mae" in group.columns else "N/A"
        fix = _replay_fix_hint(target_first, stop_first, avg_mfe, avg_mae)
        rows.append({
            "Setup Type": setup_type,
            "Count": len(group),
            "Target First": int(target_first),
            "Stop First": int(stop_first),
            "Avg MFE": avg_mfe,
            "Avg MAE": avg_mae,
            "Fix": fix,
        })

    return pd.DataFrame(rows).sort_values(["Count", "Setup Type"], ascending=[False, True])


def _replay_fix_hint(target_first, stop_first, avg_mfe, avg_mae):

    if stop_first > target_first:

        return "review stop/entry timing"
    if target_first > stop_first:

        return "keep collecting"
    if isinstance(avg_mfe, (int, float)) and isinstance(avg_mae, (int, float)):

        if avg_mfe < abs(avg_mae):

            return "tighten or block"

    return "review"


def build_opened_trades_table(trades_df):

    if trades_df.empty:

        return pd.DataFrame()

    columns = [
        column for column in [
            "event_time",
            "event_type",
            "trade_key",
            "symbol",
            "direction",
            "option_ticker",
            "setup_type",
            "market_regime",
            "option_quality_score",
            "option_spread_pct",
            "option_quote_freshness",
            "r_multiple",
            "status",
            "exit_reason",
        ]
        if column in trades_df.columns
    ]

    return trades_df[columns].tail(25) if columns else trades_df.tail(25)


def build_best_skipped_table(scanner_df, decision_log):

    candidates = []

    if not scanner_df.empty:

        df = scanner_df.copy()
        action_column = _first_existing(df, ["Action Status", "action_status"])
        rr_column = _first_existing(df, ["Candidate RR", "Risk Reward", "RR", "rr"])
        setup_column = _first_existing(df, ["Setup %", "setup_percent", "15m Score"])

        if action_column:

            skipped_df = df[
                ~df[action_column].astype(str).str.upper().isin(["ENTER", "ENTER_PAPER"])
            ].copy()
        else:

            skipped_df = df.copy()

        if not skipped_df.empty:

            skipped_df = skipped_df[
                skipped_df.apply(
                    lambda row: price_geometry_error(row) is None,
                    axis=1
                )
            ].copy()

        if rr_column:

            skipped_df["_rr"] = _safe_numeric(skipped_df[rr_column])
        else:

            skipped_df["_rr"] = 0

        if setup_column:

            skipped_df["_setup"] = _safe_numeric(skipped_df[setup_column])
        else:

            skipped_df["_setup"] = 0

        candidates.append(
            skipped_df.sort_values(["_setup", "_rr"], ascending=False).head(10)
        )

    if decision_log:

        decision_df = pd.DataFrame(decision_log)

        if not decision_df.empty and "decision" in decision_df.columns:

            blocked_df = decision_df[
                decision_df["decision"].astype(str).str.upper().isin(["BLOCKED", "SKIPPED"])
            ].copy()
            blocked_df["_setup"] = _safe_numeric(blocked_df.get("setup_percent", 0))
            blocked_df["_rr"] = _safe_numeric(blocked_df.get("rr", 0))
            candidates.append(
                blocked_df.sort_values(["_setup", "_rr"], ascending=False).head(10)
            )

    if not candidates:

        return pd.DataFrame()

    combined_df = pd.concat(candidates, ignore_index=True, sort=False)
    columns = [
        column for column in [
            "Symbol",
            "symbol",
            "Top Candidate",
            "top_candidate",
            "Entry",
            "setup_percent",
            "Setup %",
            "Candidate RR",
            "rr",
            "Action Status",
            "decision",
            "reason",
            "Blocked By",
            "blocked_by",
        ]
        if column in combined_df.columns
    ]

    return combined_df[columns].head(15) if columns else combined_df.head(15)


def build_rule_suggestions(trade_metrics, gate_metrics, replay_df, expectancy_reports):

    suggestions = []

    opened_count = gate_metrics.get("OPENED", 0) if gate_metrics else 0

    if opened_count > 3:

        suggestions.append("Too many auto-paper opens. Review top-candidate, cooldown, and range-bound gates.")
    if opened_count == 0:

        suggestions.append("No auto-paper opens. Check whether A+ setups were blocked by minor spread, quote, or cooldown rules.")
    total_r = trade_metrics.get("Total R") if trade_metrics else None

    if isinstance(total_r, (int, float)) and total_r < -2:

        suggestions.append("Daily R drawdown exceeded -2R. Review opened trades before changing strategy thresholds.")
    if not replay_df.empty and replay_df["Stop First"].sum() > replay_df["Target First"].sum():

        suggestions.append("Replay is stop-heavy today. Review stop width, target distance, and entry timing by setup.")

    weak_groups = []

    for report_df in expectancy_reports.values():

        if "verdict" in report_df.columns:

            weak_groups.extend(
                report_df[report_df["verdict"].astype(str).eq("BLOCK/TIGHTEN")]["group"]
                .astype(str)
                .head(3)
                .tolist()
            )

    if weak_groups:

        suggestions.append(
            "Negative expectancy groups to watch: " + ", ".join(sorted(set(weak_groups))[:5])
        )
    if not suggestions:

        suggestions.append("No urgent rule change. Keep collecting samples before tuning thresholds.")

    return suggestions


def archive_inputs(input_paths, review_dir):

    review_dir.mkdir(parents=True, exist_ok=True)
    copied = []

    for label, path in input_paths.items():

        if not path.exists():

            continue

        destination = review_dir / path.name
        shutil.copy2(path, destination)
        copied.append(str(destination))

    return copied


def update_daily_inputs(input_paths, report_date):

    daily_dir = get_daily_dir(report_date)
    copied = []

    for label, source_path in input_paths.items():

        if not source_path.exists():

            continue

        destination = daily_dir / DAILY_INPUT_NAMES.get(label, source_path.name)

        if source_path.resolve() == destination.resolve():

            continue

        shutil.copy2(source_path, destination)
        copied.append(str(destination))

    return copied


def render_report(
    report_date,
    manifest,
    data_health,
    data_health_warnings,
    trade_metrics,
    trades_df,
    gate_metrics_full,
    gate_reasons_full_df,
    gate_metrics_auto_entry,
    gate_reasons_auto_entry_df,
    gate_metrics_after_close,
    gate_reasons_after_close_df,
    quote_freshness_auto_entry_df,
    quote_freshness_after_close_df,
    skipped_df,
    replay_df,
    expectancy_reports,
    data_quality_df,
    lifecycle_quote_freshness_df,
    lifecycle_review_metrics,
    lifecycle_expiry_metrics,
    suggestions,
    archived_files
):

    manifest_rows = "".join(
        _metric_row(label, manifest.get(label, ""))
        for label in [
            "trading_day",
            "session_id",
            "status",
            "first_scan_at",
            "last_scan_at",
            "last_scan_id",
            "finalized"
        ]
    )
    health_rows = "".join(
        _metric_row(label, value)
        for label, value in data_health.items()
    )
    health_warnings_html = "".join(
        f"<li>{html.escape(warning)}</li>"
        for warning in data_health_warnings
    ) or "<li>No data-health warnings.</li>"
    lifecycle_review_rows = "".join(
        _metric_row(label, value)
        for label, value in lifecycle_review_metrics.items()
    )
    lifecycle_expiry_rows = "".join(
        _metric_row(label, value)
        for label, value in lifecycle_expiry_metrics.items()
    )
    metric_rows = "".join(
        _metric_row(label, value)
        for label, value in {"Date": report_date, **trade_metrics}.items()
    )
    gate_rows_full = "".join(
        _metric_row(label, value)
        for label, value in gate_metrics_full.items()
    )
    gate_rows_auto_entry = "".join(
        _metric_row(label, value)
        for label, value in gate_metrics_auto_entry.items()
    )
    gate_rows_after_close = "".join(
        _metric_row(label, value)
        for label, value in gate_metrics_after_close.items()
    )
    expectancy_sections = []

    for group_name, report_df in expectancy_reports.items():

        expectancy_sections.append(f"<h3>By {html.escape(group_name)}</h3>")
        expectancy_sections.append(_html_table(report_df))

    suggestions_html = "".join(
        f"<li>{html.escape(suggestion)}</li>"
        for suggestion in suggestions
    )
    archived_html = "".join(
        f"<li>{html.escape(path)}</li>"
        for path in archived_files
    ) or "<li>No input files archived.</li>"

    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Daily Validation {html.escape(report_date)}</title>
  <style>
    body {{ font-family: Segoe UI, Arial, sans-serif; margin: 32px; color: #202124; }}
    h1, h2, h3 {{ margin-top: 28px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0 24px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 7px 9px; text-align: left; }}
    th {{ background: #f6f8fa; }}
    .metric-table {{ max-width: 760px; }}
  </style>
</head>
<body>
  <h1>Daily Validation {html.escape(report_date)}</h1>

    <h2>Session Manifest</h2>
    <table class="metric-table">{manifest_rows}</table>

    <h2>Validation Data Health</h2>
    <table class="metric-table">{health_rows}</table>
    <ul>{health_warnings_html}</ul>

  <h2>A. Trade Result Scorecard</h2>
  <table class="metric-table">{metric_rows}</table>
  <h3>Opened Trades</h3>
  {_html_table(build_opened_trades_table(trades_df), "No opened/closed trade rows found.")}

    <h2>B. Gate Quality - Full Day</h2>
    <table class="metric-table">{gate_rows_full}</table>
    <h3>Top Block/Skip Reasons</h3>
    {_html_table(gate_reasons_full_df, "No block/skip reasons found.")}

    <h2>Gate Quality - Auto Entry Window Only</h2>
    <table class="metric-table">{gate_rows_auto_entry}</table>
    <h3>Top skip reasons during 09:45-15:30 ET only</h3>
    {_html_table(gate_reasons_auto_entry_df, "No auto-entry-window skip reasons found.")}

    <h2>Gate Quality - After Close Only</h2>
    <table class="metric-table">{gate_rows_after_close}</table>
    <h3>Top Block/Skip Reasons After Close</h3>
    {_html_table(gate_reasons_after_close_df, "No after-close skip reasons found.")}

    <h2>Quote Freshness - Auto Entry Window Only</h2>
    {_html_table(quote_freshness_auto_entry_df, "No auto-entry-window quote freshness data found.")}

    <h2>Quote Freshness - After Close Only</h2>
    {_html_table(quote_freshness_after_close_df, "No after-close quote freshness data found.")}

  <h2>C. Replay Calibration Scorecard</h2>
  {_html_table(replay_df, "No replay telemetry found.")}

  <h2>D. Expectancy Scorecard</h2>
  {''.join(expectancy_sections) if expectancy_sections else '<p>No expectancy tables available yet.</p>'}

  <h2>E. Backtest Validation Scorecard</h2>
  <p>Run the no-lookahead backtest against historical candles once the daily dataset is available. Suggested progression: last 5 trading days, then 20, then 60.</p>

    <h2>F. Data Quality Checks</h2>
    {_html_table(data_quality_df, "No data quality checks available.")}

    <h2>G. Signal Lifecycle Analysis</h2>
    <h3>Quote Freshness During Auto Entry Window</h3>
    {_html_table(lifecycle_quote_freshness_df, "No signal lifecycle quote freshness data found.")}
    <h3>Review State Duration</h3>
    <table class="metric-table">{lifecycle_review_rows}</table>
    <h3>Suggestion Expiry</h3>
    <table class="metric-table">{lifecycle_expiry_rows}</table>

  <h2>Best Skipped Opportunities</h2>
  {_html_table(skipped_df, "No skipped/block candidate data found.")}

  <h2>Rule-Change Suggestions</h2>
  <ul>{suggestions_html}</ul>

  <h2>Archived Inputs</h2>
  <ul>{archived_html}</ul>
</body>
</html>
"""


def build_report(args):

    report_date = args.date or _today()
    manifest = get_or_create_session_manifest(report_date)
    manifest.setdefault("trading_day", report_date)
    manifest.setdefault("session_id", get_session_id(report_date))

    if args.finalize:

        manifest = finalize_daily_report(report_date)

    input_paths = resolve_input_paths(report_date)
    scanner_df = _read_excel(input_paths["scanner_output"])
    telemetry_df = _read_csv(input_paths["telemetry"])
    paper_events_df = _read_csv(input_paths["paper_trade_events"])
    paper_trade_state = _read_json(input_paths["paper_trade_state"], {})
    trade_state = _read_json(input_paths["trade_state"], {})
    (
        decision_log,
        decision_source,
        decision_source_path,
        decision_source_warning,
    ) = load_auto_paper_decisions(
        report_date,
        input_paths["auto_paper_decision_log"],
    )
    suggested_trade_state = _read_json(input_paths["suggested_trade_state"], {})
    candidate_df = _read_candidate_snapshots(report_date)
    lifecycle_events_df = _read_csv(daily_path(report_date, "signal_lifecycle_events.csv"))
    lifecycle_transitions_df = _read_csv(daily_path(report_date, "signal_state_transitions.csv"))
    data_health, data_health_warnings = build_data_health(
        scanner_df,
        telemetry_df,
        paper_trade_state,
        trade_state,
        decision_log,
        decision_source,
        str(decision_source_path),
        suggested_trade_state,
        candidate_df,
        paper_events_df
    )

    if decision_source_warning:

        data_health_warnings.append(decision_source_warning)

    trade_source_df = choose_trade_score_source(
        paper_events_df,
        telemetry_df,
        paper_trade_state
    )

    trade_metrics, trades_df = build_trade_scorecard(trade_source_df)
    gate_metrics_full, gate_reasons_full_df = build_gate_scorecard(decision_log)
    auto_entry_decisions = filter_decisions_by_bucket(decision_log, "AUTO_ENTRY_WINDOW")
    after_close_decisions = filter_decisions_by_bucket(decision_log, "AFTER_CLOSE")
    gate_metrics_auto_entry, gate_reasons_auto_entry_df = build_gate_scorecard(auto_entry_decisions)
    gate_metrics_after_close, gate_reasons_after_close_df = build_gate_scorecard(after_close_decisions)
    quote_freshness_auto_entry_df = build_quote_freshness_table(auto_entry_decisions)
    quote_freshness_after_close_df = build_quote_freshness_table(after_close_decisions)
    replay_df = build_replay_scorecard(telemetry_df)
    expectancy_source = _normalize_telemetry(telemetry_df)
    expectancy_reports = (
        build_grouped_expectancy_reports(expectancy_source)
        if not expectancy_source.empty
        else {}
    )
    skipped_df = build_best_skipped_table(scanner_df, decision_log)
    data_quality_df = build_data_quality_checks(
        scanner_df,
        candidate_df,
        suggested_trade_state,
        decision_log,
        paper_events_df,
        paper_trade_state
    )
    (
        lifecycle_quote_freshness_df,
        lifecycle_review_metrics,
        lifecycle_expiry_metrics,
    ) = build_signal_lifecycle_analysis(
        lifecycle_events_df,
        lifecycle_transitions_df,
        suggested_trade_state,
    )
    suggestions = build_rule_suggestions(
        trade_metrics,
        gate_metrics_full,
        replay_df,
        expectancy_reports
    )
    archived_files = []

    if args.update_daily:

        archived_files.extend(
            update_daily_inputs(input_paths, report_date)
        )

    if args.archive:

        archived_files.extend(archive_inputs(
            input_paths,
            ROOT_DIR / "daily_reviews" / report_date
        ))

    output_path = Path(args.output) if args.output else (
        ROOT_DIR / "reports" / f"daily_validation_{report_date}.html"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_report(
            report_date,
            manifest,
            data_health,
            data_health_warnings,
            trade_metrics or {
                "Total paper trades": 0,
                "Wins": 0,
                "Losses": 0,
                "Win rate": "0%",
                "Total R": 0,
                "Average R": 0,
                "Best trade": "N/A",
                "Worst trade": "N/A",
                "Average hold time minutes": "N/A",
                "Calls vs puts": "N/A",
            },
            trades_df,
            gate_metrics_full or {"OPENED": 0, "BLOCKED": 0, "SKIPPED": 0},
            gate_reasons_full_df,
            gate_metrics_auto_entry or {"OPENED": 0, "BLOCKED": 0, "SKIPPED": 0},
            gate_reasons_auto_entry_df,
            gate_metrics_after_close or {"OPENED": 0, "BLOCKED": 0, "SKIPPED": 0},
            gate_reasons_after_close_df,
            quote_freshness_auto_entry_df,
            quote_freshness_after_close_df,
            skipped_df,
            replay_df,
            expectancy_reports,
            data_quality_df,
            lifecycle_quote_freshness_df,
            lifecycle_review_metrics,
            lifecycle_expiry_metrics,
            suggestions,
            archived_files
        ),
        encoding="utf-8"
    )

    daily_report_path = daily_path(report_date, "daily_validation_report.html")
    shutil.copy2(output_path, daily_report_path)

    return output_path


def parse_args():

    parser = argparse.ArgumentParser(
        description="Create a daily scanner validation HTML report."
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Report date in YYYY-MM-DD format. Defaults to today."
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output HTML path. Defaults to reports/daily_validation_YYYY-MM-DD.html."
    )
    parser.add_argument(
        "--archive",
        action="store_true",
        help="Copy scanner/telemetry/state inputs into daily_reviews/YYYY-MM-DD/."
    )
    parser.add_argument(
        "--update-daily",
        action="store_true",
        help="Copy available live/legacy inputs into data/daily/YYYY-MM-DD/ before writing the report."
    )
    parser.add_argument(
        "--finalize",
        action="store_true",
        help="Mark the daily manifest FINAL after writing the report."
    )

    return parser.parse_args()


if __name__ == "__main__":

    output = build_report(parse_args())
    print(f"[DAILY VALIDATION REPORT] {output}")