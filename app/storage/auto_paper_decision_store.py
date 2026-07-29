from __future__ import annotations

import csv
import json
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


RECENT_LOG_LIMIT = 500
MARKET_TZ = ZoneInfo("America/New_York")
AUTO_PAPER_DECISION_FIELDS = [
    "timestamp",
    "trading_day",
    "session_id",
    "scan_id",
    "scan_timestamp",
    "market_session",
    "decision_time_bucket",
    "is_regular_market",
    "is_auto_entry_window",
    "is_after_close",
    "minutes_from_open",
    "minutes_to_close",
    "gate_mode",
    "min_rr_used",
    "min_setup_used",
    "symbol",
    "decision",
    "reason",
    "trade_key",
    "top_candidate",
    "setup_percent",
    "rr",
    "setup_valid",
    "execution_ready",
    "realtime_ready",
    "affordable",
    "paper_affordability_override",
    "original_affordable",
    "original_affordability_status",
    "original_option_contract_cost",
    "original_max_allowed_contract_cost",
    "price_geometry_ok",
    "price_geometry_error",
    "scanner_output_age_minutes",
    "allow_review_tv_chart_auto_paper",
    "review_validation_candidate",
    "real_trading_enabled",
    "real_alerts_only",
    "paper_trade_opened",
    "real_trade_readiness",
    "real_review_scan_count",
    "real_entry_checklist",
    "action_status",
    "blocked_by",
    "scanner_blocked_by",
    "action_reason",
    "option_rejection_reason",
    "realtime_block_reason",
    "option_quality_score",
    "option_spread_pct",
    "option_quote_freshness",
    "expiration_bucket",
    "early_watch_status",
    "early_watch_reason",
    "would_pass_gate_if_rr_1_7",
    "would_pass_gate_if_setup_65",
    "would_pass_gate_if_review_allowed",
    "late_entry_risk",
    "missed_move_type",
]


def classify_decision_time(value: datetime) -> dict[str, Any]:

    if value.tzinfo is None:

        value = value.replace(tzinfo=MARKET_TZ)

    value = value.astimezone(MARKET_TZ)
    current_time = value.time()
    open_dt = value.replace(hour=9, minute=30, second=0, microsecond=0)
    close_dt = value.replace(hour=16, minute=0, second=0, microsecond=0)

    if current_time < time(9, 30):

        bucket = "PREMARKET"

    elif current_time < time(9, 45):

        bucket = "OPENING_RANGE"

    elif current_time < time(15, 30):

        bucket = "AUTO_ENTRY_WINDOW"

    elif current_time < time(16, 0):

        bucket = "LATE_NO_NEW_ENTRY_WINDOW"

    else:

        bucket = "AFTER_CLOSE"

    return {
        "market_session": bucket,
        "decision_time_bucket": bucket,
        "is_regular_market": time(9, 30) <= current_time < time(16, 0),
        "is_auto_entry_window": time(9, 45) <= current_time < time(15, 30),
        "is_after_close": current_time >= time(16, 0),
        "minutes_from_open": round((value - open_dt).total_seconds() / 60, 2),
        "minutes_to_close": round((close_dt - value).total_seconds() / 60, 2),
    }


def append_daily_auto_paper_decision(
    decision: dict[str, Any],
    daily_dir: Path,
) -> None:

    append_daily_auto_paper_decisions([decision], daily_dir)


def append_daily_auto_paper_decisions(
    decisions: list[dict[str, Any]],
    daily_dir: Path,
) -> None:

    decisions = [
        decision for decision in decisions
        if decision
    ]

    if not decisions:

        return

    daily_dir.mkdir(parents=True, exist_ok=True)
    path = daily_dir / "auto_paper_decisions.csv"
    file_exists = path.exists() and path.stat().st_size > 0

    with path.open("a", newline="", encoding="utf-8") as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=AUTO_PAPER_DECISION_FIELDS,
            extrasaction="ignore",
        )
        if not file_exists:

            writer.writeheader()

        writer.writerows(decisions)


def update_recent_auto_paper_log(
    decision: dict[str, Any],
    state_path: Path,
    limit: int = RECENT_LOG_LIMIT,
) -> None:

    update_recent_auto_paper_logs([decision], state_path, limit=limit)


def update_recent_auto_paper_logs(
    decisions: list[dict[str, Any]],
    state_path: Path,
    limit: int = RECENT_LOG_LIMIT,
) -> None:

    decisions = [
        decision for decision in decisions
        if decision
    ]

    if not decisions:

        return

    state_path.parent.mkdir(parents=True, exist_ok=True)

    if state_path.exists() and state_path.stat().st_size > 0:

        try:

            rows = json.loads(state_path.read_text(encoding="utf-8"))

            if not isinstance(rows, list):

                rows = []

        except Exception:

            rows = []

    else:

        rows = []

    rows.extend(decisions)
    rows = rows[-limit:]
    payload = json.dumps(rows, indent=2, default=str)
    tmp_path = state_path.with_name(
        f".{state_path.name}.{datetime.now().strftime('%Y%m%d%H%M%S%f')}.tmp"
    )

    try:

        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(state_path)

    except FileNotFoundError:

        state_path.write_text(payload, encoding="utf-8")

    except Exception:

        try:

            state_path.write_text(payload, encoding="utf-8")

        except Exception:

            pass

    finally:

        try:

            if tmp_path.exists():

                tmp_path.unlink()

        except Exception:

            pass