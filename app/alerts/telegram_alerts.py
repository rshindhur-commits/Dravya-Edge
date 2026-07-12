import html
import os
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
from app.decision import evaluate_candidate
from app.utils.json_store import load_json_file, save_json_file


ROOT_DIR = Path(__file__).resolve().parents[2]
ALERT_STATE_FILE = ROOT_DIR / "app" / "state" / "telegram_alert_state.json"
MAX_SENT_ALERTS = 1000
ENTRY_EVENT_TYPE = "ENTRY"


EXIT_ALERT_TRADE_MODES = {
    "PAPER",
    "REAL"
}


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
        or _streamlit_secret(["telegram", "bot_token"])
        or ""
    )
    chat_id = (
        os.getenv("TELEGRAM_CHAT_ID")
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


def send_telegram_alert(message):

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

    response = requests.post(
        url,
        json=payload,
        timeout=10
    )
    response.raise_for_status()


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


def _scanner_entry_alert_key(symbol, option_ticker, action_status, bar_timestamp):

    return "_".join([
        str(symbol),
        str(option_ticker),
        str(action_status),
        str(bar_timestamp)
    ])


def _exit_alert_key(symbol, option_ticker, trade, exit_reason, event_type):

    opened_at = (
        trade.get("opened_at")
        or trade.get("trade_key")
        or "NO_OPEN_TIME"
    )

    return "|".join([
        str(event_type or "EXIT"),
        str(symbol),
        str(option_ticker or "NO_CONTRACT"),
        str(opened_at),
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
        f"Contract Cost: ${cost}",
        f"Risk At Stop: ${risk_at_stop}",
        f"Affordability: {affordability}",
        f"Spread %: {_fmt(option_contract.get('spread_pct'))}",
        f"Quality: {_fmt(option_contract.get('option_quality_score'))}",
        f"Quote: {_fmt(option_contract.get('quote_freshness'))}",
        f"Alert Score: {_fmt(alert_score)}",
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

    return "\n".join([
        "<b>ENTRY ALERT</b>",
        f"Ticker: {_fmt(symbol)}",
        f"Direction: {_fmt(direction)}",
        f"Contract: {_fmt(option_ticker)}",
        f"Entry: {_fmt(trade.get('entry_price'))}",
        f"Stop: {_fmt(trade.get('stop_loss'))}",
        f"Target: {_fmt(trade.get('take_profit'))}",
        f"Setup: {_fmt(scanner_context.get('Setup Grade'))} / {_fmt(scanner_context.get('Setup %'))}",
        f"RR: {_fmt(trade.get('planned_rr') or scanner_context.get('Candidate RR') or scanner_context.get('RR'))}",
        f"Option Mid: {_fmt(option_mid)}",
        f"Contract Cost: ${_fmt(scanner_context.get('Option Contract Cost'))}",
        f"Quote: {_fmt(scanner_context.get('Option Quote Freshness'))}",
        f"Action: {_fmt(scanner_context.get('Action Status'))}",
        f"Reason: {_fmt(reason or scanner_context.get('Action Reason'))}",
        "Skip if broker bid/ask, spread, or chart confirmation disagrees."
    ])


@_telegram_attempt_logger("PAPER_ENTRY")
def maybe_send_paper_entry_alert(trade, scanner_context=None, reason=None):

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

    if action_status not in [
        "ENTER",
        "ENTER_PAPER",
        "REVIEW_TV_CHART"
    ]:

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
    policy_mode = _telegram_alert_policy_mode()
    min_setup = _float_setting(
        "TELEGRAM_MIN_PAPER_ENTRY_SETUP_SCORE",
        70.0
    )
    candidate = dict(scanner_context)
    candidate["Action Status"] = action_status

    if policy_mode == "REAL_REVIEW":

        policy_allowed, policy_reason, decision = _real_review_policy_allowed(candidate)

    elif policy_mode == "CUSTOM":

        policy_allowed, policy_reason, decision = _custom_policy_allowed(
            candidate,
            policy,
            min_setup=min_setup
        )

    else:

        policy_allowed, policy_reason, decision = _paper_policy_allowed(
            candidate,
            policy["min_alert_score"]
        )

    if not policy_allowed:

        return {
            "sent": False,
            "reason": policy_reason,
            "decision_score": decision.score,
            "telegram_policy": policy_mode
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
    send_telegram_alert(message)
    mark_alert_sent(
        alert_key,
        {
            "symbol": symbol,
            "option_ticker": option_ticker,
            "event_type": ENTRY_EVENT_TYPE,
            "source": "paper_entry",
            "telegram_policy": policy_mode,
            "decision_score": decision.score,
            "action_status": action_status,
            "setup_key": "_".join([
                str(symbol),
                str(direction),
                str(trade.get("entry_type"))
            ]),
            "closed": False
        }
    )

    return {
        "sent": True,
        "reason": "SENT",
        "alert_key": alert_key,
        "decision_score": decision.score,
        "telegram_policy": policy_mode
    }


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
    price_source=None
):

    option_ticker = _fmt(
        trade.get("option_ticker")
        or trade.get("ticker")
    )
    entry_price = _fmt(trade.get("entry_price"))
    option_entry_mid = _fmt(
        trade.get("option_entry_mid")
        or trade.get("option_mid")
    )

    title = "PARTIAL EXIT ALERT" if event_type == "PARTIAL_EXIT" else "EXIT ALERT"

    return "\n".join([
        f"<b>{title}</b>",
        f"Ticker: {_fmt(symbol)}",
        f"Contract: {option_ticker}",
        f"Entry: {entry_price}",
        f"Current: {_fmt(current_price)}",
        f"Expected Same-Symbol Close: {_fmt(expected_underlying_price)}",
        f"Price Source: {_fmt(price_source)}",
        f"Option Entry Mid: {option_entry_mid}",
        f"Option Current Mid: {_fmt(option_current_mid)}",
        f"P/L %: {_fmt(pnl_pct)}",
        f"R Multiple: {_fmt(r_multiple)}",
        f"Outcome: {_fmt(outcome)}",
        f"Action: {_fmt(event_type)}",
        f"Reason: {_fmt(exit_reason)}"
    ])


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
    candidate_prices=None
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
        price_source=price_source
    )

    send_telegram_alert(message)
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

    mark_alert_sent(
        alert_key,
        {
            "symbol": symbol,
            "option_ticker": option_ticker,
            "event_type": event_type,
            "exit_reason": exit_reason,
            "outcome": outcome,
            "current_price": current_price,
            "expected_underlying_price": expected_underlying_price,
            "price_source": price_source,
            "scanner_row_symbol": scanner_row_symbol
        }
    )

    return {
        "sent": True,
        "reason": "SENT",
        "alert_key": alert_key
    }


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
    relative_volume=0
):

    if not telegram_entry_alerts_enabled():

        return {
            "sent": False,
            "reason": "TELEGRAM_ENTRY_ALERTS_DISABLED"
        }

    action_status = action_decision.get("action_status")

    if action_status not in [
        "ENTER",
        "ENTER_PAPER",
        "REVIEW_TV_CHART"
    ]:

        return {
            "sent": False,
            "reason": "ACTION_NOT_ALERTABLE"
        }

    if str(market_session or "").upper() in [
        "PREMARKET",
        "OPENING_RANGE",
        "CLOSED",
        "AFTERHOURS"
    ]:

        return {
            "sent": False,
            "reason": "MARKET_SESSION_NOT_ALERTABLE"
        }

    if not option_contract:

        return {
            "sent": False,
            "reason": "NO_OPTION_CONTRACT"
        }

    policy = _entry_alert_policy()
    policy_mode = _telegram_alert_policy_mode()

    quote_freshness = (
        option_quote_freshness
        or option_contract.get("quote_freshness")
    )

    quality_score = _float_value(
        option_quality_score,
        _float_value(option_contract.get("option_quality_score"))
    )

    risk_reward = _float_value(risk_setup.get("risk_reward"))

    spread_pct = _float_value(
        option_spread_pct,
        _float_value(option_contract.get("spread_pct"), None)
    )

    alert_score = calculate_entry_alert_score(
        setup_score=setup_score,
        alignment_score=alignment_score,
        rs_rank_score=rs_rank_score,
        option_quality_score=quality_score,
        risk_reward=risk_reward,
        relative_volume=relative_volume,
        option_spread_pct=spread_pct
    )

    min_score = policy["min_alert_score"]
    time_bucket = _entry_alert_time_bucket()

    if time_bucket == "AFTERNOON":

        min_score = policy["afternoon_min_alert_score"]

    candidate = {
        "Action Status": action_status,
        "Setup %": setup_score,
        "Candidate RR": risk_reward,
        "Option Quality Score": quality_score,
        "Option Spread %": spread_pct,
        "Option Quote Freshness": quote_freshness,
        "Affordable": option_contract.get("affordable"),
        "Event Blocked": event_blocked,
        "Regime Blocked": regime_blocked,
        "Top Candidate": top_candidate,
        "Entry Alert Score": alert_score,
    }

    if policy_mode == "REAL_REVIEW":

        policy_allowed, policy_reason, decision = _real_review_policy_allowed(candidate)

    elif policy_mode == "CUSTOM":

        policy_allowed, policy_reason, decision = _custom_policy_allowed(
            candidate,
            policy,
            min_setup=0.0
        )

    else:

        policy_allowed, policy_reason, decision = _paper_policy_allowed(
            candidate,
            min_score
        )

    if not policy_allowed:

        return {
            "sent": False,
            "reason": policy_reason,
            "alert_score": alert_score,
            "decision_score": decision.score,
            "telegram_policy": policy_mode
        }

    if policy_mode in {"CUSTOM", "REAL_REVIEW"}:

        if final_signal not in [
            "HIGH CONVICTION BULLISH",
            "HIGH CONVICTION BEARISH"
        ]:

            return {
                "sent": False,
                "reason": "NOT_HIGH_CONVICTION"
            }

        if not _top_candidate_allowed(
            top_candidate,
            policy["top_candidate_limit"]
        ):

            return {
                "sent": False,
                "reason": "NOT_TOP_ALERT_CANDIDATE"
            }

    if time_bucket in [
        "TOO_EARLY",
        "TOO_LATE"
    ]:

        return {
            "sent": False,
            "reason": f"ENTRY_ALERT_{time_bucket}"
        }

    instant_alert = alert_score >= policy["instant_alert_score"]

    state = _load_alert_state()

    if _entry_alerts_today(state) >= policy["max_daily_entries"]:

        return {
            "sent": False,
            "reason": "MAX_DAILY_ENTRY_ALERTS_REACHED"
        }

    if len(_active_entry_alerts(state)) >= policy["max_active_alerted_trades"]:

        return {
            "sent": False,
            "reason": "MAX_ACTIVE_ALERTED_TRADES_REACHED"
        }

    bucket_limit = {
        "MORNING": policy["max_morning_entries"],
        "MIDDAY": policy["max_midday_entries"],
        "AFTERNOON": policy["max_afternoon_entries"]
    }.get(time_bucket)

    if (
        bucket_limit is not None
        and not instant_alert
        and _entry_alerts_in_bucket(state, time_bucket) >= bucket_limit
    ):

        return {
            "sent": False,
            "reason": f"{time_bucket}_ENTRY_ALERT_LIMIT_REACHED"
        }

    option_ticker = option_contract.get("ticker") or "NO_CONTRACT"
    setup_key = "_".join([
        str(symbol),
        str(entry_setup.get("entry_type")),
        str(action_status)
    ])

    if _recent_matching_entry_alert(
        state,
        symbol,
        setup_key,
        policy["cooldown_minutes"]
    ):

        return {
            "sent": False,
            "reason": "ENTRY_ALERT_COOLDOWN_ACTIVE"
        }

    if _recent_closed_symbol_alert(
        state,
        symbol,
        policy["symbol_cooldown_minutes"]
    ):

        return {
            "sent": False,
            "reason": "SYMBOL_COOLDOWN_ACTIVE"
        }

    alert_key = _scanner_entry_alert_key(
        symbol,
        option_ticker,
        action_status,
        bar_timestamp
    )

    if alert_was_sent(alert_key):

        return {
            "sent": False,
            "reason": "DUPLICATE_ALERT",
            "alert_key": alert_key
        }

    message = build_scanner_entry_alert_message(
        symbol=symbol,
        final_signal=final_signal,
        action_status=action_status,
        entry_setup=entry_setup,
        risk_setup=risk_setup,
        option_contract=option_contract,
        latest_price=latest_price,
        next_condition=next_condition,
        alert_score=alert_score
    )

    send_telegram_alert(message)
    mark_alert_sent(
        alert_key,
        {
            "symbol": symbol,
            "option_ticker": option_ticker,
            "event_type": ENTRY_EVENT_TYPE,
            "action_status": action_status,
            "final_signal": final_signal,
            "setup_key": setup_key,
            "top_candidate": top_candidate,
            "time_bucket": time_bucket,
            "alert_score": alert_score,
            "decision_score": decision.score,
            "telegram_policy": policy_mode,
            "instant_alert": instant_alert,
            "closed": False
        }
    )

    return {
        "sent": True,
        "reason": "SENT",
        "alert_key": alert_key,
        "alert_score": alert_score,
        "decision_score": decision.score,
        "telegram_policy": policy_mode
    }


def _telegram_alert_policy_mode():

    return str(
        os.getenv(
            "TELEGRAM_ALERT_POLICY",
            _streamlit_secret(
                ["TELEGRAM_ALERT_POLICY"],
                _streamlit_secret(
                    ["telegram", "alert_policy"],
                    "PAPER"
                )
            )
        )
    ).strip().upper()


def _decision_policy_allowed(decision, min_score):

    if decision.action != "ENTER_PAPER":

        return False, "DECISION_NOT_ENTER_PAPER"

    if decision.blocked:

        return False, "DECISION_BLOCKED: " + "; ".join(decision.block_reasons)

    if decision.score < min_score:

        return False, "DECISION_SCORE_BELOW_MIN"

    return True, "ELIGIBLE"


def _real_review_policy_allowed(candidate):

    decision = evaluate_candidate(candidate)

    allowed, reason = _decision_policy_allowed(
        decision,
        _float_setting("REAL_MIN_SETUP", 88.0)
    )

    if not allowed:

        return False, reason, decision

    gate_allowed, gate_reason = evaluate_entry_gate(
        candidate,
        EntryGateConfig(
            min_rr=_float_setting("REAL_MIN_RR", 2.0),
            min_setup_percent=_float_setting("REAL_MIN_SETUP", 88.0),
            min_option_quality=_float_setting("REAL_MIN_OPTION_QUALITY", 90.0),
            max_spread_pct=_float_setting("REAL_MAX_SPREAD_PCT", 8.0)
        ),
        mode="telegram_real_review"
    )

    if not gate_allowed:

        return False, gate_reason, decision

    scan_count = _float_value(
        candidate.get("Candidate Scan Count"),
        0
    )
    min_scans = _float_setting(
        "REAL_MIN_CONSECUTIVE_SCANS",
        2.0
    )

    if scan_count < min_scans:

        return False, "REAL_REVIEW_PERSISTENCE_REQUIRED", decision

    if not _top_candidate_allowed(
        candidate.get("Top Candidate"),
        1
    ):

        return False, "REAL_REVIEW_TOP1_REQUIRED", decision

    return True, "ELIGIBLE", decision


def _custom_policy_allowed(candidate, policy, min_setup=None):

    decision = evaluate_candidate(candidate)
    gate_allowed, gate_reason = evaluate_entry_gate(
        candidate,
        EntryGateConfig(
            min_rr=policy["min_rr"],
            min_setup_percent=(
                min_setup
                if min_setup is not None
                else policy["min_alert_score"]
            ),
            min_option_quality=policy["min_option_quality"],
            max_spread_pct=policy["max_spread_pct"]
        ),
        mode="telegram_custom"
    )

    if not gate_allowed:

        return False, gate_reason, decision

    if decision.blocked:

        return False, "DECISION_BLOCKED: " + "; ".join(decision.block_reasons), decision

    return True, "ELIGIBLE", decision


def _paper_policy_allowed(candidate, min_score):

    decision = evaluate_candidate(candidate)
    allowed, reason = _decision_policy_allowed(
        decision,
        min_score
    )
    return allowed, reason, decision