import html
import os
import re
from datetime import datetime, timedelta, timezone
from functools import wraps
from inspect import signature
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from app.gates import (
    EntryGateConfig,
    evaluate_entry_gate
)
from app.runtime import measure_runtime
from app.runtime.telegram_dispatcher import dispatch_telegram_message
from app.storage.daily_paths import state_path
from app.utils.json_store import load_json_file, save_json_file


ROOT_DIR = Path(__file__).resolve().parents[2]
ALERT_STATE_FILE = state_path("telegram_alert_state.json")
MAX_SENT_ALERTS = 1000
ENTRY_EVENT_TYPE = "ENTRY"
REVIEW_EVENT_TYPE = "REVIEW"
UPDATE_EVENT_TYPE = "UPDATE"


EXIT_ALERT_TRADE_MODES = {
    "PAPER",
    "REAL",
    "SCANNER_TRACKED",
}


class TelegramDeliveryError(RuntimeError):

    def __init__(self, status_code, response_body):

        self.status_code = status_code
        self.telegram_response = response_body
        description = (
            response_body.get("description")
            if isinstance(response_body, dict)
            else response_body
        )
        super().__init__(
            f"Telegram API error {status_code}: {description}"
        )


def _bool_value(value, default=False):

    if value is None:

        return default

    return str(value).strip().lower() in [
        "1",
        "true",
        "yes",
        "y",
        "on"
    ]


def _int_setting(name, default):

    try:

        return int(
            os.getenv(
                name,
                _streamlit_secret([name], default)
            )
        )

    except Exception:

        return default


def _float_setting(name, default):

    try:

        return float(
            os.getenv(
                name,
                _streamlit_secret([name], default)
            )
        )

    except Exception:

        return default


def _exit_price_mismatch_limit():

    return _float_setting(
        "TELEGRAM_EXIT_PRICE_MISMATCH_PCT",
        0.03
    )


def resolve_exit_price_context(
    candidate_prices=None,
    fallback_current_price=None,
    fallback_price_source=None
):

    candidate_prices = candidate_prices or {}

    for source_name in (
        "latest_quote",
        "df_5m_latest_close",
        "df_15m_latest_close"
    ):

        price_value = _float_value(
            candidate_prices.get(source_name),
            None
        )

        if price_value is not None:

            return {
                "current_price": price_value,
                "price_source": source_name,
                "expected_underlying_price": price_value
            }

    fallback_price = _float_value(
        fallback_current_price,
        None
    )

    if fallback_price is not None:

        return {
            "current_price": fallback_price,
            "price_source": fallback_price_source or "provided_current_price",
            "expected_underlying_price": fallback_price
        }

    return {
        "current_price": None,
        "price_source": fallback_price_source or "unknown",
        "expected_underlying_price": None
    }


def _float_value(value, default=0.0):

    try:

        if value is None:

            return default

        return float(value)

    except Exception:

        return default


def _entry_alert_policy():

    return {
        "max_daily_entries": _int_setting(
            "TELEGRAM_MAX_ENTRY_ALERTS_PER_DAY",
            3
        ),
        "max_active_alerted_trades": _int_setting(
            "TELEGRAM_MAX_ACTIVE_ALERTED_TRADES",
            2
        ),
        "cooldown_minutes": _int_setting(
            "TELEGRAM_ENTRY_COOLDOWN_MINUTES",
            60
        ),
        "symbol_cooldown_minutes": _int_setting(
            "TELEGRAM_SYMBOL_COOLDOWN_MINUTES",
            60
        ),
        "top_candidate_limit": _int_setting(
            "TELEGRAM_TOP_CANDIDATE_LIMIT",
            3
        ),
        "min_alert_score": _float_setting(
            "TELEGRAM_MIN_ENTRY_ALERT_SCORE",
            85.0
        ),
        "instant_alert_score": _float_setting(
            "TELEGRAM_INSTANT_ENTRY_ALERT_SCORE",
            88.0
        ),
        "afternoon_min_alert_score": _float_setting(
            "TELEGRAM_AFTERNOON_MIN_ENTRY_ALERT_SCORE",
            90.0
        ),
        "min_option_quality": _float_setting(
            "TELEGRAM_MIN_OPTION_QUALITY_SCORE",
            65.0
        ),
        "min_rr": _float_setting(
            "TELEGRAM_MIN_RR",
            2.0
        ),
        "max_spread_pct": _float_setting(
            "TELEGRAM_MAX_SPREAD_PCT",
            8.0
        ),
        "max_morning_entries": _int_setting(
            "TELEGRAM_MAX_MORNING_ENTRY_ALERTS",
            2
        ),
        "max_midday_entries": _int_setting(
            "TELEGRAM_MAX_MIDDAY_ENTRY_ALERTS",
            1
        ),
        "max_afternoon_entries": _int_setting(
            "TELEGRAM_MAX_AFTERNOON_ENTRY_ALERTS",
            1
        )
    }


def _entry_alert_policy_mode():

    return str(
        os.getenv(
            "TELEGRAM_ALERT_POLICY",
            _streamlit_secret(["TELEGRAM_ALERT_POLICY"], "PAPER")
        )
    ).strip().upper()


def _streamlit_secret(path, default=None):

    try:

        import streamlit as st

        value = st.secrets

        for part in path:

            value = value[part]

        return value

    except Exception:

        return default


def telegram_alerts_enabled():

    return _bool_value(
        os.getenv(
            "TELEGRAM_ALERTS_ENABLED",
            _streamlit_secret(
                ["TELEGRAM_ALERTS_ENABLED"],
                _streamlit_secret(
                    ["telegram", "enabled"],
                    False
                )
            )
        )
    )


def _telegram_alert_type_enabled(name):

    return telegram_alerts_enabled() and _bool_value(
        os.getenv(
            name,
            _streamlit_secret(
                [name],
                _streamlit_secret(
                    ["telegram", name.lower()],
                    True
                )
            )
        ),
        True
    )


def telegram_entry_alerts_enabled():

    return _telegram_alert_type_enabled(
        "TELEGRAM_ENTRY_ALERTS_ENABLED"
    )


def telegram_exit_alerts_enabled():

    return _telegram_alert_type_enabled(
        "TELEGRAM_EXIT_ALERTS_ENABLED"
    )


def get_telegram_credentials():

    token = (
        os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("bot_token")
        or _streamlit_secret(["telegram", "bot_token"])
        or ""
    )
    chat_id = (
        os.getenv("TELEGRAM_CHAT_ID")
        or os.getenv("chat_id")
        or _streamlit_secret(["telegram", "chat_id"])
        or ""
    )

    return str(token).strip(), str(chat_id).strip()


def _alert_attempt_context(alert_type, arguments):

    if alert_type == "PAPER_ENTRY":

        trade = arguments.get("trade") or {}
        scanner_context = (
            arguments.get("scanner_context")
            or trade.get("scanner_context")
            or {}
        )
        return {
            "symbol": trade.get("symbol") or scanner_context.get("Symbol"),
            "direction": trade.get("direction") or scanner_context.get("Candidate Direction"),
            "option_ticker": trade.get("option_ticker") or scanner_context.get("Option Ticker"),
            "dedupe_key": _paper_entry_alert_key(
                trade,
                scanner_context
            ),
            "payload": {
                "trade_key": trade.get("trade_key"),
                "reason": arguments.get("reason"),
                "scanner_context": scanner_context
            }
        }

    if alert_type == "TRADE_EXIT":

        trade = arguments.get("trade") or {}
        return {
            "symbol": arguments.get("symbol") or trade.get("symbol"),
            "direction": trade.get("direction"),
            "option_ticker": trade.get("option_ticker") or trade.get("ticker"),
            "dedupe_key": _exit_alert_key(
                arguments.get("symbol") or trade.get("symbol"),
                trade.get("option_ticker") or trade.get("ticker"),
                trade,
                arguments.get("exit_reason"),
                arguments.get("event_type")
            ),
            "payload": {
                "trade_key": trade.get("trade_key"),
                "exit_reason": arguments.get("exit_reason"),
                "event_type": arguments.get("event_type"),
                "current_price": arguments.get("current_price"),
                "pnl_pct": arguments.get("pnl_pct"),
                "r_multiple": arguments.get("r_multiple"),
                "outcome": arguments.get("outcome"),
                "scanner_row_symbol": arguments.get("scanner_row_symbol")
            }
        }

    if alert_type == "TRADE_UPDATE":

        trade = arguments.get("trade") or {}
        scanner_context = arguments.get("scanner_context") or {}
        r_multiple = _trade_r_multiple(
            trade,
            arguments.get("current_price")
        )
        trend_health = (
            scanner_context.get("V2 Trend Health Status")
            or scanner_context.get("Trend Health State")
        )
        return {
            "symbol": trade.get("symbol"),
            "direction": trade.get("direction"),
            "option_ticker": trade.get("option_ticker"),
            "dedupe_key": _trade_update_alert_key(
                trade,
                r_multiple,
                trend_health
            ),
            "payload": {
                "trade_key": trade.get("trade_key"),
                "r_multiple": r_multiple,
                "trend_health": trend_health,
            }
        }

    if alert_type == "TRADE_OPEN":

        trade = arguments.get("trade") or {}
        scanner_context = arguments.get("scanner_context") or {}
        return {
            "symbol": trade.get("symbol"),
            "direction": (
                trade.get("direction")
                or scanner_context.get("Candidate Direction")
            ),
            "option_ticker": trade.get("option_ticker"),
            "dedupe_key": _trade_open_alert_key(trade),
            "payload": {
                "trade_key": trade.get("trade_key"),
                "scanner_context": scanner_context,
            }
        }

    option_contract = arguments.get("option_contract") or {}
    action_decision = arguments.get("action_decision") or {}
    action_status = action_decision.get("action_status")
    option_ticker = option_contract.get("ticker")
    return {
        "symbol": arguments.get("symbol"),
        "direction": option_contract.get("type"),
        "option_ticker": option_ticker,
        "dedupe_key": _scanner_entry_alert_key(
            arguments.get("symbol"),
            option_ticker,
            action_status,
            arguments.get("bar_timestamp")
        ),
        "payload": {
            "final_signal": arguments.get("final_signal"),
            "action_status": action_status,
            "bar_timestamp": arguments.get("bar_timestamp"),
            "top_candidate": arguments.get("top_candidate"),
            "market_session": arguments.get("market_session"),
            "option_quality_score": arguments.get("option_quality_score"),
            "option_spread_pct": arguments.get("option_spread_pct")
        }
    }


def _record_alert_attempt(alert_type, context, result=None, error=None):

    try:

        from app.background import run_background
        from app.db.persistence import record_alert_event

        result = result or {}
        status = "FAILED" if error else "SENT" if result.get("sent") else "SKIPPED"
        reason = str(error) if error else result.get("reason")
        payload = dict(context.get("payload") or {})
        payload["result"] = result
        dedupe_key = None

        if result.get("sent"):

            dedupe_key = result.get("alert_key") or context.get("dedupe_key")

        elif error:

            dedupe_key = context.get("dedupe_key")

        run_background(
            record_alert_event,
            alert_type=alert_type,
            symbol=context.get("symbol"),
            direction=context.get("direction"),
            option_ticker=context.get("option_ticker"),
            status=status,
            reason=reason,
            dedupe_key=dedupe_key,
            payload=payload
        )

    except Exception as exc:

        print(f"[DB ALERT EVENT WARNING] {exc}")


def _telegram_attempt_logger(alert_type):

    def decorator(func):

        func_signature = signature(func)

        @wraps(func)
        def wrapper(*args, **kwargs):

            bound = func_signature.bind_partial(*args, **kwargs)
            context = _alert_attempt_context(
                alert_type,
                bound.arguments
            )

            try:

                result = func(*args, **kwargs)
                _record_alert_attempt(
                    alert_type,
                    context,
                    result=result
                )
                return result

            except Exception as exc:

                _record_alert_attempt(
                    alert_type,
                    context,
                    error=exc
                )
                raise

        return wrapper

    return decorator


def _send_telegram_alert_direct(message):

    token, chat_id = get_telegram_credentials()

    if not token or not chat_id:

        raise ValueError("Telegram bot token/chat_id not configured")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    with measure_runtime(
        "telegram",
        "send_telegram_alert"
    ):

        response = requests.post(
            url,
            json=payload,
            timeout=10
        )
    try:

        response.raise_for_status()

    except requests.HTTPError as exc:

        try:

            response_body = response.json()

        except ValueError:

            response_body = response.text

        raise TelegramDeliveryError(
            response.status_code,
            response_body
        ) from None

    try:

        response_body = response.json()

    except ValueError:

        response_body = {}

    if isinstance(response_body, dict):

        result = response_body.get("result") or {}
        return {
            "telegram_message_id": result.get("message_id"),
            "telegram_response": response_body
        }

    return {}


def send_telegram_alert(message, after_success=None, scan_id=None, dispatch_metadata=None):

    return dispatch_telegram_message(
        _send_telegram_alert_direct,
        message,
        name="send_telegram_alert",
        scan_id=scan_id,
        after_success=after_success,
        dispatch_metadata=dispatch_metadata
    )


def _queued_send_result(result, alert_key):

    if isinstance(result, dict) and result.get("queued"):

        return {
            "sent": False,
            "queued": True,
            "reason": "QUEUED",
            "alert_key": alert_key,
            "job_id": result.get("job_id")
        }

    return {
        "sent": True,
        "reason": "SENT",
        "alert_key": alert_key
    }


def _load_alert_state():

    state = load_json_file(
        str(ALERT_STATE_FILE),
        {"sent": {}}
    )

    if not isinstance(state, dict):

        return {"sent": {}}

    if not isinstance(state.get("sent"), dict):

        state["sent"] = {}

    return state


def _save_alert_state(state):

    sent = state.get("sent", {})

    if len(sent) > MAX_SENT_ALERTS:

        ordered = sorted(
            sent.items(),
            key=lambda item: item[1].get("sent_at", "")
        )
        state["sent"] = dict(ordered[-MAX_SENT_ALERTS:])

    save_json_file(
        str(ALERT_STATE_FILE),
        state
    )


def alert_was_sent(alert_key):

    state = _load_alert_state()
    return alert_key in state.get("sent", {})


def mark_alert_sent(alert_key, metadata=None):

    state = _load_alert_state()
    state.setdefault("sent", {})[alert_key] = {
        "sent_at": datetime.now(timezone.utc).isoformat(),
        **(metadata or {})
    }
    _save_alert_state(state)


def mark_alert_closed(symbol, option_ticker=None):

    state = _load_alert_state()
    for metadata in state.get("sent", {}).values():

        if metadata.get("event_type") != ENTRY_EVENT_TYPE:

            continue

        if metadata.get("symbol") != symbol:

            continue

        if option_ticker and metadata.get("option_ticker") != option_ticker:

            continue

        metadata["closed"] = True
        metadata["closed_at"] = datetime.now(timezone.utc).isoformat()

    _save_alert_state(state)


def _paper_entry_alert_key(trade, scanner_context=None):

    trade = trade or {}
    scanner_context = scanner_context or {}
    lifecycle_id = _trade_lifecycle_id(trade)
    if lifecycle_id:
        return "|".join(["PAPER_ENTRY", lifecycle_id])

    symbol = trade.get("symbol") or scanner_context.get("Symbol")
    direction = trade.get("direction") or scanner_context.get("Candidate Direction")
    option_ticker = (
        trade.get("option_ticker")
        or scanner_context.get("Option Ticker")
        or "NO_CONTRACT"
    )
    opened_at = trade.get("opened_at") or datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    opened_date = str(opened_at).split(" ")[0]
    return "_".join([
        str(symbol),
        str(direction),
        str(option_ticker),
        "PAPER_ENTRY",
        opened_date
    ])


def _trade_open_alert_key(trade):

    return "|".join(["TRADE_OPEN", _trade_lifecycle_id(trade)])


def _review_alert_key(symbol, entry_type):

    return "_".join([
        str(symbol),
        str(entry_type or "NO_SETUP"),
        "REVIEW",
        str(_today_key())
    ])


def _state_key_for_alert(trade):

    return "_".join([
        str(trade.get("symbol")),
        str(trade.get("option_ticker") or "NO_CONTRACT"),
        str(trade.get("opened_at") or "NO_OPEN_TIME")
    ])


def _trade_lifecycle_id(trade):

    trade = trade or {}
    return str(
        trade.get("trade_id")
        or trade.get("trade_key")
        or _state_key_for_alert(trade)
    )


def _subscriber_entry_metadata(trade):

    lifecycle_id = _trade_lifecycle_id(trade)
    latest = None
    for metadata in _load_alert_state().get("sent", {}).values():
        if metadata.get("message_type") not in {"PAPER_ENTRY", "TRADE_OPEN"}:
            continue
        recorded_id = str(
            metadata.get("lifecycle_id")
            or metadata.get("trade_id")
            or metadata.get("trade_key")
            or ""
        )
        if recorded_id != lifecycle_id:
            continue
        if latest is None or metadata.get("sent_at", "") > latest.get("sent_at", ""):
            latest = metadata
    return latest or {}


def _trade_update_alert_key(trade, r_multiple, trend_health):

    return "|".join([
        "UPDATE",
        _trade_lifecycle_id(trade),
        str(round(_float_value(r_multiple), 2)),
        str(trend_health or "UNKNOWN")
    ])


def _scanner_entry_alert_key(symbol, option_ticker, action_status, bar_timestamp):

    return "_".join([
        str(symbol),
        str(option_ticker),
        str(action_status),
        str(bar_timestamp)
    ])


def _exit_alert_key(symbol, option_ticker, trade, exit_reason, event_type):

    return "|".join([
        str(event_type or "EXIT"),
        _trade_lifecycle_id(trade),
        str(exit_reason or "NO_REASON")
    ])


def _can_send_exit_alert(trade, event_type):

    trade = trade or {}
    trade_mode = str(
        trade.get("trade_mode") or ""
    ).upper()
    status = str(
        trade.get("status") or "OPEN"
    ).upper()

    if trade_mode not in EXIT_ALERT_TRADE_MODES:

        return False, "EXIT_ALERT_NOT_CONFIRMED_TRADE"

    if status not in {"OPEN", "CLOSED"}:

        return False, "TRADE_NOT_OPEN_OR_CLOSED"

    if event_type == "EXIT" and trade.get("exit_alert_sent"):

        return False, "EXIT_ALERT_ALREADY_SENT"

    if event_type == "PARTIAL_EXIT" and trade.get("partial_exit_alert_sent"):

        return False, "PARTIAL_EXIT_ALERT_ALREADY_SENT"

    return True, "ELIGIBLE"


def _sent_at_datetime(metadata):

    try:

        return datetime.fromisoformat(metadata.get("sent_at"))

    except Exception:

        return None


def _today_key():

    return _current_et().date()


def _sent_at_trading_date(metadata):

    sent_at = _sent_at_datetime(metadata)

    if not sent_at:

        return None

    try:

        return sent_at.astimezone(
            ZoneInfo("America/New_York")
        ).date()

    except Exception:

        return sent_at.date()


def _entry_alerts_today(state):

    today = _today_key()
    count = 0

    for metadata in state.get("sent", {}).values():

        if metadata.get("event_type") != ENTRY_EVENT_TYPE:

            continue

        sent_date = _sent_at_trading_date(metadata)

        if sent_date == today:

            count += 1

    return count


def _entry_alerts_in_bucket(state, bucket):

    today = _today_key()
    count = 0

    for metadata in state.get("sent", {}).values():

        if metadata.get("event_type") != ENTRY_EVENT_TYPE:

            continue

        if metadata.get("time_bucket") != bucket:

            continue

        sent_date = _sent_at_trading_date(metadata)

        if sent_date == today:

            count += 1

    return count


def _active_entry_alerts(state):

    return [
        metadata for metadata in state.get("sent", {}).values()
        if metadata.get("event_type") == ENTRY_EVENT_TYPE
        and not metadata.get("closed")
    ]


def _recent_matching_entry_alert(state, symbol, setup_key, cooldown_minutes):

    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=cooldown_minutes
    )

    for metadata in state.get("sent", {}).values():

        if metadata.get("event_type") != ENTRY_EVENT_TYPE:

            continue

        if metadata.get("symbol") != symbol:

            continue

        if metadata.get("setup_key") != setup_key:

            continue

        sent_at = _sent_at_datetime(metadata)

        if sent_at and sent_at >= cutoff:

            return True

    return False


def _recent_closed_symbol_alert(state, symbol, cooldown_minutes):

    cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=cooldown_minutes
    )

    for metadata in state.get("sent", {}).values():

        if metadata.get("event_type") != ENTRY_EVENT_TYPE:

            continue

        if metadata.get("symbol") != symbol:

            continue

        if not metadata.get("closed"):

            continue

        closed_at = None

        try:

            closed_at = datetime.fromisoformat(
                metadata.get("closed_at")
            )

        except Exception:

            closed_at = None

        if closed_at and closed_at >= cutoff:

            return True

    return False


def _top_candidate_allowed(top_candidate, limit):

    top_candidate = str(top_candidate or "").upper()

    if not top_candidate:

        return False

    for direction in [
        "BULLISH",
        "BEARISH"
    ]:

        for rank in range(1, limit + 1):

            if top_candidate == f"{direction}_TOP_{rank}":

                return True

    return False


def _current_et():

    return datetime.now(
        ZoneInfo("America/New_York")
    )


def _entry_alert_time_bucket(current_et=None):

    current_et = current_et or _current_et()
    minutes = current_et.hour * 60 + current_et.minute

    if minutes < 9 * 60 + 45:

        return "TOO_EARLY"

    if minutes < 10 * 60 + 30:

        return "MORNING"

    if minutes < 13 * 60 + 30:

        return "MIDDAY"

    if minutes < 14 * 60 + 45:

        return "AFTERNOON"

    return "TOO_LATE"


def calculate_entry_alert_score(
    setup_score=0,
    alignment_score=0,
    rs_rank_score=0,
    option_quality_score=0,
    risk_reward=0,
    relative_volume=0,
    option_spread_pct=None
):

    setup_component = min(
        abs(_float_value(setup_score)) / 10,
        1
    ) * 25
    alignment_component = min(
        abs(_float_value(alignment_score)) / 6,
        1
    ) * 15
    rs_component = min(
        abs(_float_value(rs_rank_score)) / 5,
        1
    ) * 10
    option_quality_component = min(
        _float_value(option_quality_score) / 100,
        1
    ) * 25
    rr_component = min(
        _float_value(risk_reward) / 2.5,
        1
    ) * 15
    volume_component = min(
        _float_value(relative_volume) / 1,
        1
    ) * 10

    spread = _float_value(option_spread_pct, None)
    spread_penalty = 0

    if spread is not None and spread > 5:

        spread_penalty = min(
            (spread - 5) * 2,
            15
        )

    return round(
        max(
            0,
            setup_component
            + alignment_component
            + rs_component
            + option_quality_component
            + rr_component
            + volume_component
            - spread_penalty
        ),
        2
    )


def _fmt(value, default="-"):

    if value is None:

        return default

    return html.escape(str(value))


def _number(value, default=None):

    try:

        return float(value)

    except (TypeError, ValueError):

        return default


def _money(value):

    amount = _number(value)
    return "-" if amount is None else f"${amount:,.2f}"


def _score_stars(value):

    score = _number(value)
    if score is None:

        return ""

    filled = max(1, min(5, round(score / 20)))
    return "★" * filled + "☆" * (5 - filled)


def _confidence_label(value):

    score = _number(value)
    if score is None:

        return None

    if score >= 90:

        return "★★★★★ Excellent"

    if score >= 80:

        return "★★★★☆ High"

    if score >= 70:

        return "★★★☆☆ Medium"

    if score >= 60:

        return "★★☆☆☆ Low"

    return "★☆☆☆☆ Weak"


def _trade_stage_label(scanner_context):

    pullback = _number(scanner_context.get("V2 Pullback Number"), None)
    if pullback == 1:

        return "First Pullback"

    if pullback == 2:

        return "Second Pullback"

    if pullback is not None and pullback >= 3:

        return "Late Trend"

    health = str(scanner_context.get("V2 Trend Health Status") or "").upper()
    if health in {"STRONG", "HEALTHY"}:

        return "Fresh Trend"

    return None


def _trade_quality_grade(score, projected_grade=None):

    numeric = _number(score)
    if numeric is None:

        return projected_grade or ""

    if numeric >= 90:

        return "A+"

    if numeric >= 80:

        return "A"

    if numeric >= 70:

        return "B"

    return "C"


def _format_expected_hold(value):

    text = str(value or "").strip()
    if not text:

        return None

    normalized = text.lower()
    if normalized in {"intraday", "daytrade"}:

        return None

    if "min" in normalized or "hr" in normalized or "hour" in normalized:

        return text

    if normalized == "scalp":

        return "Scalp"

    if normalized == "momentum":

        return "Momentum"

    if normalized == "swing intraday":

        return "Swing Intraday"

    return None


def _format_trade_reference(trade_key, entry=True):

    trade_id = str(trade_key or "").strip()
    prefix = "Signal" if entry else "Trade"
    if not trade_id:

        return prefix

    digits = re.findall(r"\d+", trade_id)
    if digits:

        return f"{prefix} #{digits[-1]}"

    return f"{prefix} {trade_id}"


def _parse_datetime(value):

    if not value:

        return None

    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:

            return datetime.strptime(text, fmt)

        except Exception:

            continue

    try:

        return datetime.fromisoformat(text)

    except Exception:

        return None


def _format_event_time(timestamp):

    dt = _parse_datetime(timestamp)
    if dt is None:

        return None

    if dt.tzinfo is None:

        dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))

    try:

        dt = dt.astimezone(ZoneInfo("America/New_York"))

    except Exception:

        pass

    return dt.strftime("%H:%M ET")


def _format_alert_timestamp(timestamp=None):

    dt = _parse_datetime(timestamp) or datetime.now(ZoneInfo("America/New_York"))
    if dt.tzinfo is None:

        dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))

    return dt.astimezone(ZoneInfo("America/New_York")).strftime("%b %d, %Y · %H:%M ET")


def _holding_time_label(opened_at, event_timestamp=None):

    start = _parse_datetime(opened_at)
    end = _parse_datetime(event_timestamp)

    if not start or not end:

        return None

    if start.tzinfo is None:

        start = start.replace(tzinfo=timezone.utc)

    if end.tzinfo is None:

        end = end.replace(tzinfo=timezone.utc)

    delta = end - start
    minutes = int(delta.total_seconds() // 60)

    if minutes < 0:
        return None

    if minutes < 60:

        return f"Holding Time: {minutes} min"

    hours = minutes // 60
    remainder = minutes % 60
    return f"Holding Time: {hours}h {remainder}m" if remainder else f"Holding Time: {hours}h"


def _signed_money(value):

    amount = _number(value)
    if amount is None:

        return "-"

    return f"{'+' if amount > 0 else ''}${amount:,.2f}"


def _exit_reason_label(exit_reason):

    normalized = str(exit_reason or "").upper()
    if "TARGET" in normalized:

        return "🟩 Target Hit"

    if "STOP" in normalized:

        return "🟥 Stop Loss"

    if "FAILED BREAKOUT" in normalized or "FAILED_BREAKOUT" in normalized:

        return "⚠️ Failed Breakout"

    if "EMA" in normalized:

        return "🟨 EMA Exit"

    if "VWAP" in normalized:

        return "🟦 VWAP Exit"

    if (
        "TIME" in normalized
        or "END_OF_DAY" in normalized
        or "END-OF-DAY" in normalized
        or "END OF DAY" in normalized
        or "NEAR_CLOSE" in normalized
        or "NEAR-CLOSE" in normalized
        or "NEAR CLOSE" in normalized
    ):

        return "🟪 Time Exit"

    if "MANUAL" in normalized:

        return "📈 Manual Exit"

    if "PROFIT" in normalized:

        return "📊 Profit Lock"

    if "MARKET" in normalized:

        return "⚠ Market Weakness"

    if any(token in normalized for token in ("TREND", "MOMENTUM", "BREAKDOWN")):

        return "📉 Trend Failure"

    return str(exit_reason or "Exit signal").replace("_", " ").title()


def _trade_status_line(event_type):

    if event_type in {ENTRY_EVENT_TYPE, "TRADE_OPEN", UPDATE_EVENT_TYPE, "STOP_MOVED"}:

        return "Trade Status: 🟢 Open"

    if event_type in {"PARTIAL", "PARTIAL_EXIT"}:

        return "Trade Status: 🟡 Partial"

    return "Trade Status: 🔴 Closed"


def _reason_checklist(reasons):

    if not reasons:

        return ["✅ Confirmed setup"]

    return [f"✅ {reason}" for reason in reasons if reason]


def _execution_label(trend_capture_pct):

    pct = _number(trend_capture_pct)
    if pct is None:

        return None

    if pct >= 85:

        return "Excellent"

    if pct >= 70:

        return "Good"

    if pct >= 50:

        return "Average"

    return "Poor"


def _trade_update_reason(event_type, r_multiple, trend_health, updated_stop, partial_profit_taken):

    if event_type == "PARTIAL":

        return "Partial profit taken"

    if event_type == "STOP_MOVED":

        if r_multiple is not None and r_multiple >= 1:

            return "Higher low confirmed"

        return "Higher low confirmed"

    if updated_stop is not None:

        return "Stop moved"

    return "Hold and monitor"


def _risk_change_label(trade, updated_stop):

    old_stop = _float_value(trade.get("stop_loss"), None)
    new_stop = _float_value(updated_stop, None)
    if old_stop is None or new_stop is None:

        return None

    is_short = str(trade.get("direction") or "").upper() in {"PUT", "SHORT"}

    if is_short:

        if new_stop < old_stop:

            return "Reduced"

        if new_stop > old_stop:

            return "Wider"

    else:

        if new_stop > old_stop:

            return "Reduced"

        if new_stop < old_stop:

            return "Wider"

    return "Updated"


def _option_cost(scanner_context, option_mid):

    cost = _number(scanner_context.get("Option Contract Cost"))
    if cost is not None:

        return cost

    premium = _number(option_mid)
    return premium * 100 if premium is not None else None


def _option_lifecycle_lines(trade, scanner_context=None):

    trade = trade or {}
    scanner_context = scanner_context or trade.get("scanner_context") or {}
    direction = trade.get("direction") or scanner_context.get("Candidate Direction")
    contract_type = "P" if str(direction or "").upper() in {"PUT", "SHORT"} else "C"
    option_strike = trade.get("option_strike") or scanner_context.get("Option Strike")
    option_ticker = trade.get("option_ticker") or scanner_context.get("Option Ticker")
    contract = (
        f"{_fmt(option_strike)}{contract_type}"
        if option_strike is not None
        else _fmt(option_ticker)
    )
    expiration = trade.get("option_expiration") or scanner_context.get("Option Expiration")
    option_mid = (
        trade.get("option_entry_mid")
        or trade.get("option_mid")
        or scanner_context.get("Option Mid Price")
        or scanner_context.get("Option Midpoint")
    )
    option_cost = _option_cost(scanner_context, option_mid)

    return [
        "<b>OPTION</b>",
        f"Contract: {contract}",
        f"Expiry: {_fmt(expiration)}",
        f"Contract Cost: {_money(option_cost)}",
    ]


def _entry_reasons(scanner_context, setup):

    setup_label = str(setup).replace("_", " ").title()
    setup_label = setup_label.replace("Ema", "EMA").replace("Vwap", "VWAP")
    reasons = [setup_label]
    health = str(scanner_context.get("V2 Trend Health Status") or "").upper()
    if health in {"STRONG", "HEALTHY"}:

        reasons.append(f"{health.title()} trend")

    if _number(scanner_context.get("Relative Volume"), 0) >= 1:

        reasons.append("Volume confirmed")

    if abs(_number(scanner_context.get("RS Rank Score"), 0)) > 0:

        reasons.append("Relative strength confirmed")

    if _number(scanner_context.get("V2 Trend Age Bars")) == 1:

        reasons.append("First pullback")

    return reasons[:5]


def build_scanner_entry_alert_message(
    symbol,
    final_signal,
    action_status,
    entry_setup,
    risk_setup,
    option_contract,
    latest_price,
    next_condition,
    alert_score=None
):

    direction = _fmt(option_contract.get("type", "OPTION")).upper()
    option_ticker = _fmt(option_contract.get("ticker"))
    cost = _fmt(option_contract.get("contract_cost"))
    risk_at_stop = _fmt(option_contract.get("risk_at_stop"))
    affordability = _fmt(option_contract.get("affordability_status"))

    return "\n".join([
        f"<b>ENTRY ALERT - {direction}</b>",
        f"Ticker: {_fmt(symbol)}",
        f"Signal: {_fmt(final_signal)}",
        f"Action: {_fmt(action_status)}",
        f"Price: {_fmt(latest_price)}",
        f"Setup: {_fmt(entry_setup.get('entry_type'))}",
        f"RR: {_fmt(risk_setup.get('risk_reward'))}",
        f"Contract: {option_ticker}",
        f"Expiry: {_fmt(option_contract.get('expiration'))}",
        f"Contract Cost: ${cost}",
        f"Risk At Stop: ${risk_at_stop}",
        f"Affordability: {affordability}",
        f"Spread %: {_fmt(option_contract.get('spread_pct'))}",
        f"Quality: {_fmt(option_contract.get('option_quality_score'))}",
        f"Quote: {_fmt(option_contract.get('quote_freshness'))}",
        f"Next: {_fmt(next_condition)}",
        "Skip if broker bid/ask, spread, or chart confirmation disagrees."
    ])


def build_paper_entry_alert_message(trade, scanner_context, reason=None):

    scanner_context = scanner_context or {}
    symbol = trade.get("symbol") or scanner_context.get("Symbol")
    direction = trade.get("direction") or scanner_context.get("Candidate Direction")
    option_ticker = trade.get("option_ticker") or scanner_context.get("Option Ticker")
    option_mid = (
        trade.get("option_mid")
        or scanner_context.get("Option Mid Price")
        or scanner_context.get("Option Midpoint")
    )

    side = "SELL" if str(direction or "").upper() in {"PUT", "SHORT"} else "BUY"
    setup = trade.get("entry_type") or scanner_context.get("Entry")
    trade_quality = scanner_context.get("Trade Quality Score")
    confidence = (
        scanner_context.get("Expected Remaining Trend")
        or scanner_context.get("V2 Trend Health Score")
    )
    reasons = _entry_reasons(scanner_context, setup)
    checklist = _reason_checklist(reasons)
    quality_grade = _trade_quality_grade(
        trade_quality,
        scanner_context.get("Projected Entry Grade")
    )
    quality_line = quality_grade
    confidence_line = _confidence_label(confidence)
    stage_line = _trade_stage_label(scanner_context)
    expected_hold_line = _format_expected_hold(scanner_context.get("Expected Hold"))
    trade_ref = _format_trade_reference(trade.get("trade_key") or scanner_context.get("Candidate Key") or symbol, entry=True)

    lines = [
        "🟢 <b>NEW TRADE</b>",
        f"{trade_ref}",
        _format_alert_timestamp(trade.get("opened_at") or scanner_context.get("Signal Time") or scanner_context.get("Bar Timestamp")),
        f"{_fmt(symbol)} {_fmt(direction)}",
        "",
        "<b>ACTION</b>",
        f"<b>{side} NOW</b>",
        "",
        "<b>TRADE</b>",
        f"Entry: {_money(trade.get('entry_price'))}",
        f"Stop: {_money(trade.get('stop_loss'))}",
        f"Target: {_money(trade.get('take_profit'))}",
        f"RR: {_fmt(trade.get('planned_rr') or scanner_context.get('Candidate RR') or scanner_context.get('RR'))}R",
        "",
        "<b>SETUP</b>",
        _fmt(str(setup or "Confirmed setup").replace("_", " ").title().replace("Ema", "EMA").replace("Vwap", "VWAP")),
        f"{_score_stars(confidence)} {confidence_line}" if confidence_line else None,
        "",
        *_option_lifecycle_lines(trade, scanner_context),
        f"Premium: {_money(option_mid)}",
        "",
        _trade_status_line(ENTRY_EVENT_TYPE),
    ]

    return "\n".join([line for line in lines if line])


def build_review_alert_message(symbol, entry_type, next_condition):

    return "\n".join([
        "⚪ <b>WATCHLIST REVIEW</b>",
        f"Ticker: {_fmt(symbol)}",
        f"Setup: {_fmt(entry_type)}",
        f"Required confirmation: {_fmt(next_condition)}",
        "Status: Waiting for entry readiness. No action yet."
    ])


def _trade_r_multiple(trade, current_price):

    entry = _float_value(trade.get("entry_price"), None)
    stop = _float_value(trade.get("stop_loss"), None)
    price = _float_value(current_price, None)
    if entry is None or stop is None or price is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    is_short = str(trade.get("direction") or "").upper() in {"PUT", "SHORT"}
    return round((entry - price if is_short else price - entry) / risk, 2)


def _trend_health_rank(status):

    return {
        "STRONG": 4,
        "HEALTHY": 3,
        "WEAKENING": 2,
        "BROKEN": 1,
    }.get(str(status or "").upper(), 0)


def _last_trade_lifecycle_metadata(trade):

    lifecycle_id = _trade_lifecycle_id(trade)
    latest = None
    for metadata in _load_alert_state().get("sent", {}).values():
        recorded_id = str(
            metadata.get("lifecycle_id")
            or metadata.get("trade_id")
            or metadata.get("trade_key")
            or ""
        )
        if recorded_id != lifecycle_id:
            continue
        if metadata.get("message_type") not in {
            "PAPER_ENTRY",
            "TRADE_OPEN",
            "TRADE_UPDATE",
        }:
            continue
        if latest is None or metadata.get("sent_at", "") > latest.get("sent_at", ""):
            latest = metadata
    return latest or {}


def build_paper_trade_update_message(
    trade,
    r_multiple,
    trend_health,
    updated_stop=None,
    partial_profit_taken=False,
    confidence_score=None,
    event_type="UPDATE",
    event_timestamp=None,
):

    title = (
        "PARTIAL PROFIT"
        if event_type == "PARTIAL"
        else "TRADE UPDATE"
    )

    reason_line = _trade_update_reason(event_type, r_multiple, trend_health, updated_stop, partial_profit_taken)
    risk_label = _risk_change_label(trade, updated_stop)
    stop_line = None
    if updated_stop is not None:
        if trade.get("stop_loss") is not None:
            stop_line = f"Stop: {_money(trade.get('stop_loss'))} → {_money(updated_stop)}"
        else:
            stop_line = f"Stop: {_money(updated_stop)}"

    if event_type == "PARTIAL":
        partial_stop = (
            "Moved to Breakeven"
            if updated_stop is not None
            and _float_value(updated_stop) == _float_value(trade.get("entry_price"))
            else stop_line
        )
        lines = [
            "🟡 <b>PARTIAL PROFIT</b>",
            _format_alert_timestamp(event_timestamp),
            f"<b>{_fmt(trade.get('symbol'))} {_fmt(trade.get('direction'))}</b>",
            "",
            f"Result: {_fmt(r_multiple)}R",
            "Position: Partial closed",
            "Runner: Still Open",
            f"Stop: {partial_stop}" if partial_stop else None,
            "",
            *_option_lifecycle_lines(trade),
            "",
            _trade_status_line(event_type),
        ]
        return "\n".join([line for line in lines if line])

    lines = [
        "🔵 <b>TRADE UPDATE</b>",
        _format_alert_timestamp(event_timestamp),
        f"<b>{_fmt(trade.get('symbol'))} {_fmt(trade.get('direction'))}</b>",
        "",
        f"Current: {_fmt(r_multiple)}R",
        f"Risk: {risk_label}" if risk_label else None,
        stop_line,
        f"Reason: {reason_line}",
        "Action: Continue Holding",
        f"Trend: {_fmt(trend_health)}" if trend_health else None,
        "",
        *_option_lifecycle_lines(trade),
        "",
        _trade_status_line(event_type),
    ]

    return "\n".join([line for line in lines if line])


def build_multiday_position_continue_message(trade, current_price, trend_health, event_timestamp=None):

    r_multiple = _trade_r_multiple(trade, current_price)
    days_held = int(_number(trade.get("days_held"), 1) or 1)
    opened_dt = _parse_datetime(trade.get("opened_at_et") or trade.get("opened_at"))
    if opened_dt is not None:
        if opened_dt.tzinfo is None:
            opened_dt = opened_dt.replace(tzinfo=ZoneInfo("America/New_York"))
        opened_label = opened_dt.astimezone(ZoneInfo("America/New_York")).strftime("%d %b")
    else:
        opened_label = "Previous session"

    return "\n".join([
        "🌅 <b>POSITION CONTINUES</b>",
        _format_alert_timestamp(event_timestamp),
        f"<b>{_fmt(trade.get('symbol'))} {_fmt(trade.get('direction'))}</b>",
        "",
        f"Opened: {opened_label}",
        f"Current: {_fmt(r_multiple)}R",
        f"Holding: Day {days_held}",
        f"Trend: {_fmt(trend_health or 'UNKNOWN')}",
        "Action: Continue Holding",
        "",
        *_option_lifecycle_lines(trade),
        "",
        "Trade Status: 🟢 Open",
    ])


@_telegram_attempt_logger("POSITION_CONTINUES")
def maybe_send_multiday_position_continue_alert(trade, current_price, scanner_context=None):

    from app.state.holding_policy import holding_policy
    from app.state.paper_trade_manager import load_paper_trades, save_paper_trades

    trade = trade or {}
    if not telegram_entry_alerts_enabled():
        return {"sent": False, "reason": "TELEGRAM_ENTRY_ALERTS_DISABLED"}
    if str(trade.get("status") or "").upper() != "OPEN":
        return {"sent": False, "reason": "TRADE_NOT_OPEN"}
    if not holding_policy(trade.get("holding_profile")).telegram_resume:
        return {"sent": False, "reason": "NOT_MULTIDAY"}
    if not trade.get("overnight_transition"):
        return {"sent": False, "reason": "NO_OVERNIGHT_TRANSITION"}

    scanner_context = scanner_context or {}
    trend_health = (
        scanner_context.get("V2 Trend Health Status")
        or scanner_context.get("Trend Health State")
        or "UNKNOWN"
    )
    alert_key = "|".join([
        "POSITION_CONTINUES",
        str(trade.get("trade_key") or _state_key_for_alert(trade)),
        str(trade.get("session_id_current") or ""),
    ])
    if alert_was_sent(alert_key):
        return {"sent": False, "reason": "DUPLICATE_ALERT", "alert_key": alert_key}

    metadata = {
        "symbol": trade.get("symbol"),
        "direction": trade.get("direction"),
        "option_ticker": trade.get("option_ticker"),
        "event_type": "POSITION_CONTINUES",
        "message_type": "POSITION_CONTINUES",
        "candidate_key": trade.get("trade_key") or alert_key,
        "trade_id": trade.get("trade_id"),
        "trade_key": trade.get("trade_key") or _state_key_for_alert(trade),
        "lifecycle_id": _trade_lifecycle_id(trade),
        "closed": False,
    }

    def mark_continuation_sent(_result):
        mark_alert_sent(alert_key, metadata)
        state = load_paper_trades()
        state_key = trade.get("trade_key") or _state_key_for_alert(trade)
        if state_key in state:
            state[state_key]["overnight_transition"] = False
            save_paper_trades(state)

    send_result = send_telegram_alert(
        build_multiday_position_continue_message(trade, current_price, trend_health),
        after_success=mark_continuation_sent,
        scan_id=trade.get("scan_id") or scanner_context.get("Scan ID"),
        dispatch_metadata={"alert_key": alert_key, "metadata": metadata},
    )
    return _queued_send_result(send_result, alert_key)


def build_telegram_rule_evaluations(scanner_context, result, scan_id, symbol, setup=None):
    from app.gates.rule_evaluation import RuleEvaluation

    result = result or {}
    reason = result.get("reason")
    sent = bool(result.get("sent"))
    eligible = sent or reason == "ELIGIBLE"
    return [
        # blocked_trade is always False: Telegram is a delivery transport, not an
        # entry gate. A failed send is not a trade-decision failure.
        RuleEvaluation(scan_id, symbol, setup, "Telegram Eligibility", "Telegram", reason, "ELIGIBLE", eligible, False, 60)
    ]


@_telegram_attempt_logger("PAPER_ENTRY")
def maybe_send_paper_entry_alert(
    trade,
    scanner_context=None,
    reason=None,
    scan_id=None
):

    if not telegram_entry_alerts_enabled():

        return {
            "sent": False,
            "reason": "TELEGRAM_ENTRY_ALERTS_DISABLED"
        }

    trade = trade or {}
    scanner_context = scanner_context or trade.get("scanner_context") or {}
    symbol = trade.get("symbol") or scanner_context.get("Symbol")
    direction = trade.get("direction") or scanner_context.get("Candidate Direction")
    option_ticker = trade.get("option_ticker") or scanner_context.get("Option Ticker") or "NO_CONTRACT"
    action_status = str(scanner_context.get("Action Status") or "").upper()

    if action_status not in ["ENTER", "ENTER_PAPER"]:

        return {
            "sent": False,
            "reason": "ACTION_NOT_ALERTABLE"
        }

    if not _bool_value(scanner_context.get("Realtime Ready")):

        return {
            "sent": False,
            "reason": "REALTIME_NOT_READY"
        }

    policy = _entry_alert_policy()
    min_setup = _float_setting(
        "TELEGRAM_MIN_PAPER_ENTRY_SETUP_SCORE",
        70.0
    )

    gate_allowed, gate_reason = evaluate_entry_gate(
        scanner_context,
        EntryGateConfig(
            min_rr=policy["min_rr"],
            min_setup_percent=min_setup,
            min_option_quality=policy["min_option_quality"],
            max_spread_pct=policy["max_spread_pct"]
        ),
        mode="telegram"
    )

    if not gate_allowed:

        return {
            "sent": False,
            "reason": gate_reason
        }

    if _bool_value(scanner_context.get("Event Blocked")):

        return {
            "sent": False,
            "reason": "EVENT_BLOCKED"
        }

    if _bool_value(scanner_context.get("Regime Blocked")):

        return {
            "sent": False,
            "reason": "REGIME_BLOCKED"
        }

    opened_at = trade.get("opened_at") or datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    trade["opened_at"] = opened_at
    alert_key = _paper_entry_alert_key(
        trade,
        scanner_context
    )

    if alert_was_sent(alert_key):

        return {
            "sent": False,
            "reason": "DUPLICATE_ALERT",
            "alert_key": alert_key
        }

    message = build_paper_entry_alert_message(
        trade,
        scanner_context,
        reason=reason
    )
    metadata = {
        "symbol": symbol,
        "direction": direction,
        "option_ticker": option_ticker,
        "event_type": ENTRY_EVENT_TYPE,
        "message_type": "PAPER_ENTRY",
        "decision": action_status,
        "candidate_key": trade.get("trade_key") or alert_key,
        "source": "paper_entry",
        "action_status": action_status,
        "trade_id": trade.get("trade_id"),
        "trade_key": trade.get("trade_key") or _state_key_for_alert(trade),
        "lifecycle_id": _trade_lifecycle_id(trade),
        "last_r_multiple": 0.0,
        "last_trend_health": scanner_context.get("V2 Trend Health Status"),
        "setup_key": "_".join([
            str(symbol),
            str(direction),
            str(trade.get("entry_type"))
        ]),
        "closed": False
    }
    send_result = send_telegram_alert(
        message,
        after_success=lambda _result: mark_alert_sent(alert_key, metadata),
        scan_id=(
            scan_id
            or trade.get("scan_id")
            or scanner_context.get("Scan ID")
        ),
        dispatch_metadata={
            "alert_key": alert_key,
            "metadata": metadata,
        }
    )

    return _queued_send_result(send_result, alert_key)


@_telegram_attempt_logger("TRADE_UPDATE")
def maybe_send_paper_trade_update_alert(
    trade,
    current_price,
    scanner_context=None,
    updated_stop=None,
    partial_profit_taken=False,
    confidence_score=None
):

    if not telegram_entry_alerts_enabled():

        return {"sent": False, "reason": "TELEGRAM_ENTRY_ALERTS_DISABLED"}

    trade = trade or {}
    if str(trade.get("status") or "").upper() != "OPEN":

        return {"sent": False, "reason": "TRADE_NOT_OPEN"}

    if not _subscriber_entry_metadata(trade):

        return {"sent": False, "reason": "SUBSCRIBER_NEW_TRADE_NOT_SENT"}

    r_multiple = _trade_r_multiple(trade, current_price)
    if r_multiple is None:

        return {"sent": False, "reason": "R_MULTIPLE_UNAVAILABLE"}

    scanner_context = scanner_context or {}
    trend_health = (
        scanner_context.get("V2 Trend Health Status")
        or scanner_context.get("Trend Health State")
        or "UNKNOWN"
    )
    previous = _last_trade_lifecycle_metadata(trade)
    previous_r = _float_value(previous.get("last_r_multiple"), 0.0)
    previous_health = previous.get("last_trend_health")
    previous_stop = _float_value(previous.get("last_stop"), None)
    previous_confidence = _float_value(previous.get("last_confidence"), None)
    material_r_move = abs(r_multiple - previous_r) >= 0.5
    health_deteriorated = (
        _trend_health_rank(trend_health) < _trend_health_rank(previous_health)
    )
    stop_moved = (
        updated_stop is not None
        and previous_stop is not None
        and _float_value(updated_stop) != previous_stop
    )
    partial_taken = partial_profit_taken and not bool(
        previous.get("partial_profit_taken")
    )
    confidence_changed = (
        confidence_score is not None
        and previous_confidence is not None
        and abs(_float_value(confidence_score) - previous_confidence) >= 10
    )
    if not any([
        material_r_move,
        health_deteriorated,
        stop_moved,
        partial_taken,
        confidence_changed,
    ]):

        return {"sent": False, "reason": "NO_MATERIAL_TRADE_CHANGE"}

    event_type = (
        "PARTIAL"
        if partial_taken
        else "STOP_MOVED"
        if stop_moved
        else "UPDATE"
    )
    alert_key = "|".join([
        _trade_update_alert_key(trade, r_multiple, trend_health),
        event_type,
        str(updated_stop),
        str(confidence_score),
    ])
    if alert_was_sent(alert_key):

        return {
            "sent": False,
            "reason": "DUPLICATE_ALERT",
            "alert_key": alert_key
        }

    metadata = {
        "symbol": trade.get("symbol"),
        "direction": trade.get("direction"),
        "option_ticker": trade.get("option_ticker"),
        "event_type": UPDATE_EVENT_TYPE,
        "message_type": "TRADE_UPDATE",
        "decision": "HOLD",
        "candidate_key": trade.get("trade_key") or alert_key,
        "trade_key": trade.get("trade_key") or _state_key_for_alert(trade),
        "last_r_multiple": r_multiple,
        "last_trend_health": trend_health,
        "last_stop": updated_stop,
        "partial_profit_taken": bool(partial_profit_taken),
        "last_confidence": confidence_score,
        "lifecycle_event": event_type,
    }
    send_result = send_telegram_alert(
        build_paper_trade_update_message(
            trade,
            r_multiple,
            trend_health,
            updated_stop=updated_stop,
            partial_profit_taken=partial_profit_taken,
            confidence_score=confidence_score,
            event_type=event_type,
        ),
        after_success=lambda _result: mark_alert_sent(alert_key, metadata),
        scan_id=trade.get("scan_id") or scanner_context.get("Scan ID"),
        dispatch_metadata={"alert_key": alert_key, "metadata": metadata},
    )
    return _queued_send_result(send_result, alert_key)


def build_trade_exit_alert_message(
    symbol,
    trade,
    exit_reason,
    current_price=None,
    option_current_mid=None,
    pnl_pct=None,
    r_multiple=None,
    outcome=None,
    event_type="EXIT",
    expected_underlying_price=None,
    price_source=None,
    trend_capture_pct=None,
    mfe_r=None,
    event_timestamp=None,
):

    direction = trade.get("direction") or ""
    entry_price = _money(trade.get("entry_price"))
    option_entry_mid = _number(
        trade.get("option_entry_mid")
        or trade.get("option_mid")
    )
    option_current_mid = _number(option_current_mid)
    contracts = max(1, int(_number(trade.get("option_contracts"), 1)))
    option_pnl = (
        (option_current_mid - option_entry_mid) * contracts * 100
        if option_entry_mid is not None and option_current_mid is not None
        else None
    )
    final_r = _number(r_multiple)
    is_win = final_r is not None and final_r > 0
    result_label = "✅ WIN" if is_win else "Loss" if final_r is not None and final_r < 0 else "Closed"
    execution_label = _execution_label(trend_capture_pct)
    status_line = _trade_status_line(event_type)
    holding_time = _holding_time_label(
        trade.get("opened_at_et") or trade.get("opened_at"),
        event_timestamp,
    )

    if event_type == "PARTIAL_EXIT":
        return "\n".join([line for line in [
            "🟡 <b>PARTIAL PROFIT</b>",
            _format_alert_timestamp(event_timestamp),
            f"<b>{_fmt(symbol)} {_fmt(direction)}</b>",
            "",
            f"Result: {_fmt(r_multiple)}R",
            "Position: Partial closed",
            "Runner: Still Open",
            "",
            *_option_lifecycle_lines(trade),
            "",
            status_line,
        ] if line])

    lines = [
        "🔴 <b>TRADE CLOSED</b>",
        _format_alert_timestamp(event_timestamp),
        f"<b>{_fmt(symbol)} {_fmt(direction)}</b>",
        "",
        "<b>RESULT</b>",
        result_label,
        f"{_fmt(r_multiple)}R",
        f"Option P/L: {_signed_money(option_pnl)}",
        "",
        *_option_lifecycle_lines(trade),
        "",
        f"Reason: {_exit_reason_label(exit_reason)}",
        "",
        f"Execution: {execution_label}" if execution_label else None,
        f"Trend Capture: {_fmt(trend_capture_pct)}%" if trend_capture_pct is not None else None,
        holding_time,
        "Risk Managed: According to Plan" if final_r is not None and final_r < 0 else None,
        "",
        status_line,
        "Next: Monitoring for the next qualified setup.",
    ]

    return "\n".join([line for line in lines if line])


def _trend_capture_pct(r_multiple, mfe_r):

    final_r = _float_value(r_multiple, None)
    maximum_r = _float_value(mfe_r, None)
    if final_r is None or maximum_r is None or maximum_r <= 0:

        return None

    return round(max(0, min(100, final_r / maximum_r * 100)), 1)


@_telegram_attempt_logger("TRADE_EXIT")
def maybe_send_trade_exit_alert(
    symbol,
    trade,
    exit_reason,
    current_price=None,
    option_current_mid=None,
    pnl_pct=None,
    r_multiple=None,
    outcome=None,
    event_type="EXIT",
    event_timestamp=None,
    expected_underlying_price=None,
    price_source=None,
    scanner_row_symbol=None,
    candidate_prices=None,
    scan_id=None,
    mfe_r=None,
    trend_capture_pct=None
):

    if not telegram_exit_alerts_enabled():

        return {
            "sent": False,
            "reason": "TELEGRAM_EXIT_ALERTS_DISABLED"
        }

    trade = trade or {}
    option_ticker = (
        trade.get("option_ticker")
        or trade.get("ticker")
        or "NO_CONTRACT"
    )

    can_send, guard_reason = _can_send_exit_alert(
        trade,
        event_type
    )

    if not can_send:

        return {
            "sent": False,
            "reason": guard_reason
        }

    if not _subscriber_entry_metadata(trade):

        return {
            "sent": False,
            "reason": "SUBSCRIBER_NEW_TRADE_NOT_SENT"
        }

    trade_symbol = trade.get("symbol") or symbol

    if scanner_row_symbol and str(scanner_row_symbol) != str(symbol):

        return {
            "sent": False,
            "reason": "SCANNER_ROW_SYMBOL_MISMATCH",
            "trade_symbol": trade_symbol,
            "scanner_row_symbol": scanner_row_symbol
        }

    resolved_price_context = resolve_exit_price_context(
        candidate_prices=candidate_prices,
        fallback_current_price=current_price,
        fallback_price_source=price_source
    )

    observed_price = _float_value(
        resolved_price_context.get("current_price"),
        None
    )
    expected_price = _float_value(
        expected_underlying_price,
        None
    )
    if expected_price is None and observed_price is not None:

        expected_price = observed_price

    price_source = resolved_price_context.get("price_source") or price_source
    current_price = observed_price
    expected_underlying_price = expected_price

    if expected_price and observed_price:

        mismatch_pct = abs(observed_price - expected_price) / expected_price

        if mismatch_pct > _exit_price_mismatch_limit():

            return {
                "sent": False,
                "reason": "UNDERLYING_PRICE_MISMATCH",
                "symbol": symbol,
                "current_price": observed_price,
                "expected_underlying_price": expected_price,
                "mismatch_pct": round(mismatch_pct, 4),
                "price_source": price_source
            }

    alert_key = _exit_alert_key(
        symbol,
        option_ticker,
        trade,
        exit_reason,
        event_type
    )

    if alert_was_sent(alert_key):

        return {
            "sent": False,
            "reason": "DUPLICATE_ALERT",
            "alert_key": alert_key
        }

    trend_capture_pct = (
        trend_capture_pct
        if trend_capture_pct is not None
        else _trend_capture_pct(r_multiple, mfe_r)
    )

    message = build_trade_exit_alert_message(
        symbol=symbol,
        trade=trade,
        exit_reason=exit_reason,
        current_price=current_price,
        option_current_mid=option_current_mid,
        pnl_pct=pnl_pct,
        r_multiple=r_multiple,
        outcome=outcome,
        event_type=event_type,
        expected_underlying_price=expected_underlying_price,
        price_source=price_source,
        trend_capture_pct=trend_capture_pct,
        mfe_r=mfe_r,
        event_timestamp=event_timestamp or trade.get("closed_at") or datetime.now(ZoneInfo("America/New_York")),
    )

    def after_success(_result):

        if event_type == "EXIT":

            mark_alert_closed(
                symbol,
                option_ticker
            )
            trade["exit_alert_sent"] = True
            trade["exit_alert_sent_at"] = datetime.now(timezone.utc).isoformat()

        if event_type == "PARTIAL_EXIT":

            trade["partial_exit_alert_sent"] = True
            trade["partial_exit_alert_sent_at"] = datetime.now(timezone.utc).isoformat()

        mark_alert_sent(alert_key, metadata)

    metadata = {
        "symbol": symbol,
        "direction": trade.get("direction"),
        "option_ticker": option_ticker,
        "event_type": event_type,
        "message_type": "TRADE_EXIT",
        "decision": "EXIT_ELIGIBLE",
        "candidate_key": trade.get("trade_key") or alert_key,
        "trade_id": trade.get("trade_id"),
        "trade_key": trade.get("trade_key") or _state_key_for_alert(trade),
        "lifecycle_id": _trade_lifecycle_id(trade),
        "exit_reason": exit_reason,
        "outcome": outcome,
        "current_price": current_price,
        "expected_underlying_price": expected_underlying_price,
        "price_source": price_source,
        "scanner_row_symbol": scanner_row_symbol,
        "mfe_r": mfe_r,
        "trend_capture_pct": trend_capture_pct,
    }
    send_result = send_telegram_alert(
        message,
        after_success=after_success,
        scan_id=scan_id or trade.get("scan_id"),
        dispatch_metadata={
            "alert_key": alert_key,
            "metadata": metadata,
        }
    )

    return _queued_send_result(send_result, alert_key)


@_telegram_attempt_logger("SCANNER_ENTRY")
def maybe_send_scanner_entry_alert(
    symbol,
    final_signal,
    action_decision,
    entry_setup,
    risk_setup,
    option_contract,
    latest_price,
    bar_timestamp,
    next_condition,
    top_candidate=None,
    market_session=None,
    option_quote_freshness=None,
    option_quality_score=None,
    option_spread_pct=None,
    event_blocked=False,
    regime_blocked=False,
    setup_score=0,
    alignment_score=0,
    rs_rank_score=0,
    relative_volume=0,
    scan_id=None
):

    if not telegram_entry_alerts_enabled():

        return {
            "sent": False,
            "reason": "TELEGRAM_ENTRY_ALERTS_DISABLED"
        }

    action_status = action_decision.get("action_status")
    entry_type = str(entry_setup.get("entry_type") or "").upper()
    if entry_type in {"ACTIVE_TRADE", "PAPER_TRADE", "OPEN_TRADE"}:

        return {"sent": False, "reason": "ACTIVE_TRADE_SUPPRESSED"}

    if action_status in {"ENTER", "ENTER_PAPER"}:

        return {"sent": False, "reason": "ENTRY_AWAITING_TRADE_OPEN"}

    if action_status == "REVIEW_TV_CHART":

        return {"sent": False, "reason": "REVIEW_ALERT_SUPPRESSED"}

    if action_status != "REVIEW_TV_CHART":

        return {
            "sent": False,
            "reason": "ACTION_NOT_ALERTABLE"
        }

    option_contract = option_contract or {}

    option_ticker = option_contract.get("ticker") or "NO_CONTRACT"
    alert_key = _review_alert_key(symbol, entry_type)

    if alert_was_sent(alert_key):

        return {
            "sent": False,
            "reason": "DUPLICATE_ALERT",
            "alert_key": alert_key
        }

    message = build_review_alert_message(
        symbol,
        entry_type,
        next_condition
    )

    metadata = {
        "symbol": symbol,
        "direction": option_contract.get("type"),
        "option_ticker": option_ticker,
        "event_type": REVIEW_EVENT_TYPE,
        "message_type": "REVIEW",
        "decision": action_status,
        "candidate_key": alert_key,
        "action_status": action_status,
        "final_signal": final_signal,
        "setup_key": "_".join([str(symbol), entry_type]),
        "closed": False
    }
    send_result = send_telegram_alert(
        message,
        after_success=lambda _result: mark_alert_sent(alert_key, metadata),
        scan_id=scan_id,
        dispatch_metadata={
            "alert_key": alert_key,
            "metadata": metadata,
        }
    )

    return _queued_send_result(send_result, alert_key)


@_telegram_attempt_logger("TRADE_OPEN")
def maybe_send_trade_open_alert(trade, scanner_context=None, scan_id=None):

    if not telegram_entry_alerts_enabled():

        return {"sent": False, "reason": "TELEGRAM_ENTRY_ALERTS_DISABLED"}

    trade = trade or {}
    scanner_context = scanner_context or {}
    if str(trade.get("status") or "").upper() != "OPEN":

        return {"sent": False, "reason": "TRADE_NOT_OPEN"}

    action_status = str(scanner_context.get("Action Status") or "").upper()
    if action_status not in {"ENTER", "ENTER_PAPER"}:

        return {"sent": False, "reason": "ACTION_NOT_ALERTABLE"}

    alert_key = _trade_open_alert_key(trade)
    if alert_was_sent(alert_key):

        return {
            "sent": False,
            "reason": "DUPLICATE_ALERT",
            "alert_key": alert_key,
        }

    metadata = {
        "symbol": trade.get("symbol"),
        "direction": trade.get("direction"),
        "option_ticker": trade.get("option_ticker"),
        "event_type": ENTRY_EVENT_TYPE,
        "message_type": "TRADE_OPEN",
        "decision": action_status,
        "candidate_key": trade.get("trade_key") or alert_key,
        "trade_key": trade.get("trade_key") or _state_key_for_alert(trade),
        "last_r_multiple": 0.0,
        "last_stop": trade.get("stop_loss"),
        "last_trend_health": scanner_context.get("V2 Trend Health Status"),
        "last_confidence": scanner_context.get("V2 Trend Health Score"),
        "partial_profit_taken": False,
        "closed": False,
    }
    send_result = send_telegram_alert(
        build_paper_entry_alert_message(trade, scanner_context),
        after_success=lambda _result: mark_alert_sent(alert_key, metadata),
        scan_id=scan_id or trade.get("scan_id") or scanner_context.get("Scan ID"),
        dispatch_metadata={"alert_key": alert_key, "metadata": metadata},
    )
    return _queued_send_result(send_result, alert_key)


def build_trade_cancelled_alert_message(suggestion, reason=None, event_timestamp=None):

    suggestion = suggestion or {}
    message_reason = reason or suggestion.get("validity_reason") or "Entry conditions never confirmed."

    return "\n".join([
        "⚫ <b>TRADE CANCELLED</b>",
        _format_alert_timestamp(event_timestamp),
        f"<b>{_fmt(suggestion.get('symbol'))} {_fmt(suggestion.get('direction'))}</b>",
        "",
        f"Reason: {_fmt(message_reason)}",
        "Status: Cancelled",
        "No action taken.",
    ])


def _suggestion_was_alerted_to_subscriber(suggestion):

    suggestion_id = str((suggestion or {}).get("suggestion_id") or "")
    if not suggestion_id:
        return False
    for metadata in _load_alert_state().get("sent", {}).values():
        if str(metadata.get("candidate_key") or "") != suggestion_id:
            continue
        if metadata.get("message_type") in {"WATCHLIST_REVIEW", "REVIEW"}:
            return True
    return False


@_telegram_attempt_logger("TRADE_CANCELLED")
def maybe_send_trade_cancelled_alert(suggestion, reason=None, event_timestamp=None):

    suggestion = suggestion or {}
    if not telegram_entry_alerts_enabled():
        return {"sent": False, "reason": "TELEGRAM_ENTRY_ALERTS_DISABLED"}
    if not _suggestion_was_alerted_to_subscriber(suggestion):
        return {"sent": False, "reason": "NO_SUBSCRIBER_ALERT_TO_CANCEL"}

    alert_key = "|".join([
        "TRADE_CANCELLED",
        str(suggestion.get("suggestion_id") or suggestion.get("symbol") or "UNKNOWN"),
        str(event_timestamp or suggestion.get("expired_at") or _today_key()),
    ])
    if alert_was_sent(alert_key):
        return {"sent": False, "reason": "DUPLICATE_ALERT", "alert_key": alert_key}

    metadata = {
        "symbol": suggestion.get("symbol"),
        "direction": suggestion.get("direction"),
        "option_ticker": suggestion.get("option_ticker"),
        "event_type": "TRADE_CANCELLED",
        "message_type": "TRADE_CANCELLED",
        "candidate_key": suggestion.get("suggestion_id") or alert_key,
        "closed": True,
    }
    send_result = send_telegram_alert(
        build_trade_cancelled_alert_message(suggestion, reason, event_timestamp),
        after_success=lambda _result: mark_alert_sent(alert_key, metadata),
        dispatch_metadata={"alert_key": alert_key, "metadata": metadata},
    )
    return _queued_send_result(send_result, alert_key)