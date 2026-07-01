import html
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from app.utils.json_store import load_json_file, save_json_file


ROOT_DIR = Path(__file__).resolve().parents[2]
ALERT_STATE_FILE = ROOT_DIR / "app" / "state" / "telegram_alert_state.json"
MAX_SENT_ALERTS = 1000
ENTRY_EVENT_TYPE = "ENTRY"


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
            3
        ),
        "cooldown_minutes": _int_setting(
            "TELEGRAM_ENTRY_COOLDOWN_MINUTES",
            60
        ),
        "top_candidate_limit": _int_setting(
            "TELEGRAM_TOP_CANDIDATE_LIMIT",
            3
        ),
        "min_alert_score": _float_setting(
            "TELEGRAM_MIN_ENTRY_ALERT_SCORE",
            80.0
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
            1.8
        ),
        "max_spread_pct": _float_setting(
            "TELEGRAM_MAX_SPREAD_PCT",
            10.0
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


def build_trade_exit_alert_message(
    symbol,
    trade,
    exit_reason,
    current_price=None,
    option_current_mid=None,
    pnl_pct=None,
    r_multiple=None,
    outcome=None,
    event_type="EXIT"
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
        f"Option Entry Mid: {option_entry_mid}",
        f"Option Current Mid: {_fmt(option_current_mid)}",
        f"P/L %: {_fmt(pnl_pct)}",
        f"R Multiple: {_fmt(r_multiple)}",
        f"Outcome: {_fmt(outcome)}",
        f"Action: {_fmt(event_type)}",
        f"Reason: {_fmt(exit_reason)}"
    ])


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
    event_timestamp=None
):

    if not telegram_alerts_enabled():

        return {
            "sent": False,
            "reason": "TELEGRAM_ALERTS_DISABLED"
        }

    trade = trade or {}
    option_ticker = (
        trade.get("option_ticker")
        or trade.get("ticker")
        or "NO_CONTRACT"
    )
    event_key_part = event_timestamp or exit_reason or event_type
    alert_key = "_".join([
        str(symbol),
        str(option_ticker),
        str(event_type),
        str(event_key_part)
    ])

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
        event_type=event_type
    )

    send_telegram_alert(message)
    if event_type == "EXIT":

        mark_alert_closed(
            symbol,
            option_ticker
        )

    mark_alert_sent(
        alert_key,
        {
            "symbol": symbol,
            "option_ticker": option_ticker,
            "event_type": event_type,
            "exit_reason": exit_reason,
            "outcome": outcome
        }
    )

    return {
        "sent": True,
        "reason": "SENT",
        "alert_key": alert_key
    }


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

    if not telegram_alerts_enabled():

        return {
            "sent": False,
            "reason": "TELEGRAM_ALERTS_DISABLED"
        }

    action_status = action_decision.get("action_status")

    if final_signal not in [
        "HIGH CONVICTION BULLISH",
        "HIGH CONVICTION BEARISH"
    ]:

        return {
            "sent": False,
            "reason": "NOT_HIGH_CONVICTION"
        }

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

    if not _bool_value(option_contract.get("affordable")):

        return {
            "sent": False,
            "reason": option_contract.get(
                "affordability_status",
                "OPTION_NOT_AFFORDABLE"
            )
        }

    policy = _entry_alert_policy()

    quote_freshness = (
        option_quote_freshness
        or option_contract.get("quote_freshness")
    )

    if quote_freshness != "LIVE_QUOTE":

        return {
            "sent": False,
            "reason": "OPTION_QUOTE_NOT_FRESH"
        }

    quality_score = _float_value(
        option_quality_score,
        _float_value(option_contract.get("option_quality_score"))
    )

    if quality_score < policy["min_option_quality"]:

        return {
            "sent": False,
            "reason": "OPTION_QUALITY_BELOW_ALERT_MIN"
        }

    risk_reward = _float_value(risk_setup.get("risk_reward"))

    if risk_reward < policy["min_rr"]:

        return {
            "sent": False,
            "reason": "RR_BELOW_ALERT_MIN"
        }

    spread_pct = _float_value(
        option_spread_pct,
        _float_value(option_contract.get("spread_pct"), None)
    )

    if spread_pct is not None and spread_pct > policy["max_spread_pct"]:

        return {
            "sent": False,
            "reason": "SPREAD_ABOVE_ALERT_MAX"
        }

    if event_blocked:

        return {
            "sent": False,
            "reason": "EVENT_BLOCKED"
        }

    if regime_blocked:

        return {
            "sent": False,
            "reason": "REGIME_BLOCKED"
        }

    if not _top_candidate_allowed(
        top_candidate,
        policy["top_candidate_limit"]
    ):

        return {
            "sent": False,
            "reason": "NOT_TOP_ALERT_CANDIDATE"
        }

    time_bucket = _entry_alert_time_bucket()

    if time_bucket in [
        "TOO_EARLY",
        "TOO_LATE"
    ]:

        return {
            "sent": False,
            "reason": f"ENTRY_ALERT_{time_bucket}"
        }

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

    if time_bucket == "AFTERNOON":

        min_score = policy["afternoon_min_alert_score"]

    if alert_score < min_score:

        return {
            "sent": False,
            "reason": "ALERT_SCORE_BELOW_MIN",
            "alert_score": alert_score
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

    alert_key = "_".join([
        str(symbol),
        str(option_ticker),
        str(action_status),
        str(bar_timestamp)
    ])

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
            "instant_alert": instant_alert,
            "closed": False
        }
    )

    return {
        "sent": True,
        "reason": "SENT",
        "alert_key": alert_key
    }