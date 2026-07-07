from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.gates import price_geometry_error
from app.storage.signal_lifecycle_store import record_signal_expiry_transition
from app.utils.json_store import load_json_file, save_json_file


ROOT_DIR = Path(__file__).resolve().parents[2]
SUGGESTED_TRADE_STATE_FILE = str(
    ROOT_DIR / "app" / "state" / "suggested_trade_state.json"
)
ET = ZoneInfo("America/New_York")

ACTIVE_STATUSES = {
    "NEW_CALL",
    "NEW_PUT",
    "STILL_VALID_CALL",
    "STILL_VALID_PUT",
    "WATCH_WEAKENING",
    "EXPIRED_NOT_ENTERED",
    "DO_NOT_CHASE",
    "CONTRACT_CHANGED"
}

PROMOTABLE_STATUSES = {
    "NEW_CALL",
    "NEW_PUT",
    "STILL_VALID_CALL",
    "STILL_VALID_PUT",
    "WATCH_WEAKENING",
    "CONTRACT_CHANGED"
}


def load_suggestions():

    return load_json_file(
        SUGGESTED_TRADE_STATE_FILE,
        {}
    )


def save_suggestions(state):

    save_json_file(
        SUGGESTED_TRADE_STATE_FILE,
        state
    )


def _now_et():

    return datetime.now(ET)


def _timestamp_text(value):

    if value.tzinfo is None:

        value = value.replace(tzinfo=ET)

    return value.astimezone(ET).isoformat(timespec="seconds")


def _now_str():

    return _timestamp_text(_now_et())


def _parse_timestamp(value):

    try:

        parsed = datetime.fromisoformat(str(value))

    except Exception:

        try:

            parsed = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")

        except Exception:

            return None

    if parsed.tzinfo is None:

        parsed = parsed.replace(tzinfo=ET)

    return parsed.astimezone(ET)


def _safe_float(value, default=None):

    try:

        if value is None:

            return default

        return float(value)

    except Exception:

        return default


def _row_get(row, *names, default=None):

    for name in names:

        try:

            value = row.get(name)

        except Exception:

            value = None

        if value not in [None, ""]:

            return value

    return default


def suggestion_id_from_row(row):

    symbol = str(row.get("Symbol") or "UNKNOWN")
    direction = str(row.get("Candidate Direction") or "NONE")
    setup_type = str(row.get("Entry") or "NO_ENTRY")
    option_ticker = str(row.get("Option Ticker") or "NO_OPTION")

    return "|".join([
        symbol,
        direction,
        setup_type,
        option_ticker
    ])


def _base_status(row, is_new):

    direction = str(row.get("Candidate Direction") or "")

    if is_new:

        return (
            "NEW_CALL"
            if direction == "CALL"
            else "NEW_PUT"
            if direction == "PUT"
            else "WATCH"
        )

    return (
        "STILL_VALID_CALL"
        if direction == "CALL"
        else "STILL_VALID_PUT"
        if direction == "PUT"
        else "WATCH"
    )


def upsert_suggestion_from_scan(row):

    state = load_suggestions()
    suggestion_id = suggestion_id_from_row(row)
    now = _now_str()
    existing = state.get(suggestion_id, {})
    is_new = not bool(existing)

    original_entry = _safe_float(
        existing.get("entry_price"),
        _safe_float(row.get("Candidate Entry Price"))
    )
    current_price = _safe_float(row.get("Price"))
    current_setup = _safe_float(row.get("Setup %"), 0) or 0
    current_rr = _safe_float(
        _row_get(row, "RR", "Candidate RR", "Risk Reward"),
        0
    ) or 0
    original_setup = _safe_float(
        existing.get("original_setup_percent"),
        current_setup
    ) or 0
    original_rr = _safe_float(
        existing.get("original_rr"),
        current_rr
    ) or 0

    status = _base_status(row, is_new)
    validity_reason = "current valid scanner candidate"

    if current_setup < original_setup - 10 or current_rr < original_rr - 0.25:

        status = "WATCH_WEAKENING"
        validity_reason = "setup or RR weakened"

    if original_entry and current_price:

        move_from_entry_pct = abs(
            current_price - original_entry
        ) / original_entry * 100

        if move_from_entry_pct >= 0.75:

            status = "DO_NOT_CHASE"
            validity_reason = "price moved away from original entry"

    geometry_error = price_geometry_error(row)

    if geometry_error:

        status = "INVALID_PRICE_GEOMETRY"
        validity_reason = geometry_error

    suggestion = {
        **existing,
        "suggestion_id": suggestion_id,
        "symbol": row.get("Symbol"),
        "direction": row.get("Candidate Direction"),
        "setup_type": row.get("Entry"),
        "option_ticker": row.get("Option Ticker"),
        "entry_price": row.get("Candidate Entry Price"),
        "stop_loss": row.get("Candidate Stop Price"),
        "take_profit": row.get("Candidate Target Price"),
        "first_seen_at": existing.get("first_seen_at", now),
        "last_seen_at": now,
        "last_valid_at": now,
        "status": status,
        "validity_reason": validity_reason,
        "times_seen": int(existing.get("times_seen", 0)) + 1,
        "original_setup_percent": existing.get(
            "original_setup_percent",
            row.get("Setup %")
        ),
        "original_rr": existing.get(
            "original_rr",
            _row_get(row, "RR", "Candidate RR", "Risk Reward")
        ),
        "current_setup_percent": row.get("Setup %"),
        "current_rr": _row_get(row, "RR", "Candidate RR", "Risk Reward"),
        "current_action_status": row.get("Action Status"),
        "current_price": row.get("Price"),
        "current_r_progress": row.get("RR Progress"),
        "top_candidate": row.get("Top Candidate"),
        "recommended_option": row.get("Recommended Option"),
        "option_expiration": row.get("Option Expiration"),
        "option_strike": row.get("Option Strike"),
        "expiration_bucket": row.get("Expiration Bucket"),
        "option_quality_score": row.get("Option Quality Score"),
        "option_quote_freshness": row.get("Option Quote Freshness"),
        "blocked_by": geometry_error or row.get("Blocked By"),
        "action_reason": geometry_error or row.get("Action Reason"),
        "realtime_ready": row.get("Realtime Ready"),
        "realtime_block_reason": geometry_error or row.get("Realtime Block Reason")
    }

    state[suggestion_id] = suggestion
    save_suggestions(state)

    return suggestion


def mark_suggestion_expired(suggestion_id, reason="not present in latest scan"):

    state = load_suggestions()

    if suggestion_id not in state:

        return None

    suggestion = state[suggestion_id]

    if suggestion.get("status") == "ENTERED_PAPER":

        return suggestion

    expired_at_dt = _now_et()
    expired_at = _timestamp_text(expired_at_dt)
    expiry = record_signal_expiry_transition(
        suggestion,
        reason=reason,
        expired_at=expired_at_dt
    )
    first_seen_at = suggestion.get("first_seen_at")
    last_valid_at = suggestion.get("last_valid_at")
    review_minutes = None

    if suggestion.get("current_action_status") == "REVIEW_TV_CHART":

        review_minutes = _minutes_between(first_seen_at, expired_at)

    suggestion["status"] = "EXPIRED_NOT_ENTERED"
    suggestion["validity_reason"] = reason
    suggestion["expired_at"] = expired_at
    suggestion["lifetime_minutes"] = _minutes_between(first_seen_at, expired_at)
    suggestion["valid_minutes"] = _minutes_between(first_seen_at, last_valid_at)
    suggestion["review_minutes"] = review_minutes
    suggestion["scans_seen"] = suggestion.get("times_seen", 0)
    suggestion["last_state_before_expiry"] = expiry.get("last_state_before_expiry")
    suggestion["expiry_market_session"] = expiry.get("expiry_market_session")
    suggestion["expiry_reason"] = reason
    suggestion["blocked_by"] = None
    suggestion["action_reason"] = reason
    suggestion["realtime_ready"] = False
    suggestion["realtime_block_reason"] = reason
    suggestion["last_seen_at"] = suggestion.get("last_seen_at")
    state[suggestion_id] = suggestion
    save_suggestions(state)

    return suggestion


def _minutes_between(start_value, end_value):

    start = _parse_timestamp(start_value)
    end = _parse_timestamp(end_value)

    if not start or not end:

        return None

    return round((end - start).total_seconds() / 60, 2)


def promote_suggestion_to_paper_trade(symbol):

    state = load_suggestions()

    for suggestion_id, suggestion in state.items():

        if suggestion.get("symbol") == symbol and suggestion.get("status") in PROMOTABLE_STATUSES:

            suggestion["status"] = "ENTERED_PAPER"
            suggestion["validity_reason"] = "promoted to paper trade"
            state[suggestion_id] = suggestion

    save_suggestions(state)


def sync_suggestions_from_scan(rows):

    state = load_suggestions()
    seen_ids = set()

    for row in rows:

        suggestion = upsert_suggestion_from_scan(row)
        seen_ids.add(suggestion["suggestion_id"])

    for suggestion_id, suggestion in list(state.items()):

        if suggestion_id in seen_ids:

            continue

        if suggestion.get("status") == "ENTERED_PAPER":

            continue

        if suggestion.get("status") in ["CLOSED", "EXPIRED_NOT_ENTERED"]:

            continue

        mark_suggestion_expired(
            suggestion_id,
            reason="not present in latest scan"
        )


def cleanup_old_suggestions(max_age_days=5):

    state = load_suggestions()
    cutoff = _now_et() - timedelta(days=max_age_days)

    for suggestion_id, suggestion in list(state.items()):

        last_seen = suggestion.get("last_seen_at") or suggestion.get("first_seen_at")

        last_seen_dt = _parse_timestamp(last_seen)

        if not last_seen_dt:

            continue

        if last_seen_dt < cutoff and suggestion.get("status") not in ["ENTERED_PAPER"]:

            del state[suggestion_id]

    save_suggestions(state)


def suggestions_as_list():

    return list(load_suggestions().values())
