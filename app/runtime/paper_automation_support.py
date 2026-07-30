from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from app.gates import (
    EntryGateConfig,
    active_symbol_trade,
    env_int,
    evaluate_entry_gate,
    has_active_symbol_trade,
    is_symbol_in_cooldown,
    symbol_trade_count_today,
)
from app.state.holding_policy import holding_policy
from app.storage.auto_paper_decision_store import (
    append_daily_auto_paper_decision,
    classify_decision_time,
    update_recent_auto_paper_log,
)
from app.storage.daily_paths import get_daily_dir, state_path
from app.storage.session_manager import get_scan_id, get_session_id, get_trading_day
from app.utils.json_store import load_json_file


ROOT_DIR = Path(__file__).resolve().parents[2]
AUTO_PAPER_DECISION_LOG_FILE = state_path("auto_paper_decision_log.json")
AUTO_PAPER_SETTINGS_FILE = state_path("auto_paper_settings.json")
AUTO_PAPER_TOP_CANDIDATES = [
    "BULLISH_TOP_1",
    "BEARISH_TOP_1",
    "BULLISH_TOP_2",
    "BEARISH_TOP_2",
    "BULLISH_TOP_3",
    "BEARISH_TOP_3",
]
INDEX_REVIEW_VALIDATION_SYMBOLS = {"SPY", "QQQ"}
REVIEW_VALIDATION_ENTRY_TYPES = {
    "BREAKOUT",
    "BREAKOUT_LONG",
    "EMA_PULLBACK",
    "VWAP_RECLAIM",
    "COILED_BREAKOUT",
    "BREAKDOWN_SHORT",
    "EMA_REJECTION_SHORT",
    "VWAP_REJECTION",
}
AUTO_PAPER_ENTRY_START = time(9, 45)
AUTO_PAPER_ENTRY_END = time(15, 30)
AUTO_PAPER_EOD_CLOSE = time(15, 55)
AUTO_PAPER_MAX_CANDIDATE_RANK = 3
DEFAULT_AUTO_PAPER_MIN_RR = 1.8
DEFAULT_AUTO_PAPER_MIN_OPTION_QUALITY = 65.0
DEFAULT_AUTO_PAPER_MAX_SPREAD_PCT = 10.0
AUTO_PAPER_REQUIRED_COLUMNS = [
    "Symbol", "Setup Valid", "Candidate Direction", "Candidate Entry Price",
    "Candidate Stop Price", "Candidate Target Price", "Candidate RR", "Entry",
    "Action Status", "Next Condition", "Live Chart Checklist",
]


def load_auto_paper_controls():
    settings = load_json_file(str(AUTO_PAPER_SETTINGS_FILE), {})
    return {
        "auto_paper_enabled": _boolish(
            settings.get("auto_paper_enabled", _env_bool("AUTO_PAPER_ENABLED", True))
        ),
        "max_daily": int(settings.get("auto_paper_max_daily", 3)),
        "min_setup": float(settings.get("auto_paper_min_setup", 70)),
        "min_rr": float(settings.get("auto_paper_min_rr", DEFAULT_AUTO_PAPER_MIN_RR)),
        "direction": settings.get("auto_paper_direction", "Both"),
        # Default ON. `app/state/auto_paper_settings.json` is gitignored, so on
        # Streamlit Cloud it does not exist after a redeploy and every setting falls
        # back to its code default. With this defaulted to False, the standing policy
        # of flattening intraday positions at 15:55 ET silently reverted to OFF on
        # every deploy, leaving day trades to be carried overnight.
        "eod_close_enabled": _boolish(settings.get("auto_paper_eod_close_enabled", True)),
        "restore_multiday_positions": _boolish(settings.get("restore_multiday_positions", True)),
    }


def _env_bool(name, default=False):

    import os

    value = os.getenv(name)

    if value is None:

        return default

    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name, default):

    import os

    try:

        return float(os.getenv(name, default))

    except Exception:

        return default


def _safe_float(value, default=0.0):

    try:

        if value is None or pd.isna(value):

            return default

        return float(value)

    except Exception:

        return default


def _boolish(value):

    if isinstance(value, bool):

        return value

    return str(value).strip().lower() in {"true", "1", "yes", "y", "on"}


def _current_et():

    return datetime.now(ZoneInfo("America/New_York"))


def auto_paper_session_block_reason(now=None):
    now = now or _current_et()
    if now.weekday() >= 5:
        return "market day closed"
    if not (AUTO_PAPER_ENTRY_START <= now.time() <= AUTO_PAPER_ENTRY_END):
        return "outside auto-entry window"
    return None


def should_record_auto_paper_session_skip(reason, now=None):
    now = now or _current_et()
    trading_day = get_trading_day(now)
    market_session = classify_decision_time(now).get("market_session")
    recent = load_json_file(str(AUTO_PAPER_DECISION_LOG_FILE), [])
    for row in reversed(recent if isinstance(recent, list) else []):
        if (
            row.get("trading_day") == trading_day
            and row.get("market_session") == market_session
            and row.get("symbol") == "SYSTEM"
            and row.get("decision") == "SKIPPED"
            and row.get("reason") == reason
        ):
            return False
    return True


def _allow_review_tv_chart_auto_paper():

    return _env_bool("ALLOW_REVIEW_TV_CHART_AUTO_PAPER", False)


def _ignore_affordability_for_paper_validation():

    return _env_bool("PAPER_IGNORE_AFFORDABILITY", True)


def _require_affordability_for_real_readiness():

    return _env_bool("REAL_REQUIRE_AFFORDABILITY", True)


def _paper_affordability_override_needed(row):

    if not _ignore_affordability_for_paper_validation():

        return False

    if "Affordable" not in row.index:

        return False

    return not _boolish(row.get("Affordable"))


def _annotate_paper_affordability_override(row):

    if not _paper_affordability_override_needed(row):

        return row
    row = row.copy()
    row["Paper Affordability Override"] = True
    row["Original Affordable"] = row.get("Affordable")
    row["Original Affordability Status"] = row.get("Affordability Status")
    row["Original Option Contract Cost"] = row.get("Option Contract Cost")
    row["Original Max Allowed Contract Cost"] = row.get("Max Allowed Contract Cost")
    return row


def _paper_gate_row(row):

    if not _paper_affordability_override_needed(row):

        return row
    gate_row = row.copy()
    gate_row["Paper Affordability Override"] = True
    gate_row["Original Affordable"] = row.get("Affordable")
    gate_row["Original Affordability Status"] = row.get("Affordability Status")
    gate_row["Affordable"] = True
    gate_row["Affordability Status"] = "IGNORED_FOR_PAPER_VALIDATION"
    return gate_row


def _affordability_mask(df, ignore_affordability):

    if ignore_affordability or "Affordable" not in df.columns:
        return pd.Series(True, index=df.index)
    return df["Affordable"].astype(str).str.lower().isin(["true", "1", "yes"]) | (df["Affordable"] == True)


def _is_valid_new_entry_type(entry_type):

    return str(entry_type or "").upper() not in {
        "",
        "NAN",
        "NONE",
        "NO_ENTRY",
        "NO_SETUP",
        "ACTIVE_TRADE",
        "PAPER_TRADE",
        "OPEN_TRADE",
    }


def _compute_setup_percent(row):

    score = abs(_safe_float(row.get("15m Score")))
    rr = _safe_float(row.get("Risk Reward"))
    action = str(row.get("Action Status", "WAIT")).upper()
    entry = row.get("Entry")
    setup_valid = _boolish(row.get("Setup Valid"))
    score_points = min(score / 10, 1) * 40
    rr_points = min(rr / 2.5, 1) * 25
    entry_points = 15 if _is_valid_new_entry_type(entry) else 0

    if action in ["ENTER", "ENTER_PAPER"]:
        action_points = 20
    elif action in ["WATCH", "REVIEW_TV_CHART"]:
        action_points = 15
    elif action == "QUALITY_BUT_TOO_EXPENSIVE":
        action_points = 10
    elif action == "WAIT":
        action_points = 5
    else:
        action_points = 0

    readiness = score_points + rr_points + entry_points + action_points
    if not setup_valid and action != "REVIEW_TV_CHART":
        readiness = min(readiness, 59)
    if action in ["AVOID", "NO_TRADE_MARKET_CLOSED", "OPTION_MARKET_CLOSED", "NO_BID_ASK", "NO_QUOTE_SNAPSHOT", "RATE_LIMITED", "PROVIDER_ERROR"]:
        readiness = min(readiness, 49)
    return round(max(0, min(readiness, 100)), 0)


def _high_quality_index_review_exception(row):

    symbol = str(row.get("Symbol") or "").strip().upper()
    entry_type = str(row.get("Entry") or "").strip().upper()
    if symbol not in INDEX_REVIEW_VALIDATION_SYMBOLS or entry_type not in REVIEW_VALIDATION_ENTRY_TYPES:
        return False
    setup = _safe_float(row.get("Setup %"), 0)
    rr = _safe_float(row.get("RR"), _safe_float(row.get("Candidate RR"), _safe_float(row.get("Risk Reward"), 0)))
    option_quality = _safe_float(row.get("Option Quality Score"), 0)
    spread = _safe_float(row.get("Option Spread %"), None)
    quote_age = _safe_float(row.get("Option Quote Age Minutes"), 999)
    review_scan_count = _safe_float(row.get("Real Review Scan Count"), 0)
    if setup < _env_float("INDEX_REVIEW_MIN_SETUP", 82.0):
        return False
    if rr < _env_float("INDEX_REVIEW_MIN_RR", 1.8):
        return False
    if option_quality < _env_float("INDEX_REVIEW_MIN_OPTION_QUALITY", 90.0):
        return False
    if spread is not None and spread > _env_float("INDEX_REVIEW_MAX_SPREAD_PCT", 3.0):
        return False
    if quote_age > _env_float("INDEX_REVIEW_MAX_QUOTE_AGE_MINUTES", 3.0):
        return False
    if review_scan_count < _env_float("INDEX_REVIEW_MIN_SCANS", 2):
        return False
    if _boolish(row.get("Event Blocked")) or _boolish(row.get("Regime Blocked")):
        return False
    return True


def _paper_trade_candidates(df):

    if df.empty:
        return pd.DataFrame()
    eligible = [
        index
        for index, row in df.iterrows()
        if _paper_candidate_filter_reason(row) is None
    ]
    return df.loc[eligible].copy()


def _auto_paper_actionable_rows(df):
    if df.empty or "Action Status" not in df.columns:
        return pd.DataFrame()
    statuses = {"ENTER", "ENTER_PAPER"}
    if _allow_review_tv_chart_auto_paper():
        statuses.add("REVIEW_TV_CHART")
    return df[df["Action Status"].astype(str).str.upper().isin(statuses)].copy()


def _paper_candidate_filter_reason(row):
    missing = [column for column in AUTO_PAPER_REQUIRED_COLUMNS if column not in row.index]
    if missing:
        return "MISSING_SCANNER_FIELDS:" + ",".join(missing)
    status = str(row.get("Action Status") or "").upper()
    if status not in {"ENTER", "ENTER_PAPER", "REVIEW_TV_CHART"}:
        return "NOT_ACTIONABLE_STATUS"
    if status == "REVIEW_TV_CHART" and not _allow_review_tv_chart_auto_paper():
        return "REVIEW_VALIDATION_DISABLED"
    if not _boolish(row.get("Setup Valid")):
        return "SETUP_INVALID"
    if row.get("Candidate Direction") not in {"CALL", "PUT"}:
        return "INVALID_CANDIDATE_DIRECTION"
    if not _is_valid_new_entry_type(row.get("Entry")):
        return "INVALID_ENTRY_TYPE"
    if not _ignore_affordability_for_paper_validation() and not _boolish(row.get("Affordable")):
        return "PAPER_AFFORDABILITY_REJECTED"
    review_ready = status == "REVIEW_TV_CHART" and _allow_review_tv_chart_auto_paper()
    if not review_ready and not _boolish(row.get("Realtime Ready")):
        return row.get("Realtime Block Reason") or "REALTIME_NOT_READY"
    return None


def _scanner_context_from_row(row):

    context_fields = [field for field in row.index]
    context = {field: row.get(field) for field in context_fields}
    from app.state.holding_policy import derive_holding_profile

    context["Holding Profile"] = derive_holding_profile(context).value
    return context


def _real_entry_checklist(row):
    if row.get("Real Trade Readiness") != "A_PLUS_REAL_REVIEW":
        return None
    return "Real review only - no auto order; Confirm candle, live quote, spread, and risk."


def _real_trade_readiness(row):
    action_status = str(row.get("Action Status") or "").upper()
    if action_status not in ["ENTER", "ENTER_PAPER", "REVIEW_TV_CHART"]:
        return "NOT_REAL_READY"
    if not _boolish(row.get("Paper Trade Opened")):
        return "PAPER_ONLY"
    if _require_affordability_for_real_readiness() and not _boolish(row.get("Affordable")):
        return "PAPER_ONLY_UNAFFORDABLE"
    return "REVIEW_REQUIRED"


def _record_auto_paper_decision(symbol, decision, reason, row=None, trade=None, controls=None):
    controls = controls or {}
    decision_time = _current_et()
    trading_day = get_trading_day(decision_time)
    scan_timestamp = decision_time.strftime("%Y-%m-%d %H:%M:%S")
    scan_id = (
        row.get("Scan ID") or row.get("scan_id")
        if row is not None
        else None
    ) or get_scan_id(trading_day, decision_time)
    action_status = row.get("Action Status") if row is not None else None
    scanner_blocked_by = row.get("Blocked By") if row is not None else None
    blocked_by = (
        reason
        if str(decision or "").upper() == "BLOCKED"
        else None
        if str(scanner_blocked_by or "").strip().upper() == str(action_status or "").strip().upper()
        else scanner_blocked_by
    )
    entry = {
        "timestamp": scan_timestamp,
        "trading_day": trading_day,
        "session_id": get_session_id(trading_day),
        "scan_id": scan_id,
        "scan_timestamp": scan_timestamp,
        **classify_decision_time(decision_time),
        "gate_mode": "auto_paper",
        "min_rr_used": controls.get("min_rr"),
        "min_setup_used": controls.get("min_setup"),
        "symbol": symbol,
        "decision": decision,
        "reason": reason,
        "trade_key": trade.get("trade_key") if trade else None,
        "top_candidate": row.get("Top Candidate") if row is not None else None,
        "setup_percent": row.get("Setup %") if row is not None else None,
        "rr": row.get("RR") if row is not None else None,
        "setup_valid": row.get("Setup Valid") if row is not None else None,
        "execution_ready": row.get("Execution Ready") if row is not None else None,
        "scanner_recommendation": row.get("Scanner Recommendation") if row is not None else action_status,
        "execution_eligibility": row.get("Execution Eligibility") if row is not None else None,
        "execution_outcome": row.get("Execution Outcome") if row is not None else decision,
        "execution_reason": row.get("Execution Reason") if row is not None else reason,
        "trade_status": row.get("Trade Status") if row is not None else "NOT_CREATED",
        "telegram_status": row.get("Telegram Status") if row is not None else "NO_LIFECYCLE_EVENT",
        "telegram_reason": row.get("Telegram Reason") if row is not None else "NO_LIFECYCLE_EVENT",
        "realtime_ready": row.get("Realtime Ready") if row is not None else None,
        "blocked_by": blocked_by,
        "scanner_blocked_by": scanner_blocked_by,
        "action_status": action_status,
        "action_reason": row.get("Action Reason") if row is not None else None,
    }
    append_daily_auto_paper_decision(entry, get_daily_dir(trading_day))
    update_recent_auto_paper_log(entry, AUTO_PAPER_DECISION_LOG_FILE)


def _closed_paper_trades(paper_trades):
    return [trade for trade in (paper_trades or {}).values() if trade.get("status") == "CLOSED"]


def _auto_paper_trade_count_today(paper_trades):
    today = _current_et().date()
    count = 0
    for trade in paper_trades.values():
        opened_at = trade.get("opened_at")
        if not opened_at:
            continue
        try:
            opened_date = datetime.strptime(opened_at, "%Y-%m-%d %H:%M:%S").date()
        except Exception:
            continue
        if opened_date == today and str(trade.get("notes", "")).startswith("Auto paper"):
            count += 1
    return count


def spread_cost_exceeds_risk(row):
    """True when the option's round trip costs more than the trade risks.

    A stop is set on the underlying; the position is an option. Convert the stop
    distance into the option move it implies -- distance * delta / premium -- and
    compare it against the round-trip spread. When the spread is larger, the trade
    pays more to open and close than it stands to lose being wrong, so it cannot
    be won by being right about direction.

    Every trade on 2026-07-30 failed this test. PLTR risked a 3.2% option move
    against a 4.1% spread; ORCL risked 2.6% against 8.0%. Both booked positive R
    and lost money. R cannot see this, because R is computed entirely on the
    underlying and never looks at what the contract costs to trade.

    Returns False when delta, premium or spread are missing rather than blocking on
    absent data: a gate that fires on a missing field silently stops trading
    altogether, which is a worse failure than the one it prevents.
    """

    entry = _safe_float(row.get("Candidate Entry Price"), None)
    stop = _safe_float(row.get("Candidate Stop Price"), None)
    delta = _safe_float(row.get("Option Delta"), None)
    premium = _safe_float(row.get("Option Mid Price"), None) or _safe_float(row.get("Option Midpoint"), None)
    spread_pct = _safe_float(row.get("Option Spread %"), None)

    if None in (entry, stop, delta, premium, spread_pct):
        return False, None

    if premium <= 0 or spread_pct <= 0:
        return False, None

    risk_move_pct = abs(entry - stop) * abs(delta) / premium * 100

    if risk_move_pct <= 0:
        return False, None

    tolerance = _env_float("AUTO_PAPER_MAX_SPREAD_TO_RISK", 1.0)

    if spread_pct > risk_move_pct * tolerance:
        return True, (
            f"SPREAD_EXCEEDS_RISK: spread {spread_pct:.1f}% vs "
            f"{risk_move_pct:.1f}% option move to stop"
        )

    return False, None


def _auto_paper_entry_reason(row, controls, paper_trades):
    now_et = _current_et()
    if not controls["auto_paper_enabled"]:
        return False, "auto paper disabled"
    session_block = auto_paper_session_block_reason(now_et)
    if session_block:
        return False, session_block
    action_status = str(row.get("Action Status")).strip().upper()
    realtime_ready = str(row.get("Realtime Ready")).strip().lower() in ["true", "1", "yes"]
    review_validation_candidate = action_status == "REVIEW_TV_CHART" and _allow_review_tv_chart_auto_paper()
    top_candidate = row.get("Top Candidate")
    candidate_rank = _safe_float(row.get("Candidate Rank"), None)
    rank_eligible = (
        candidate_rank is not None
        and candidate_rank <= env_int(
            "AUTO_PAPER_MAX_CANDIDATE_RANK",
            AUTO_PAPER_MAX_CANDIDATE_RANK,
        )
    )
    if (
        top_candidate not in AUTO_PAPER_TOP_CANDIDATES
        and not rank_eligible
        and not _high_quality_index_review_exception(row)
    ):
        return False, "not top candidate"
    if _safe_float(row.get("Setup %"), None) is None:
        row = row.copy()
        row["Setup %"] = _compute_setup_percent(row)
    gate_allowed, gate_reason = evaluate_entry_gate(_paper_gate_row(row), EntryGateConfig(min_rr=controls["min_rr"], min_setup_percent=controls["min_setup"], min_option_quality=DEFAULT_AUTO_PAPER_MIN_OPTION_QUALITY, max_spread_pct=DEFAULT_AUTO_PAPER_MAX_SPREAD_PCT), mode="paper")
    if not gate_allowed:
        return False, gate_reason
    if not realtime_ready and not review_validation_candidate:
        return False, row.get("Realtime Block Reason") or "realtime not ready"
    if _safe_float(row.get("Option Bid"), 0) <= 0 or _safe_float(row.get("Option Ask"), 0) <= 0:
        return False, "missing option bid/ask"
    spread_blocked, spread_reason = spread_cost_exceeds_risk(row)
    if spread_blocked:
        return False, spread_reason
    if _boolish(row.get("Event Blocked")):
        return False, "event blocked"
    if _boolish(row.get("Regime Blocked")):
        return False, "regime blocked"
    direction = row.get("Candidate Direction")
    if controls["direction"] == "Calls" and direction != "CALL":
        return False, "calls only"
    if controls["direction"] == "Puts" and direction != "PUT":
        return False, "puts only"
    symbol = row.get("Symbol")
    if has_active_symbol_trade(paper_trades, symbol):
        return False, "ALREADY_HOLDING_NO_ADDITIONAL_ENTRY"
    cooldown_minutes = env_int("AUTO_PAPER_SYMBOL_COOLDOWN_MINUTES", 60)
    if is_symbol_in_cooldown(symbol, _closed_paper_trades(paper_trades), now_et, cooldown_minutes):
        return False, "SYMBOL_COOLDOWN_ACTIVE"
    if symbol_trade_count_today(paper_trades, symbol, now_et) >= env_int("MAX_TRADES_PER_SYMBOL_PER_DAY", 1):
        return False, "MAX_TRADES_PER_SYMBOL_PER_DAY_REACHED"
    open_trades = [trade for trade in paper_trades.values() if trade.get("status") == "OPEN"]
    if len(open_trades) >= 3:
        return False, "MAX_ACTIVE_PAPER_TRADES_REACHED"
    if len([trade for trade in open_trades if trade.get("direction") == direction]) >= 1:
        return False, "DIRECTION_ALREADY_ACTIVE"
    if _auto_paper_trade_count_today(paper_trades) >= controls["max_daily"]:
        return False, "DAILY_AUTO_PAPER_LIMIT_REACHED"
    if review_validation_candidate:
        return True, "REVIEW_TV_CHART_VALIDATION_ELIGIBLE"
    return True, gate_reason


def build_paper_rule_evaluations(row, allowed, reason, scan_id):
    from app.gates.rule_evaluation import RuleEvaluation

    return [
        RuleEvaluation(
            scan_id,
            str(row.get("Symbol") or ""),
            row.get("Entry"),
            "Paper Eligibility",
            "Paper",
            reason,
            "ELIGIBLE",
            bool(allowed),
            # Operational outcome, not an entry gate: Paper records what execution
            # did with the recommendation, so it never blocks the trade decision.
            False,
            50,
        )
    ]


def _scanner_block_reason(row):
    for column in ["Option Rejection Reason", "Realtime Block Reason", "Action Reason", "Regime Block Reason", "Event Block Reason", "Blocked By"]:
        value = row.get(column)
        if value is not None and str(value).strip() not in ["", "nan", "None"]:
            return str(value)
    return "auto paper enabled; no eligible entry candidate"


def _decision_log_rows(df):
    if df.empty or "Symbol" not in df.columns:
        return pd.DataFrame()
    return df[df["Symbol"].notna()].copy()


def eod_force_close_reason(trade, controls, now=None):
    """Holding-policy end-of-day reason for an open paper trade, or None.

    This is a session policy, not a market exit rule. All market exit decisions
    (stop, target, EMA, VWAP, MACD, failed breakout, time, profit protection)
    belong to app/exit/exit_engine.py::evaluate_exit() and are applied by the
    scanner's per-symbol loop.
    """

    if not (controls or {}).get("eod_close_enabled", False):
        return None
    if (now or _current_et()).time() < AUTO_PAPER_EOD_CLOSE:
        return None
    if not holding_policy(trade.get("holding_profile")).force_eod_exit:
        return None
    return "Auto paper exit: end-of-day close"


def _close_paper_trade(symbol, close_price, scanner_context=None, exit_reason="Manual dashboard paper exit"):
    from app.state.paper_trade_manager import close_paper_trade
    return close_paper_trade(symbol, close_price=close_price, exit_reason=exit_reason, scanner_context=scanner_context)