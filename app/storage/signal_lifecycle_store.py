from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.storage.auto_paper_decision_store import classify_decision_time
from app.storage.daily_paths import get_daily_dir
from app.storage.session_manager import get_session_id, get_trading_day, now_et


ROOT_DIR = Path(__file__).resolve().parents[2]
STATE_FILE = ROOT_DIR / "app" / "state" / "signal_lifecycle_state.json"

LIFECYCLE_EVENT_FIELDS = [
    "trading_day",
    "session_id",
    "scan_id",
    "observed_at",
    "market_session",
    "is_auto_entry_window",
    "candidate_key",
    "symbol",
    "direction",
    "setup_type",
    "option_ticker",
    "top_candidate",
    "final_signal",
    "action_status",
    "action_reason",
    "realtime_ready",
    "blocked_by",
    "realtime_block_reason",
    "option_quote_freshness",
    "option_quote_age_minutes",
    "option_quality_score",
    "option_spread_pct",
    "expiration_bucket",
    "affordable",
    "affordability_status",
    "option_contract_cost",
    "setup_percent",
    "rr",
    "entry_price",
    "stop_loss",
    "take_profit",
    "market_regime",
    "reference_regime",
    "score",
    "candidate_rank",
    "entry_readiness",
    "trend_health",
    "previous_rank",
    "rank_change",
    "promotion_reason",
    "demotion_reason",
    "state_label",
]

STATE_TRANSITION_FIELDS = [
    "trading_day",
    "session_id",
    "candidate_key",
    "symbol",
    "direction",
    "setup_type",
    "option_ticker",
    "previous_state",
    "new_state",
    "state_started_at",
    "state_ended_at",
    "duration_minutes",
    "market_session",
    "from_action_status",
    "to_action_status",
    "from_quote_freshness",
    "to_quote_freshness",
    "from_realtime_ready",
    "to_realtime_ready",
    "previous_rank",
    "new_rank",
    "rank_change",
    "promotion_reason",
    "demotion_reason",
    "reason",
]


def _read_json(path: Path) -> dict[str, Any]:

    if not path.exists() or path.stat().st_size == 0:

        return {}

    try:

        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    except Exception:

        return {}


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(data, indent=2, default=str),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _append_csv(path: Path, row: dict[str, Any], fieldnames: list[str]) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists() and path.stat().st_size > 0

    with path.open("a", newline="", encoding="utf-8") as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        if not file_exists:

            writer.writeheader()

        writer.writerow(row)


def _append_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:

    if not rows:

        return

    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists() and path.stat().st_size > 0

    with path.open("a", newline="", encoding="utf-8") as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        if not file_exists:

            writer.writeheader()

        writer.writerows(rows)


def _row_get(row: dict[str, Any], *names: str, default=None):

    for name in names:

        value = row.get(name)

        if value is None:

            continue

        if str(value).strip().lower() in {"", "nan", "none"}:

            continue

        return value

    return default


def _bool_value(value) -> bool:

    if isinstance(value, bool):

        return value

    return str(value).strip().lower() in {"true", "1", "yes"}


def _timestamp_text(value: datetime) -> str:

    return value.replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")


def _parse_timestamp(value):

    try:

        return datetime.fromisoformat(str(value))

    except Exception:

        try:

            return datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")

        except Exception:

            return None


def _minutes_between(start_value, end_value):

    start = _parse_timestamp(start_value)
    end = _parse_timestamp(end_value)

    if not start or not end:

        return None

    return round((end - start).total_seconds() / 60, 2)


def build_candidate_key(row: dict[str, Any]) -> str:

    symbol = str(_row_get(row, "symbol", "Symbol", default="")).strip()
    direction = str(_row_get(row, "direction", "Candidate Direction", "Direction", "Entry", default="")).strip()
    setup_type = str(_row_get(row, "setup_type", "Setup Type", "Entry", default="")).strip()
    option_ticker = str(_row_get(row, "option_ticker", "Option Ticker", "Recommended Option Ticker", default="")).strip()

    return "|".join([symbol, direction, setup_type, option_ticker])


def _state_key(event: dict[str, Any]) -> str:

    return "|".join([
        str(event.get("trading_day") or ""),
        str(event.get("candidate_key") or ""),
    ])


def build_state_label(row: dict[str, Any]) -> str:

    action_status = str(_row_get(row, "action_status", "Action Status", default="UNKNOWN")).strip()
    quote_freshness = str(_row_get(row, "option_quote_freshness", "Option Quote Freshness", default="NO_OPTION")).strip()
    realtime_ready = _row_get(row, "realtime_ready", "Realtime Ready", default=False)
    blocked_by = str(_row_get(row, "blocked_by", "Blocked By", default="NO_BLOCK")).strip()
    ready_label = "READY" if _bool_value(realtime_ready) else "NOT_READY"

    return f"{action_status}|{quote_freshness}|{ready_label}|{blocked_by}"


def _event_from_row(
    row: dict[str, Any],
    trading_day: str,
    session_id: str,
    scan_id: str,
    observed_at: datetime,
    market_session: str,
    is_auto_entry_window: bool,
) -> dict[str, Any]:

    state_label = build_state_label(row)

    return {
        "trading_day": trading_day,
        "session_id": session_id,
        "scan_id": scan_id,
        "observed_at": _timestamp_text(observed_at),
        "market_session": market_session,
        "is_auto_entry_window": is_auto_entry_window,
        "candidate_key": build_candidate_key(row),
        "symbol": _row_get(row, "symbol", "Symbol"),
        "direction": _row_get(row, "direction", "Candidate Direction", "Direction", "Entry"),
        "setup_type": _row_get(row, "setup_type", "Setup Type", "Entry"),
        "option_ticker": _row_get(row, "option_ticker", "Option Ticker", "Recommended Option Ticker"),
        "top_candidate": _row_get(row, "top_candidate", "Top Candidate"),
        "final_signal": _row_get(row, "final_signal", "Final Signal", "Signal"),
        "action_status": _row_get(row, "action_status", "Action Status"),
        "action_reason": _row_get(row, "action_reason", "Action Reason"),
        "realtime_ready": _row_get(row, "realtime_ready", "Realtime Ready"),
        "blocked_by": _row_get(row, "blocked_by", "Blocked By"),
        "realtime_block_reason": _row_get(row, "realtime_block_reason", "Realtime Block Reason"),
        "option_quote_freshness": _row_get(row, "option_quote_freshness", "Option Quote Freshness"),
        "option_quote_age_minutes": _row_get(row, "option_quote_age_minutes", "Option Quote Age Minutes"),
        "option_quality_score": _row_get(row, "option_quality_score", "Option Quality Score"),
        "option_spread_pct": _row_get(row, "option_spread_pct", "Option Spread %"),
        "expiration_bucket": _row_get(row, "expiration_bucket", "Expiration Bucket"),
        "affordable": _row_get(row, "affordable", "Affordable"),
        "affordability_status": _row_get(row, "affordability_status", "Affordability Status"),
        "option_contract_cost": _row_get(row, "option_contract_cost", "Option Contract Cost"),
        "setup_percent": _row_get(row, "setup_percent", "Setup %"),
        "rr": _row_get(row, "rr", "Candidate RR", "Risk Reward", "RR"),
        "entry_price": _row_get(row, "entry_price", "Candidate Entry Price", "Entry Price"),
        "stop_loss": _row_get(row, "stop_loss", "Candidate Stop Price", "Stop Loss"),
        "take_profit": _row_get(row, "take_profit", "Candidate Target Price", "Take Profit"),
        "market_regime": _row_get(row, "market_regime", "Market Regime"),
        "reference_regime": _row_get(row, "reference_regime", "Reference Regime"),
        "score": _row_get(row, "score", "15m Score"),
        "candidate_rank": _row_get(row, "candidate_rank", "Candidate Rank"),
        "entry_readiness": _row_get(row, "entry_readiness", "ENTRY_READINESS"),
        "trend_health": _row_get(row, "trend_health", "Trend Health State"),
        "state_label": state_label,
    }


def _build_transition(
    event: dict[str, Any],
    previous: dict[str, Any],
    reason: str | None = None,
) -> dict[str, Any]:

    previous_event = previous.get("last_event") or {}
    previous_rank = _row_get(previous_event, "candidate_rank", default=None)
    new_rank = _row_get(event, "candidate_rank", default=None)

    try:

        rank_change = int(previous_rank) - int(new_rank)

    except Exception:

        rank_change = None

    promotion_reason = event.get("action_reason") if rank_change is not None and rank_change > 0 else None
    demotion_reason = event.get("action_reason") if rank_change is not None and rank_change < 0 else None
    return {
        "trading_day": event["trading_day"],
        "session_id": event["session_id"],
        "candidate_key": event["candidate_key"],
        "symbol": event["symbol"],
        "direction": event["direction"],
        "setup_type": event["setup_type"],
        "option_ticker": event["option_ticker"],
        "previous_state": previous.get("state_label"),
        "new_state": event["state_label"],
        "state_started_at": previous.get("state_started_at"),
        "state_ended_at": event["observed_at"],
        "duration_minutes": _minutes_between(previous.get("state_started_at"), event["observed_at"]),
        "market_session": event["market_session"],
        "from_action_status": previous_event.get("action_status"),
        "to_action_status": event["action_status"],
        "from_quote_freshness": previous_event.get("option_quote_freshness"),
        "to_quote_freshness": event["option_quote_freshness"],
        "from_realtime_ready": previous_event.get("realtime_ready"),
        "to_realtime_ready": event["realtime_ready"],
        "previous_rank": previous_rank,
        "new_rank": new_rank,
        "rank_change": rank_change,
        "promotion_reason": promotion_reason,
        "demotion_reason": demotion_reason,
        "reason": reason or event.get("blocked_by") or event.get("realtime_block_reason"),
    }


def _append_transition(
    daily_dir: Path,
    event: dict[str, Any],
    previous: dict[str, Any],
    reason: str | None = None,
) -> None:

    transition = _build_transition(event, previous, reason=reason)
    _append_csv(
        daily_dir / "signal_state_transitions.csv",
        transition,
        STATE_TRANSITION_FIELDS,
    )


def record_signal_lifecycle_event(
    row: dict[str, Any],
    daily_dir: Path,
    trading_day: str,
    session_id: str,
    scan_id: str,
    observed_at: datetime,
    market_session: str,
    is_auto_entry_window: bool,
) -> dict[str, Any]:

    event = _event_from_row(
        row,
        trading_day,
        session_id,
        scan_id,
        observed_at,
        market_session,
        is_auto_entry_window,
    )
    _append_csv(
        daily_dir / "signal_lifecycle_events.csv",
        event,
        LIFECYCLE_EVENT_FIELDS,
    )
    latest_state = _read_json(STATE_FILE)
    state_key = _state_key(event)
    previous = latest_state.get(state_key)

    if previous and previous.get("state_label") != event["state_label"]:

        _append_transition(daily_dir, event, previous)
        latest_state[state_key] = {
            "state_label": event["state_label"],
            "state_started_at": event["observed_at"],
            "last_seen_at": event["observed_at"],
            "last_event": event,
        }

    elif previous:

        previous["last_seen_at"] = event["observed_at"]
        previous["last_event"] = event
        latest_state[state_key] = previous

    else:

        latest_state[state_key] = {
            "state_label": event["state_label"],
            "state_started_at": event["observed_at"],
            "last_seen_at": event["observed_at"],
            "last_event": event,
        }

    _atomic_write_json(STATE_FILE, latest_state)
    return event


def record_signal_lifecycle_events_for_scan(
    rows: list[dict[str, Any]],
    trading_day: str,
    scan_id: str,
    observed_at: datetime,
) -> int:

    session_id = get_session_id(trading_day)
    daily_dir = get_daily_dir(trading_day)
    session_fields = classify_decision_time(observed_at)
    latest_state = _read_json(STATE_FILE)
    events = []
    transitions = []

    for row in rows:

        event = _event_from_row(
            row,
            trading_day,
            session_id,
            scan_id,
            observed_at,
            session_fields["market_session"],
            session_fields["is_auto_entry_window"],
        )
        events.append(event)
        state_key = _state_key(event)
        previous = latest_state.get(state_key)

        if previous and previous.get("state_label") != event["state_label"]:

            transitions.append(_build_transition(event, previous))
            latest_state[state_key] = {
                "state_label": event["state_label"],
                "state_started_at": event["observed_at"],
                "last_seen_at": event["observed_at"],
                "last_event": event,
            }

        elif previous:

            previous["last_seen_at"] = event["observed_at"]
            previous["last_event"] = event
            latest_state[state_key] = previous

        else:

            latest_state[state_key] = {
                "state_label": event["state_label"],
                "state_started_at": event["observed_at"],
                "last_seen_at": event["observed_at"],
                "last_event": event,
            }

    _append_csv_rows(
        daily_dir / "signal_lifecycle_events.csv",
        events,
        LIFECYCLE_EVENT_FIELDS,
    )
    _append_csv_rows(
        daily_dir / "signal_state_transitions.csv",
        transitions,
        STATE_TRANSITION_FIELDS,
    )
    _atomic_write_json(STATE_FILE, latest_state)

    return len(events)


def record_signal_expiry_transition(
    suggestion: dict[str, Any],
    reason: str = "not present in latest scan",
    expired_at: datetime | None = None,
) -> dict[str, Any]:

    expired_at = expired_at or now_et()
    trading_day = get_trading_day(expired_at)
    session_id = get_session_id(trading_day)
    daily_dir = get_daily_dir(trading_day)
    session_fields = classify_decision_time(expired_at)
    row = {
        "symbol": suggestion.get("symbol"),
        "direction": suggestion.get("direction"),
        "setup_type": suggestion.get("setup_type"),
        "option_ticker": suggestion.get("option_ticker"),
        "Action Status": "EXPIRED_NOT_ENTERED",
        "Option Quote Freshness": "NO_OPTION",
        "Realtime Ready": False,
        "Blocked By": "NOT_PRESENT",
    }
    event = _event_from_row(
        row,
        trading_day=trading_day,
        session_id=session_id,
        scan_id="expiry",
        observed_at=expired_at,
        market_session=session_fields["market_session"],
        is_auto_entry_window=session_fields["is_auto_entry_window"],
    )
    latest_state = _read_json(STATE_FILE)
    state_key = _state_key(event)
    previous = latest_state.get(state_key)

    if previous and previous.get("state_label") != event["state_label"]:

        _append_transition(daily_dir, event, previous, reason=reason)

    latest_state[state_key] = {
        "state_label": event["state_label"],
        "state_started_at": event["observed_at"],
        "last_seen_at": event["observed_at"],
        "last_event": event,
    }
    _atomic_write_json(STATE_FILE, latest_state)
    return {
        "expired_at": event["observed_at"],
        "expiry_market_session": event["market_session"],
        "last_state_before_expiry": previous.get("state_label") if previous else None,
    }