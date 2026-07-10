from pathlib import Path
import os
import re
import sys
from datetime import datetime, time
from html import escape
from zoneinfo import ZoneInfo
import json
from io import BytesIO

import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.utils.streamlit_env import sync_streamlit_secrets_to_env

sync_streamlit_secrets_to_env()


def _verify_app_imports():

    try:

        import app
        import app.config
        import app.gates
        import app.options
        import app.utils

        return app

    except Exception as exc:

        st.error(f"Failed to initialize application package imports: {exc}")
        st.stop()


_verify_app_imports()

from app.config.settings import settings
from app.gates import (
    EntryGateConfig,
    env_int,
    has_active_symbol_trade,
    is_symbol_in_cooldown,
    symbol_trade_count_today,
    evaluate_entry_gate,
    price_geometry_error
)
from app.options.option_affordability import add_affordability_metrics
from app.utils.json_store import (
    load_json_file,
    save_json_file
)
from app.storage.auto_paper_decision_store import (
    append_daily_auto_paper_decision,
    classify_decision_time,
    update_recent_auto_paper_log
)
from app.storage.daily_paths import daily_path, get_daily_dir
from app.storage.session_manager import get_scan_id, get_session_id, get_trading_day

try:

    from streamlit_autorefresh import st_autorefresh

except Exception:

    st_autorefresh = None


ROOT_DIR = Path(__file__).resolve().parents[1]

if str(ROOT_DIR) not in sys.path:

    sys.path.insert(
        0,
        str(ROOT_DIR)
    )


SCANNER_FILE = ROOT_DIR / "scanner_output.xlsx"
LIVE_SCANNER_FILE = ROOT_DIR / "data" / "live" / "scanner_output_latest.xlsx"
LIVE_SCANNER_CSV_FILE = ROOT_DIR / "data" / "live" / "scanner_output_latest.csv"
SCANNER_LOCK_FILE = ROOT_DIR / "data" / "live" / "scanner_run.lock"
SCANNER_STATUS_FILE = ROOT_DIR / "data" / "live" / "scanner_run_status.json"
SCANNER_LOCK_STALE_MINUTES = 10
TRADE_STATE_FILE = ROOT_DIR / "app" / "state" / "trade_state.json"
TELEMETRY_FILE = ROOT_DIR / "telemetry" / "trade_telemetry.csv"
PAPER_TRADE_STATE_FILE = ROOT_DIR / "app" / "state" / "paper_trade_state.json"
SUGGESTED_TRADE_STATE_FILE = ROOT_DIR / "app" / "state" / "suggested_trade_state.json"
AI_SUMMARY_CACHE_FILE = ROOT_DIR / settings.ai_summary_cache_file
AUTO_PAPER_DECISION_LOG_FILE = ROOT_DIR / "app" / "state" / "auto_paper_decision_log.json"
AUTO_PAPER_SETTINGS_FILE = ROOT_DIR / "app" / "state" / "auto_paper_settings.json"
SUGGESTED_TRADE_STATE_FILE = ROOT_DIR / "app" / "state" / "suggested_trade_state.json"

REFRESH_INTERVALS = {
    "1 min": 1,
    "5 min": 5,
    "15 min": 15
}

SCANNER_CADENCE_INTERVALS = {
    "5 min": 5,
    "15 min": 15
}

AI_TOP_CANDIDATES = {
    "BULLISH_TOP_1",
    "BULLISH_TOP_2",
    "BULLISH_TOP_3",
    "BEARISH_TOP_1",
    "BEARISH_TOP_2",
    "BEARISH_TOP_3"
}

AUTO_PAPER_TOP_CANDIDATES = AI_TOP_CANDIDATES
INVALID_NEW_ENTRY_TYPES = {
    "",
    "NAN",
    "NONE",
    "NO_ENTRY",
    "NO_SETUP",
    "ACTIVE_TRADE",
    "PAPER_TRADE",
    "OPEN_TRADE"
}
INDEX_REVIEW_VALIDATION_SYMBOLS = {"SPY", "QQQ"}
REVIEW_VALIDATION_ENTRY_TYPES = {
    "EMA_PULLBACK",
    "VWAP_RECLAIM",
    "BREAKOUT",
    "COILED_BREAKOUT",
    "HIGHER_LOW_CONTINUATION"
}
AUTO_PAPER_ENTRY_START = time(9, 45)
AUTO_PAPER_ENTRY_END = time(15, 30)
AUTO_PAPER_EOD_CLOSE = time(15, 55)
DEFAULT_AUTO_PAPER_MIN_RR = 1.8
DEFAULT_AUTO_PAPER_MIN_OPTION_QUALITY = 65.0
DEFAULT_AUTO_PAPER_MAX_SPREAD_PCT = 10.0
ET_TZ = "America/New_York"


def _is_valid_new_entry_type(value):

    entry_type = str(value or "").strip().upper()

    return entry_type not in INVALID_NEW_ENTRY_TYPES


def _env_bool(name, default=False):

    value = os.getenv(name)

    if value is None:

        return default

    return str(value).strip().lower() in [
        "1",
        "true",
        "yes",
        "y",
        "on"
    ]


def _env_float(name, default):

    value = os.getenv(name)

    if value is None:

        return default

    try:

        return float(str(value).strip())

    except Exception:

        return default


def _env_time(name, default_value):

    value = os.getenv(name)

    if value is None:

        return default_value

    try:

        hour, minute = str(value).strip().split(":", 1)
        return time(int(hour), int(minute))

    except Exception:

        return default_value


def _manual_paper_entries_enabled():

    return _env_bool(
        "ENABLE_MANUAL_PAPER_ENTRIES",
        False
    )


def _show_manual_paper_buttons():

    return _env_bool(
        "SHOW_MANUAL_PAPER_BUTTONS",
        False
    )


def _allow_manual_paper_close():

    return _env_bool(
        "ALLOW_MANUAL_PAPER_CLOSE",
        True
    )


def _allow_review_tv_chart_auto_paper():

    return _env_bool(
        "ALLOW_REVIEW_TV_CHART_AUTO_PAPER",
        False
    )


def _ignore_affordability_for_suggestions():

    return _env_bool(
        "SUGGESTIONS_IGNORE_AFFORDABILITY",
        True
    )


def _ignore_affordability_for_paper_validation():

    return _env_bool(
        "PAPER_IGNORE_AFFORDABILITY",
        True
    )


def _require_affordability_for_real_readiness():

    return _env_bool(
        "REAL_REQUIRE_AFFORDABILITY",
        True
    )


def _affordability_mask(df, ignore_affordability):

    if ignore_affordability or "Affordable" not in df.columns:

        return pd.Series(True, index=df.index)

    return (
        df["Affordable"].astype(str).str.lower().isin(["true", "1", "yes"])
        | (df["Affordable"] == True)
    )


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


def _high_quality_index_review_exception(row):

    symbol = str(row.get("Symbol") or "").strip().upper()
    entry_type = str(row.get("Entry") or "").strip().upper()
    action_status = str(row.get("Action Status") or "").strip().upper()
    quote_freshness = str(row.get("Option Quote Freshness") or "").strip().upper()

    if symbol not in INDEX_REVIEW_VALIDATION_SYMBOLS:

        return False

    if action_status != "REVIEW_TV_CHART":

        return False

    if entry_type not in REVIEW_VALIDATION_ENTRY_TYPES:

        return False

    if not _is_valid_new_entry_type(entry_type):

        return False

    if quote_freshness != "LIVE_QUOTE":

        return False

    setup = _safe_float(row.get("Setup %"), 0)
    rr = _safe_float(
        row.get("RR"),
        _safe_float(row.get("Candidate RR"), 0)
    )
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

    if str(row.get("Late Entry Risk") or "").strip().upper() == "LATE_CHASE_RISK":

        return False

    missed_move_type = str(row.get("Missed Move Type") or "").strip()

    if missed_move_type and missed_move_type.lower() not in {"nan", "none"}:

        return False

    if _boolish(row.get("Event Blocked")) or _boolish(row.get("Regime Blocked")):

        return False

    if price_geometry_error(row) is not None:

        return False

    return True


def _real_trading_enabled():

    return _env_bool(
        "REAL_TRADING_ENABLED",
        False
    )


def _real_alerts_only():

    return _env_bool(
        "REAL_ALERTS_ONLY",
        True
    )


TRADE_COLUMNS = [
    "Symbol",
    "Suggestion Status",
    "Suggestion Age Minutes",
    "Still Valid",
    "Invalidation Reason",
    "Exit Status",
    "Exit Reason Live",
    "Price",
    "Signal",
    "Top Candidate",
    "Recommended Option",
    "Short DTE Option",
    "Longer DTE Option",
    "Option Expiration",
    "Option Strike",
    "Option Moneyness",
    "Expiration Bucket",
    "Expiration Risk",
    "Early Watch Status",
    "Early Watch Reason",
    "Would Pass Gate If RR 1.7",
    "Would Pass Gate If Setup 65",
    "Would Pass Gate If Review Allowed",
    "Late Entry Risk",
    "Missed Move Type",
    "Paper Trade Opened",
    "Real Trade Readiness",
    "Real Review Scan Count",
    "Real Entry Checklist",
    "Option Quality Score",
    "Option Liquidity Grade",
    "Setup Grade",
    "Setup %",
    "Candidate Entry Price",
    "Candidate Stop Price",
    "Candidate Target Price",
    "Candidate Direction",
    "Action Status",
    "Action Reason",
    "Blocked By",
    "Option Rejection Reason",
    "Event Block Reason",
    "TradingView Check Status",
    "Realtime Confirmation Needed",
    "Realtime Ready",
    "Realtime Block Reason",
    "Stock Data Freshness",
    "Stock Data Age Minutes",
    "Market Data Delay Minutes",
    "RS Rank Score",
    "RS vs QQQ",
    "RS vs SPY",
    "Relative Volume",
    "ATR %",
    "Market Regime",
    "Regime Blocked",
    "Sector Strength",
    "Sector RS",
    "Option Quote Freshness",
    "Option Quote Age Minutes",
    "Option Bid",
    "Option Ask",
    "Option Midpoint",
    "Option Mid Price",
    "Option Spread %",
    "Option Delta",
    "Option Contract Cost",
    "Option Risk At Stop",
    "Current Capital",
    "Max Allowed Contract Cost",
    "Preferred Max Contract Cost",
    "Affordability Status",
    "Affordable",
    "Preferred Affordable",
    "Affordability Mode",
    "Capital Profile",
    "Best Quality Option Ticker",
    "Best Quality Contract Cost",
    "Best Quality Affordability Status",
    "Affordable Option Ticker",
    "Affordable Option Contract Cost",
    "Active Option Ticker",
    "Option Quote Timestamp",
    "Option Quote Timeframe",
    "Option Quote Source",
    "Event Blocked",
    "Strength Rank",
    "Weakness Rank",
    "RR",
    "Action",
    "Entry",
    "Next Trigger"
]


ACTIVE_TRADE_COLUMNS = [
    "Symbol",
    "Entry Price",
    "Current Price",
    "P/L %",
    "Option Entry Mid",
    "Option Current Mid",
    "Option P/L %",
    "Option P/L $",
    "Option Quality",
    "Quote Freshness",
    "Stop",
    "Target",
    "Exit Signal",
    "RR Progress",
    "Bars In Trade"
]


def _safe_float(value, default=0.0):

    try:

        if pd.isna(value):

            return default

        return float(value)

    except Exception:

        return default


def _normalize_signal(signal):

    signal = str(signal or "NEUTRAL").upper()

    if "BULLISH" in signal:

        return "BULLISH"

    if "BEARISH" in signal:

        return "BEARISH"

    return "NEUTRAL"


def _trend_from_signal(signal):

    normalized = _normalize_signal(signal)

    if normalized == "BULLISH":

        return "Bullish"

    if normalized == "BEARISH":

        return "Bearish"

    return "Neutral"


def _entry_is_valid(entry):

    return str(entry or "").upper() not in [
        "",
        "NAN",
        "NONE",
        "NO_ENTRY",
        "NO_SETUP"
    ]


def _compute_setup_percent(row):

    score = abs(_safe_float(row.get("15m Score")))
    rr = _safe_float(row.get("Risk Reward"))
    action = str(row.get("Action Status", "WAIT")).upper()
    entry = row.get("Entry")
    setup_valid = bool(row.get("Setup Valid", False))

    score_points = min(score / 10, 1) * 40
    rr_points = min(rr / 2.5, 1) * 25
    entry_points = 15 if _entry_is_valid(entry) else 0

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

    if action in [
        "AVOID",
        "NO_TRADE_MARKET_CLOSED",
        "OPTION_MARKET_CLOSED",
        "NO_BID_ASK",
        "NO_QUOTE_SNAPSHOT",
        "RATE_LIMITED",
        "PROVIDER_ERROR"
    ]:

        readiness = min(readiness, 49)

    return round(max(0, min(readiness, 100)), 0)


def _setup_grade(setup_pct):

    setup_pct = _safe_float(
        setup_pct,
        0
    )

    if setup_pct >= 82:

        return f"A+ ({int(round(setup_pct))})"

    if setup_pct >= 75:

        return f"A ({int(round(setup_pct))})"

    if setup_pct >= 65:

        return f"B ({int(round(setup_pct))})"

    return f"C ({int(round(setup_pct))})"


def _style_setup_grade(value):

    text = str(value or "")

    if text.startswith("A+"):

        return "background-color: #14532d; color: white; font-weight: 700"

    if text.startswith("A"):

        return "background-color: #166534; color: white; font-weight: 700"

    if text.startswith("B"):

        return "background-color: #854d0e; color: white; font-weight: 700"

    return "background-color: #7f1d1d; color: white; font-weight: 700"


def _option_moneyness(direction, underlying_price, strike):

    direction = str(
        direction or ""
    ).upper()
    underlying_price = _safe_float(
        underlying_price,
        None
    )
    strike = _safe_float(
        strike,
        None
    )

    if not direction or underlying_price is None or strike is None:

        return None

    distance_pct = abs(
        strike - underlying_price
    ) / underlying_price * 100

    if strike == underlying_price:

        return "ATM"

    if direction == "CALL":

        if strike > underlying_price:

            return (
                "NEAR_ATM_OTM"
                if distance_pct <= 1
                else "OTM"
            )

        return (
            "NEAR_ATM_ITM"
            if distance_pct <= 1
            else "ITM"
        )

    if direction == "PUT":

        if strike < underlying_price:

            return (
                "NEAR_ATM_OTM"
                if distance_pct <= 1
                else "OTM"
            )

        return (
            "NEAR_ATM_ITM"
            if distance_pct <= 1
            else "ITM"
        )

    return None


def _boolish(value):

    if isinstance(value, bool):

        return value

    return str(value).strip().lower() in {"true", "1", "yes"}


def _shadow_gate_allowed(row, min_rr=DEFAULT_AUTO_PAPER_MIN_RR, min_setup=70.0):

    try:

        gate_row = _paper_gate_row(row)

        gate_allowed, _ = evaluate_entry_gate(
            gate_row,
            EntryGateConfig(
                min_rr=min_rr,
                min_setup_percent=min_setup,
                min_option_quality=DEFAULT_AUTO_PAPER_MIN_OPTION_QUALITY,
                max_spread_pct=DEFAULT_AUTO_PAPER_MAX_SPREAD_PCT
            ),
            mode="paper"
        )
        return bool(gate_allowed)

    except Exception:

        return False


def _price_move_from_entry(row):

    price = _safe_float(row.get("Price"), None)
    entry = _safe_float(row.get("Candidate Entry Price"), None)

    if price is None or entry in [None, 0]:

        return None

    return abs(price - entry) / entry * 100


def _r_progress_from_row(row):

    direction = str(row.get("Candidate Direction") or "").upper()
    price = _safe_float(row.get("Price"), None)
    entry = _safe_float(row.get("Candidate Entry Price"), None)
    stop = _safe_float(row.get("Candidate Stop Price"), None)

    if direction not in ["CALL", "PUT"] or price is None or entry is None or stop is None:

        return None

    risk = abs(entry - stop)

    if risk <= 0:

        return None

    progress = price - entry if direction == "CALL" else entry - price
    return progress / risk


def _missed_move_type(row):

    direction = str(row.get("Candidate Direction") or "").upper()
    price = _safe_float(row.get("Price"), None)
    target = _safe_float(row.get("Candidate Target Price"), None)
    r_progress = _r_progress_from_row(row)

    if direction == "CALL" and price is not None and target is not None and price >= target:

        return "TARGET_ALREADY_TOUCHED"

    if direction == "PUT" and price is not None and target is not None and price <= target:

        return "TARGET_ALREADY_TOUCHED"

    if r_progress is not None and r_progress >= 1:

        return "MOVED_1R_WITHOUT_ENTRY"

    return None


def _early_watch_status_reason(row):

    direction = str(row.get("Candidate Direction") or "").upper()
    entry_type = str(row.get("Entry") or "").upper()
    signal = str(row.get("Signal") or row.get("Final Signal") or "").upper()
    setup = _safe_float(row.get("Setup %"), 0)
    rr = _safe_float(row.get("RR"), _safe_float(row.get("Risk Reward"), 0))
    move_from_entry = _price_move_from_entry(row)
    missed_move = _missed_move_type(row)

    if missed_move:

        return "MISSED_MOVE_DIAGNOSTIC", missed_move

    if move_from_entry is not None and move_from_entry >= 0.75:

        return "LATE_CHASE_RISK", f"price moved {round(move_from_entry, 2)}% from candidate entry"

    if direction == "CALL" and "VWAP" in entry_type:

        return "WATCH_VWAP_RECLAIM", "CALL setup near VWAP reclaim/rejection family"

    if direction == "PUT" and "VWAP" in entry_type:

        return "WATCH_VWAP_LOSS", "PUT setup near VWAP loss/rejection family"

    if direction == "CALL" and (
        "BREAKOUT" in entry_type
        or "BULLISH" in signal
        or setup >= 65
    ):

        return "WATCH_BREAKOUT_BUILDING", f"CALL setup building with setup={setup}, rr={rr}"

    if direction == "PUT" and (
        "BREAKDOWN" in entry_type
        or "BEARISH" in signal
        or setup >= 65
    ):

        return "WATCH_BREAKDOWN_BUILDING", f"PUT setup building with setup={setup}, rr={rr}"

    return None, None


def _add_shadow_diagnostics(df):

    if df.empty:

        return df

    output = df.copy()
    statuses = []
    reasons = []
    would_pass_gate_rr_17 = []
    would_pass_gate_setup_65 = []
    would_pass_gate_review_allowed = []
    late_entry_risks = []
    missed_move_types = []

    for _, row in output.iterrows():

        status, reason = _early_watch_status_reason(row)
        missed_move = _missed_move_type(row)
        action_status = str(row.get("Action Status") or "").upper()
        move_from_entry = _price_move_from_entry(row)

        statuses.append(status)
        reasons.append(reason)
        would_pass_gate_rr_17.append(_shadow_gate_allowed(row, min_rr=1.7, min_setup=70.0))
        would_pass_gate_setup_65.append(_shadow_gate_allowed(row, min_rr=DEFAULT_AUTO_PAPER_MIN_RR, min_setup=65.0))
        would_pass_gate_review_allowed.append(
            action_status == "REVIEW_TV_CHART"
            and _shadow_gate_allowed(row)
        )
        late_entry_risks.append(
            "LATE_CHASE_RISK"
            if move_from_entry is not None and move_from_entry >= 0.75
            else None
        )
        missed_move_types.append(missed_move)

    output["Early Watch Status"] = statuses
    output["Early Watch Reason"] = reasons
    output["Would Pass Gate If RR 1.7"] = would_pass_gate_rr_17
    output["Would Pass Gate If Setup 65"] = would_pass_gate_setup_65
    output["Would Pass Gate If Review Allowed"] = would_pass_gate_review_allowed
    output["Late Entry Risk"] = late_entry_risks
    output["Missed Move Type"] = missed_move_types

    return output


def _recommended_option_label(row):

    direction = row.get("Candidate Direction")
    strike = row.get("Option Strike")
    expiration = row.get("Option Expiration")

    if pd.isna(direction) or pd.isna(strike) or pd.isna(expiration):

        return None

    parts = [
        str(direction),
        str(strike),
        str(expiration)
    ]

    expiration_bucket = row.get("Expiration Bucket")

    moneyness = row.get("Option Moneyness")

    if not pd.isna(expiration_bucket):

        parts.append(str(expiration_bucket))

    if not pd.isna(moneyness):

        parts.append(str(moneyness))

    return " ".join(parts)


def _alternate_option_label(row, prefix):

    ticker = row.get(f"{prefix} Option Ticker")
    strike = row.get(f"{prefix} Strike")
    expiration = row.get(f"{prefix} Expiration")
    bucket = row.get(f"{prefix} Bucket")
    mid = row.get(f"{prefix} Mid Price")

    if pd.isna(ticker) or pd.isna(strike) or pd.isna(expiration):

        return None

    parts = [
        str(ticker),
        str(strike),
        str(expiration)
    ]

    if not pd.isna(bucket):

        parts.append(str(bucket))

    if not pd.isna(mid):

        parts.append(f"mid={mid}")

    return " ".join(parts)


def _has_value(value):

    try:

        if pd.isna(value):

            return False

    except Exception:

        pass

    return str(value).strip().lower() not in [
        "",
        "nan",
        "none"
    ]


def _affordability_contract_from_row(row):

    mid = row.get("Option Mid Price")

    if not _has_value(mid):

        mid = row.get("Option Midpoint")

    if not _has_value(mid):

        mid = row.get("Option Mid")

    bid = row.get("Option Bid")
    ask = row.get("Option Ask")

    if not any(
        _has_value(value)
        for value in [mid, bid, ask]
    ):

        return None

    return add_affordability_metrics(
        {
            "mid_price": mid,
            "quote_midpoint": row.get("Option Midpoint"),
            "bid": bid,
            "ask": ask,
            "delta": row.get("Option Delta")
        }
    )


def _backfill_affordability_columns(df):

    affordability_columns = {
        "Option Contract Cost": "contract_cost",
        "Option Risk At Stop": "risk_at_stop",
        "Current Capital": "current_capital",
        "Max Allowed Contract Cost": "max_allowed_contract_cost",
        "Preferred Max Contract Cost": "preferred_max_contract_cost",
        "Affordability Status": "affordability_status",
        "Affordable": "affordable",
        "Preferred Affordable": "preferred_affordable",
        "Affordability Mode": "affordability_mode",
        "Capital Profile": "capital_profile"
    }

    for column in affordability_columns:

        if column not in df.columns:

            df[column] = None

    if "Option Ticker" not in df.columns:

        df["Option Ticker"] = None

    for column in [
        "Best Quality Option Ticker",
        "Best Quality Contract Cost",
        "Best Quality Affordability Status",
        "Affordable Option Ticker",
        "Affordable Option Contract Cost"
    ]:

        if column not in df.columns:

            df[column] = None

    for index, row in df.iterrows():

        contract = _affordability_contract_from_row(row)

        if not contract:

            continue

        for column, key in affordability_columns.items():

            if _has_value(row.get(column)):

                continue

            df.at[index, column] = contract.get(key)

        if not _has_value(row.get("Best Quality Option Ticker")):

            df.at[index, "Best Quality Option Ticker"] = row.get(
                "Option Ticker"
            )

        if not _has_value(row.get("Best Quality Contract Cost")):

            df.at[index, "Best Quality Contract Cost"] = contract.get(
                "contract_cost"
            )

        if not _has_value(row.get("Best Quality Affordability Status")):

            df.at[
                index,
                "Best Quality Affordability Status"
            ] = contract.get("affordability_status")

        if contract.get("affordable") and not _has_value(
            row.get("Affordable Option Ticker")
        ):

            df.at[index, "Affordable Option Ticker"] = row.get(
                "Option Ticker"
            )
            df.at[index, "Affordable Option Contract Cost"] = contract.get(
                "contract_cost"
            )

    return df


def _load_scanner_output():

    scanner_file = (
        LIVE_SCANNER_CSV_FILE
        if LIVE_SCANNER_CSV_FILE.exists()
        else LIVE_SCANNER_FILE
        if LIVE_SCANNER_FILE.exists()
        else SCANNER_FILE
    )

    if not scanner_file.exists():

        return pd.DataFrame()

    try:

        if scanner_file.suffix.lower() == ".csv":

            df = pd.read_csv(scanner_file)

        else:

            df = pd.read_excel(scanner_file)

    except Exception as exc:

        bad_file = scanner_file.with_suffix(".bad.xlsx")

        try:

            scanner_file.replace(bad_file)

        except Exception:

            pass

        st.error(
            f"{scanner_file.name} is corrupted or was partially written. "
            f"Moved it aside if possible. Run scanner again. Error: {exc}"
        )

        return pd.DataFrame()

    if df.empty:

        return df

    df = df.copy()
    df["Signal"] = df.get("Final Signal", "NEUTRAL")
    df["RR"] = df.get("Risk Reward", 0)
    df["Action"] = df.get("Action Status", "WAIT")
    df["Next Trigger"] = df.get("Next Condition", "-")
    df["Setup %"] = df.apply(_compute_setup_percent, axis=1)
    df["Setup Grade"] = df["Setup %"].apply(_setup_grade)
    if "Option Strike" in df.columns:

        df["Option Moneyness"] = df.apply(
            lambda row: _option_moneyness(
                row.get("Candidate Direction"),
                row.get("Price"),
                row.get("Option Strike")
            ),
            axis=1
        )

    else:

        df["Option Moneyness"] = None

    df["Recommended Option"] = df.apply(
        _recommended_option_label,
        axis=1
    )
    df["Short DTE Option"] = df.apply(
        lambda row: _alternate_option_label(row, "Short DTE"),
        axis=1
    )
    df["Longer DTE Option"] = df.apply(
        lambda row: _alternate_option_label(row, "Longer DTE"),
        axis=1
    )
    df = _backfill_affordability_columns(df)
    df["Trend Phase"] = df["Signal"].apply(_trend_from_signal)
    df["Volume Score"] = df.get("Relative Volume", "N/A")
    df = _add_shadow_diagnostics(df)

    return df


def _candidate_rows_for_suggestions(df):

    if df.empty:

        return []

    required = [
        "Symbol",
        "Candidate Direction",
        "Setup Valid"
    ]

    if any(column not in df.columns for column in required):

        return []

    affordability_ok = _affordability_mask(
        df,
        _ignore_affordability_for_suggestions()
    )

    rows = df[
        (df["Setup Valid"] == True)
        & (df["Candidate Direction"].isin(["CALL", "PUT"]))
        & (df["Action Status"].isin(["REVIEW_TV_CHART", "ENTER", "ENTER_PAPER"]))
        & affordability_ok
    ].copy()

    rows = rows[
        rows["Entry"].map(_is_valid_new_entry_type)
    ].copy()

    if not rows.empty:

        rows = rows[
            rows.apply(
                lambda row: price_geometry_error(row) is None,
                axis=1
            )
        ]

    return [row for _, row in rows.iterrows()]


def _sync_suggested_trades(df):

    try:

        from app.state.suggested_trade_manager import (
            cleanup_old_suggestions,
            sync_suggestions_from_scan
        )

        sync_suggestions_from_scan(
            _candidate_rows_for_suggestions(df)
        )
        cleanup_old_suggestions()

    except Exception as exc:

        st.warning(
            f"Suggested trade sync failed: {exc}"
        )


def _real_review_scan_count(row):

    symbol = str(row.get("Symbol") or "")
    direction = str(row.get("Candidate Direction") or "")
    setup_type = str(row.get("Entry") or "")

    if not symbol or not direction or not setup_type:

        return 0

    try:

        from app.state.suggested_trade_manager import suggestions_as_list

        suggestions = suggestions_as_list()

    except Exception:

        suggestions = []

    scan_count = 0

    for suggestion in suggestions:

        if str(suggestion.get("symbol") or "") != symbol:

            continue

        if str(suggestion.get("direction") or "") != direction:

            continue

        if str(suggestion.get("setup_type") or "") != setup_type:

            continue

        status = str(suggestion.get("status") or "").upper()

        if status in ["EXPIRED_NOT_ENTERED", "CLOSED"]:

            continue

        scan_count = max(
            scan_count,
            int(suggestion.get("times_seen", 0) or 0)
        )

    return scan_count


def _daily_realized_real_pnl():

    try:

        from app.state.paper_trade_manager import load_paper_trades

        trades = load_paper_trades()

    except Exception:

        trades = {}

    trading_day = _current_trading_day()
    total = 0.0

    for trade in trades.values():

        if str(trade.get("trade_mode") or "").upper() != "REAL":

            continue

        if str(trade.get("status") or "").upper() != "CLOSED":

            continue

        closed_at = str(trade.get("closed_at") or "")

        if not closed_at.startswith(trading_day):

            continue

        realized = None

        for field in ["realized_pnl", "pnl_dollars", "option_pl_dollars"]:

            if trade.get(field) is not None:

                realized = _safe_float(trade.get(field), None)
                break

        if realized is None:

            risk_at_stop = _safe_float(
                (trade.get("scanner_context") or {}).get("Option Risk At Stop"),
                None
            )
            r_multiple = _safe_float(trade.get("r_multiple"), None)
            contracts = _safe_float(trade.get("option_contracts"), 1) or 1

            if risk_at_stop is not None and r_multiple is not None:

                realized = risk_at_stop * r_multiple * contracts

        if realized is not None:

            total += realized

    return round(total, 2)


def _real_loss_limit_reached():

    limit = _env_float("MAX_DAILY_REAL_LOSS", 1000.0)

    if limit <= 0:

        return False

    return _daily_realized_real_pnl() <= -abs(limit)


def _real_entry_checklist(row):

    if row.get("Real Trade Readiness") != "A_PLUS_REAL_REVIEW":

        return None

    return (
        "Real review only - no auto order; "
        "Confirm 5m candle close; "
        "Confirm price above/below VWAP/EMA; "
        "Confirm bid/ask still live; "
        "Confirm spread <= 8%; "
        "Confirm no late chase; "
        "Suggested max risk: $25-$50"
    )


def _real_trade_readiness(row):

    action_status = str(row.get("Action Status") or "").upper()
    top_candidate = row.get("Top Candidate")
    setup = _safe_float(row.get("Setup %"), 0)
    rr = _safe_float(row.get("RR"), 0)
    option_quality = _safe_float(row.get("Option Quality Score"), 0)
    spread = _safe_float(row.get("Option Spread %"), 999)
    quote_freshness = str(row.get("Option Quote Freshness") or "").upper()
    quote_age = _safe_float(row.get("Option Quote Age Minutes"), 999)

    if action_status not in ["ENTER", "ENTER_PAPER", "REVIEW_TV_CHART"]:

        return "NOT_REAL_READY"

    if _real_loss_limit_reached():

        return "PAPER_ONLY"

    if not _boolish(row.get("Paper Trade Opened")):

        return "PAPER_ONLY"

    if top_candidate not in ["BULLISH_TOP_1", "BEARISH_TOP_1"]:

        return "PAPER_ONLY"

    if setup < _env_float("REAL_MIN_SETUP", 88.0):

        return "PAPER_ONLY"

    if rr < _env_float("REAL_MIN_RR", 2.0):

        return "PAPER_ONLY"

    if option_quality < _env_float("REAL_MIN_OPTION_QUALITY", 90.0):

        return "PAPER_ONLY"

    if _require_affordability_for_real_readiness() and not _boolish(row.get("Affordable")):

        return "PAPER_ONLY_UNAFFORDABLE"

    if spread > _env_float("REAL_MAX_SPREAD_PCT", 8.0):

        return "PAPER_ONLY"

    if (
        quote_freshness != "LIVE_QUOTE"
        or quote_age > _env_float("REAL_MAX_QUOTE_AGE_MINUTES", 3.0)
    ):

        return "PAPER_ONLY"

    if str(row.get("Late Entry Risk") or "").upper() == "LATE_CHASE_RISK":

        return "PAPER_ONLY"

    missed_move_type = str(row.get("Missed Move Type") or "").strip()

    if missed_move_type and missed_move_type.lower() not in ["nan", "none"]:

        return "PAPER_ONLY"

    if _boolish(row.get("Event Blocked")) or _boolish(row.get("Regime Blocked")):

        return "PAPER_ONLY"

    if _real_review_scan_count(row) < 2:

        return "PAPER_ONLY"

    if _current_et().time() >= _env_time("REAL_ENTRY_CUTOFF_ET", time(14, 30)):

        return "PAPER_ONLY"

    return "A_PLUS_REAL_REVIEW"


def _add_real_trade_readiness(df):

    if df.empty:

        return df

    output = df.copy()
    output["Real Review Scan Count"] = output.apply(
        _real_review_scan_count,
        axis=1
    )
    output["Real Trade Readiness"] = output.apply(
        _real_trade_readiness,
        axis=1
    )
    output["Real Entry Checklist"] = output.apply(
        _real_entry_checklist,
        axis=1
    )

    return output


def _active_paper_symbols():

    try:

        from app.state.paper_trade_manager import load_paper_trades

        paper_trades = load_paper_trades()

    except Exception:

        paper_trades = {}

    return {
        str(trade.get("symbol") or "").strip()
        for trade in paper_trades.values()
        if trade.get("status") == "OPEN"
        and trade.get("symbol")
    }


def _add_paper_trade_opened(df):

    if df.empty or "Symbol" not in df.columns:

        return df

    output = df.copy()
    active_symbols = _active_paper_symbols()
    output["Paper Trade Opened"] = output["Symbol"].map(
        lambda symbol: str(symbol).strip() in active_symbols
    )

    return output


def _parse_suggestion_timestamp(value):

    try:

        timestamp = datetime.fromisoformat(str(value))

    except Exception:

        try:

            timestamp = datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")

        except Exception:

            return None

    if timestamp.tzinfo is None:

        timestamp = timestamp.replace(tzinfo=ZoneInfo("America/New_York"))

    return timestamp.astimezone(ZoneInfo("America/New_York"))


def _load_suggested_trades_df():

    try:

        from app.state.suggested_trade_manager import suggestions_as_list

        suggestions = suggestions_as_list()

    except Exception:

        suggestions = []

    if not suggestions:

        return pd.DataFrame()

    output = pd.DataFrame(suggestions)
    now = _current_et()

    def age_minutes(value):

        try:

            timestamp = _parse_suggestion_timestamp(value)

            if timestamp is None:

                return None

            return round(
                (
                    now - timestamp
                ).total_seconds() / 60,
                2
            )

        except Exception:

            return None

    output["suggestion_age_minutes"] = output.get(
        "first_seen_at",
        pd.Series(dtype=object)
    ).map(age_minutes)

    return output


def _enrich_with_suggestion_lifecycle(df):

    if df.empty:

        return df

    output = df.copy()
    suggestions = _load_suggested_trades_df()

    output["Suggestion Status"] = None
    output["Suggestion First Seen"] = None
    output["Suggestion Last Seen"] = None
    output["Suggestion Age Minutes"] = None
    output["Still Valid"] = False
    output["Invalidation Reason"] = None
    output["Exit Status"] = None
    output["Exit Reason Live"] = output.get("Live Exit Reason")

    if suggestions.empty or "symbol" not in suggestions.columns:

        return output

    latest_by_symbol = (
        suggestions.sort_values("last_seen_at")
        .groupby("symbol")
        .tail(1)
        .set_index("symbol")
    )

    for index, row in output.iterrows():

        symbol = row.get("Symbol")

        if symbol not in latest_by_symbol.index:

            continue

        suggestion = latest_by_symbol.loc[symbol]
        status = suggestion.get("status")
        output.at[index, "Suggestion Status"] = status
        output.at[index, "Suggestion First Seen"] = suggestion.get("first_seen_at")
        output.at[index, "Suggestion Last Seen"] = suggestion.get("last_seen_at")
        output.at[index, "Suggestion Age Minutes"] = suggestion.get("suggestion_age_minutes")
        output.at[index, "Still Valid"] = status in [
            "NEW_CALL",
            "NEW_PUT",
            "STILL_VALID_CALL",
            "STILL_VALID_PUT"
        ]
        output.at[index, "Invalidation Reason"] = suggestion.get("validity_reason")

    return output


def _load_trade_state():

    if not TRADE_STATE_FILE.exists():

        return {}

    try:

        import json

        with open(
            TRADE_STATE_FILE,
            "r",
            encoding="utf-8"
        ) as state_file:

            return json.load(state_file)

    except Exception:

        return {}


def _load_telemetry():

    if not TELEMETRY_FILE.exists():

        return pd.DataFrame()

    try:

        return pd.read_csv(
            TELEMETRY_FILE
        )

    except Exception:

        return pd.DataFrame()


def _display_safe_dataframe(df):

    if df is None:

        return pd.DataFrame()

    output = df.copy()

    for column in output.columns:

        if output[column].dtype == "object":

            output[column] = output[column].map(
                lambda value: None
                if pd.isna(value)
                else str(value)
            )

    return output


def _read_download_file(file_path):

    try:

        if not file_path.exists():

            return None

        return file_path.read_bytes()

    except Exception:

        return None


def _render_file_download_button(
    label,
    path,
    file_name=None,
    mime="text/plain",
    key=None,
    container=None
):

    container = container or st.sidebar
    file_path = Path(path)
    key_base = key or f"download_{file_path.name}"

    try:

        if not file_path.exists() or file_path.stat().st_size == 0:

            container.button(
                f"{label} - not available yet",
                disabled=True,
                key=f"missing_{key_base}"
            )
            return False

        stat = file_path.stat()
        container.download_button(
            label=label,
            data=file_path.read_bytes(),
            file_name=file_name or file_path.name,
            mime=mime,
            key=f"{key_base}_{stat.st_mtime_ns}"
        )
        return True

    except Exception as exc:

        container.button(
            f"{label} - unavailable",
            disabled=True,
            key=f"unavailable_{key_base}"
        )
        container.caption(str(exc))
        return False


def _dataframe_to_xlsx_bytes(df):

    try:

        buffer = BytesIO()

        with pd.ExcelWriter(
            buffer,
            engine="openpyxl"
        ) as writer:

            df.to_excel(
                writer,
                index=False
            )

        return buffer.getvalue()

    except Exception:

        return None


def _scanner_output_download_bytes():

    df = _load_scanner_output()

    if df.empty:

        return _read_download_file(SCANNER_FILE)

    data = _dataframe_to_xlsx_bytes(df)

    return data or _read_download_file(
        LIVE_SCANNER_FILE
        if LIVE_SCANNER_FILE.exists()
        else SCANNER_FILE
    )


def _load_auto_paper_decision_log():

    return load_json_file(
        str(AUTO_PAPER_DECISION_LOG_FILE),
        []
    )


def _load_auto_paper_settings():

    return load_json_file(
        str(AUTO_PAPER_SETTINGS_FILE),
        {}
    )


def _save_auto_paper_settings(settings_data):

    save_json_file(
        str(AUTO_PAPER_SETTINGS_FILE),
        settings_data
    )


def _save_auto_paper_decision_log(entries):

    save_json_file(
        str(AUTO_PAPER_DECISION_LOG_FILE),
        entries[-500:]
    )


def _record_auto_paper_decision(symbol, decision, reason, row=None, trade=None, controls=None):

    decision_time = _current_et()
    trading_day = get_trading_day(decision_time)
    scan_timestamp = decision_time.strftime("%Y-%m-%d %H:%M:%S")
    controls = controls or {}
    entry = {
        "timestamp": scan_timestamp,
        "trading_day": trading_day,
        "session_id": get_session_id(trading_day),
        "scan_id": get_scan_id(trading_day, decision_time),
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
        "realtime_ready": row.get("Realtime Ready") if row is not None else None,
        "affordable": row.get("Affordable") if row is not None else None,
        "paper_affordability_override": row.get("Paper Affordability Override") if row is not None else None,
        "original_affordable": row.get("Original Affordable") if row is not None else None,
        "original_affordability_status": row.get("Original Affordability Status") if row is not None else None,
        "original_option_contract_cost": row.get("Original Option Contract Cost") if row is not None else None,
        "original_max_allowed_contract_cost": row.get("Original Max Allowed Contract Cost") if row is not None else None,
        "price_geometry_ok": price_geometry_error(row) is None if row is not None else None,
        "price_geometry_error": price_geometry_error(row) if row is not None else None,
        "scanner_output_age_minutes": _scanner_output_age_minutes(),
        "allow_review_tv_chart_auto_paper": _allow_review_tv_chart_auto_paper(),
        "review_validation_candidate": (
            str(row.get("Action Status") or "").upper() == "REVIEW_TV_CHART"
            and _allow_review_tv_chart_auto_paper()
        ) if row is not None else None,
        "real_trading_enabled": _real_trading_enabled(),
        "real_alerts_only": _real_alerts_only(),
        "paper_trade_opened": row.get("Paper Trade Opened") if row is not None else None,
        "real_trade_readiness": row.get("Real Trade Readiness") if row is not None else None,
        "real_review_scan_count": row.get("Real Review Scan Count") if row is not None else None,
        "real_entry_checklist": row.get("Real Entry Checklist") if row is not None else None,
        "action_status": row.get("Action Status") if row is not None else None,
        "blocked_by": row.get("Blocked By") if row is not None else None,
        "action_reason": row.get("Action Reason") if row is not None else None,
        "option_rejection_reason": row.get("Option Rejection Reason") if row is not None else None,
        "realtime_block_reason": row.get("Realtime Block Reason") if row is not None else None,
        "option_quality_score": row.get("Option Quality Score") if row is not None else None,
        "option_spread_pct": row.get("Option Spread %") if row is not None else None,
        "option_quote_freshness": row.get("Option Quote Freshness") if row is not None else None,
        "expiration_bucket": row.get("Expiration Bucket") if row is not None else None,
        "early_watch_status": row.get("Early Watch Status") if row is not None else None,
        "early_watch_reason": row.get("Early Watch Reason") if row is not None else None,
        "would_pass_gate_if_rr_1_7": row.get("Would Pass Gate If RR 1.7") if row is not None else None,
        "would_pass_gate_if_setup_65": row.get("Would Pass Gate If Setup 65") if row is not None else None,
        "would_pass_gate_if_review_allowed": row.get("Would Pass Gate If Review Allowed") if row is not None else None,
        "late_entry_risk": row.get("Late Entry Risk") if row is not None else None,
        "missed_move_type": row.get("Missed Move Type") if row is not None else None
    }
    try:

        append_daily_auto_paper_decision(entry, get_daily_dir(trading_day))

    except Exception as exc:

        print(f"[AUTO PAPER LOG WARNING] daily CSV write failed: {exc}")

    try:

        update_recent_auto_paper_log(entry, AUTO_PAPER_DECISION_LOG_FILE)

    except Exception as exc:

        print(f"[AUTO PAPER LOG WARNING] recent JSON write failed: {exc}")


def _current_trading_day():

    try:

        return get_trading_day(
            datetime.now(ZoneInfo("America/New_York"))
        )

    except Exception:

        return datetime.now(
            ZoneInfo("America/New_York")
        ).date().isoformat()


def _latest_scanner_run(df):

    for column in ["Current ET", "Data Timestamp ET"]:

        if column in df.columns and not df[column].dropna().empty:

            return df[column].dropna().iloc[0]

    age_minutes = _scanner_output_age_minutes()

    if age_minutes is None:

        return "missing"

    return f"{age_minutes} minutes ago"


def _dashboard_market_session():

    now = datetime.now(ZoneInfo("America/New_York"))
    minutes = now.hour * 60 + now.minute

    if minutes < 4 * 60:

        return "CLOSED"
    if minutes < 9 * 60 + 30:

        return "PREMARKET"
    if minutes < 9 * 60 + 45:

        return "OPENING_RANGE"
    if minutes < 16 * 60:

        return "REGULAR"
    if minutes < 20 * 60:

        return "AFTERHOURS"
    return "CLOSED"


def _round_timestamp_15m(value):

    try:

        timestamp = pd.to_datetime(value)

        if pd.isna(timestamp):

            return "unknown_time"

        minute = (
            timestamp.minute // 15
        ) * 15

        rounded = timestamp.replace(
            minute=minute,
            second=0,
            microsecond=0
        )

        return rounded.strftime(
            "%Y-%m-%d %H:%M"
        )

    except Exception:

        return "unknown_time"


def _candidate_ai_cache_key(row):

    timestamp = (
        row.get("Data Timestamp ET")
        or row.get("Current ET")
        or "unknown_time"
    )

    parts = [
        row.get("Symbol"),
        row.get("Final Signal") or row.get("Signal"),
        row.get("Entry"),
        row.get("Top Candidate"),
        _round_timestamp_15m(timestamp)
    ]

    return "|".join(
        str(part or "NA")
        for part in parts
    )


def _ai_candidate_eligibility(row):

    if not settings.enable_ai_summary:

        return False, "ENABLE_AI_SUMMARY is false"

    if not settings.openai_api_key:

        return False, "OPENAI_API_KEY_APP is not set"

    if row.get("Top Candidate") not in AI_TOP_CANDIDATES:

        return False, "not a top 3 bullish/bearish candidate"

    if _safe_float(row.get("Setup %"), 0) < 70:

        return False, "setup below 70"

    if _safe_float(row.get("RR"), 0) < 2:

        return False, "RR below 2.0"

    if str(row.get("Action Status")) != "REVIEW_TV_CHART":

        return False, "action is not REVIEW_TV_CHART"

    if bool(row.get("Event Blocked")):

        return False, "event blocked"

    if bool(row.get("Regime Blocked")):

        return False, "regime blocked"

    return True, "eligible"


def _load_ai_summary_cache():

    return load_json_file(
        str(AI_SUMMARY_CACHE_FILE),
        {}
    )


def _save_ai_summary_cache(cache):

    save_json_file(
        str(AI_SUMMARY_CACHE_FILE),
        cache
    )


def _generate_candidate_ai_summary(row):

    cache_key = _candidate_ai_cache_key(row)
    cache = _load_ai_summary_cache()

    if cache_key in cache:

        return cache[cache_key], True

    try:

        from openai import OpenAI

        client = OpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.ai_request_timeout_seconds
        )

        prompt = f"""
You are summarizing one intraday options candidate for manual review.
Do not decide the trade. Keep it under 120 words.

Candidate:
Symbol: {row.get('Symbol')}
Signal: {row.get('Final Signal') or row.get('Signal')}
Setup: {row.get('Entry')}
Setup Grade: {row.get('Setup Grade')}
RR: {row.get('RR')}
Top Candidate: {row.get('Top Candidate')}
RS vs QQQ: {row.get('RS vs QQQ')}
RS vs SPY: {row.get('RS vs SPY')}
Market Regime: {row.get('Market Regime')}
Sector Strength: {row.get('Sector Strength')}
Recommended Option: {row.get('Recommended Option')}
Option Quality: {row.get('Option Liquidity Grade')}
Quote Freshness: {row.get('Option Quote Freshness')}
Blocked By: {row.get('Blocked By')}
Next Condition: {row.get('Next Condition') or row.get('Next Trigger')}

Return exactly these labels:
Direction:
Why candidate is valid:
What must confirm on TradingView:
Option-chain warning:
Skip reason:
"""

        response = client.chat.completions.create(
            model=settings.openai_dashboard_model,
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.2,
            max_tokens=settings.openai_summary_max_tokens
        )

        summary = response.choices[0].message.content.strip()
        cache[cache_key] = summary
        _save_ai_summary_cache(cache)

        return summary, False

    except Exception as exc:

        return f"AI summary unavailable: {exc}", False


def _render_download_exports():

    st.sidebar.subheader("Exports")

    exports = [
        {
            "label": "scanner_output.xlsx",
            "path": SCANNER_FILE,
            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        },
        {
            "label": "trade_telemetry.csv",
            "path": TELEMETRY_FILE,
            "mime": "text/csv"
        },
        {
            "label": "paper_trade_state.json",
            "path": PAPER_TRADE_STATE_FILE,
            "mime": "application/json"
        },
        {
            "label": "auto_paper_decision_log.json",
            "path": AUTO_PAPER_DECISION_LOG_FILE,
            "mime": "application/json"
        },
        {
            "label": "suggested_trade_state.json",
            "path": SUGGESTED_TRADE_STATE_FILE,
            "mime": "application/json"
        },
        {
            "label": "trade_state.json",
            "path": TRADE_STATE_FILE,
            "mime": "application/json"
        }
    ]

    for export in exports:

        if export["path"] == SCANNER_FILE:

            data = _scanner_output_download_bytes()

        else:

            data = _read_download_file(
                export["path"]
            )

        if data is None:

            _render_file_download_button(
                f"Download {export['label']}",
                export["path"],
                file_name=export["label"],
                mime=export["mime"],
                key=f"download_{export['label']}"
            )
            continue

        st.sidebar.download_button(
            label=f"Download {export['label']}",
            data=data,
            file_name=export["label"],
            mime=export["mime"],
            key=f"download_{export['label']}"
        )

    _render_daily_validation_report_controls()


def _render_daily_validation_report_controls():

    st.sidebar.subheader("Daily Validation")

    default_report_date = datetime.now(
        ZoneInfo("America/New_York")
    ).date().isoformat()
    report_date = st.sidebar.text_input(
        "Validation date",
        value=default_report_date,
        key="daily_validation_report_date"
    )
    finalize_report = st.sidebar.checkbox(
        "Finalize manifest",
        value=True,
        key="daily_validation_finalize_manifest",
        help="Use after market close. Uncheck for an intraday partial report."
    )

    if st.sidebar.button(
        "Generate Daily Validation Report",
        key="generate_daily_validation_report"
    ):

        try:

            from types import SimpleNamespace
            from tools.daily_validation_report import build_report

            output_path = build_report(
                SimpleNamespace(
                    date=report_date,
                    output=None,
                    archive=True,
                    update_daily=True,
                    finalize=finalize_report
                )
            )
            st.session_state["daily_validation_report_path"] = str(output_path)
            st.sidebar.success(
                "Daily validation report generated."
            )

        except Exception as exc:

            st.sidebar.error(
                "Report generation failed."
            )
            st.sidebar.text(str(exc))

    report_path = Path(
        st.session_state.get(
            "daily_validation_report_path",
            ROOT_DIR / "reports" / f"daily_validation_{report_date}.html"
        )
    )
    daily_report_path = (
        ROOT_DIR
        / "data"
        / "daily"
        / report_date
        / "daily_validation_report.html"
    )

    download_path = (
        daily_report_path
        if daily_report_path.exists()
        else report_path
    )
    _render_file_download_button(
        "Download daily_validation_report.html",
        download_path,
        file_name=f"daily_validation_{report_date}.html",
        mime="text/html",
        key=f"download_daily_validation_report_{report_date}"
    )

    _render_daily_artifact_downloads(report_date)


def _render_daily_artifact_downloads(report_date):

    daily_exports = [
        {
            "label": "full_auto_paper_decisions.csv",
            "path": daily_path(report_date, "auto_paper_decisions.csv"),
            "file_name": "auto_paper_decisions.csv",
            "mime": "text/csv"
        },
        {
            "label": "signal_lifecycle_events.csv",
            "path": daily_path(report_date, "signal_lifecycle_events.csv"),
            "file_name": "signal_lifecycle_events.csv",
            "mime": "text/csv"
        },
        {
            "label": "signal_state_transitions.csv",
            "path": daily_path(report_date, "signal_state_transitions.csv"),
            "file_name": "signal_state_transitions.csv",
            "mime": "text/csv"
        },
        {
            "label": "paper_trade_events.csv",
            "path": daily_path(report_date, "paper_trade_events.csv"),
            "file_name": "paper_trade_events.csv",
            "mime": "text/csv"
        },
        {
            "label": "candidate_snapshots.csv",
            "path": daily_path(report_date, "candidate_snapshots.csv"),
            "file_name": "candidate_snapshots.csv",
            "mime": "text/csv"
        },
        {
            "label": "candidate_snapshots.parquet",
            "path": daily_path(report_date, "candidate_snapshots.parquet"),
            "file_name": "candidate_snapshots.parquet",
            "mime": "application/octet-stream"
        },
        {
            "label": "scanner_output_close.csv",
            "path": daily_path(report_date, "scanner_output_close.csv"),
            "file_name": "scanner_output_close.csv",
            "mime": "text/csv"
        }
    ]

    st.sidebar.caption("Daily observability files")

    for export in daily_exports:

        _render_file_download_button(
            f"Download {export['label']}",
            export["path"],
            file_name=export["file_name"],
            mime=export["mime"],
            key=f"download_daily_{report_date}_{export['label']}"
        )


def _render_runtime_key_status():

    st.sidebar.subheader("Runtime Keys")

    polygon_key = os.getenv("POLYGON_API_KEY", "").strip()
    app_ai_key = os.getenv("OPENAI_API_KEY_APP", "").strip()

    st.sidebar.caption(
        "Polygon: loaded"
        if polygon_key
        else "Polygon: missing"
    )
    st.sidebar.caption(
        "App AI key: loaded"
        if app_ai_key
        else "App AI key: not set"
    )


def _is_market_hours():

    current_et = datetime.now(
        ZoneInfo("America/New_York")
    )

    if current_et.weekday() >= 5:

        return False

    return (
        time(9, 30)
        <= current_et.time()
        <= time(16, 0)
    )


def _scanner_output_age_minutes():

    scanner_file = (
        LIVE_SCANNER_CSV_FILE
        if LIVE_SCANNER_CSV_FILE.exists()
        else LIVE_SCANNER_FILE
        if LIVE_SCANNER_FILE.exists()
        else SCANNER_FILE
    )

    if not scanner_file.exists():

        return None

    modified_at = datetime.fromtimestamp(
        scanner_file.stat().st_mtime,
        tz=ZoneInfo("America/New_York")
    )

    current_et = datetime.now(
        ZoneInfo("America/New_York")
    )

    return round(
        (current_et - modified_at).total_seconds() / 60,
        2
    )


def _load_scanner_run_status():

    return load_json_file(
        str(SCANNER_STATUS_FILE),
        {}
    )


def _save_scanner_run_status(status):

    save_json_file(
        str(SCANNER_STATUS_FILE),
        status
    )


def _scanner_status_timestamp_age_minutes(field):

    status = _load_scanner_run_status()
    value = status.get(field)

    if not value:

        return None

    try:

        timestamp = datetime.fromisoformat(value)

        if timestamp.tzinfo is None:

            timestamp = timestamp.replace(
                tzinfo=ZoneInfo("America/New_York")
            )

        current_et = datetime.now(
            ZoneInfo("America/New_York")
        )

        return round(
            (current_et - timestamp.astimezone(ZoneInfo("America/New_York"))).total_seconds() / 60,
            2
        )

    except Exception:

        return None


def _scanner_recently_attempted(cadence_minutes):

    ages = [
        age for age in [
            _scanner_status_timestamp_age_minutes("last_started_at"),
            _scanner_status_timestamp_age_minutes("last_completed_at")
        ]
        if age is not None
    ]

    if not ages:

        return False

    return min(ages) < cadence_minutes


def _mark_scanner_started():

    status = _load_scanner_run_status()
    status.update({
        "status": "RUNNING",
        "last_started_at": datetime.now(ZoneInfo("America/New_York")).isoformat()
    })
    _save_scanner_run_status(status)


def _mark_scanner_completed(result_status="COMPLETED"):

    status = _load_scanner_run_status()
    status.update({
        "status": result_status,
        "last_completed_at": datetime.now(ZoneInfo("America/New_York")).isoformat()
    })
    _save_scanner_run_status(status)


def _scanner_lock_age_minutes():

    if not SCANNER_LOCK_FILE.exists():

        return None

    modified_at = datetime.fromtimestamp(
        SCANNER_LOCK_FILE.stat().st_mtime,
        tz=ZoneInfo("America/New_York")
    )
    current_et = datetime.now(
        ZoneInfo("America/New_York")
    )

    return round(
        (current_et - modified_at).total_seconds() / 60,
        2
    )


def _scanner_is_running():

    lock_age = _scanner_lock_age_minutes()

    if lock_age is None:

        return False

    if lock_age > SCANNER_LOCK_STALE_MINUTES:

        try:

            SCANNER_LOCK_FILE.unlink()

        except Exception:

            pass

        return False

    return True


def _acquire_scanner_lock():

    SCANNER_LOCK_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    try:

        lock_handle = os.open(
            str(SCANNER_LOCK_FILE),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY
        )

    except FileExistsError:

        if _scanner_is_running():

            return False

        try:

            SCANNER_LOCK_FILE.unlink()

        except Exception:

            return False

        return _acquire_scanner_lock()

    with os.fdopen(lock_handle, "w", encoding="utf-8") as file:

        file.write(
            datetime.now(ZoneInfo("America/New_York")).isoformat()
        )

    return True


def _release_scanner_lock():

    try:

        SCANNER_LOCK_FILE.unlink()

    except FileNotFoundError:

        pass

    except Exception:

        pass


def _auto_refresh_defaults():

    saved_auto_settings = _load_auto_paper_settings()

    if "auto_refresh_enabled" not in st.session_state:

        st.session_state["auto_refresh_enabled"] = _is_market_hours()

    if "refresh_interval_label" not in st.session_state:

        st.session_state["refresh_interval_label"] = "1 min"

    if "scanner_cadence_label" not in st.session_state:

        st.session_state["scanner_cadence_label"] = "5 min"

    if "last_auto_run_marker" not in st.session_state:

        st.session_state["last_auto_run_marker"] = None

    if "auto_paper_enabled" not in st.session_state:

        st.session_state["auto_paper_enabled"] = bool(
            saved_auto_settings.get(
                "auto_paper_enabled",
                _env_bool("AUTO_PAPER_ENABLED", True)
            )
        )

    if "auto_paper_max_daily" not in st.session_state:

        st.session_state["auto_paper_max_daily"] = int(
            saved_auto_settings.get(
                "auto_paper_max_daily",
                3
            )
        )

    if "auto_paper_min_setup" not in st.session_state:

        st.session_state["auto_paper_min_setup"] = int(
            saved_auto_settings.get(
                "auto_paper_min_setup",
                70
            )
        )

    if "auto_paper_min_rr" not in st.session_state:

        st.session_state["auto_paper_min_rr"] = float(
            saved_auto_settings.get(
                "auto_paper_min_rr",
                DEFAULT_AUTO_PAPER_MIN_RR
            )
        )

    if "auto_paper_direction" not in st.session_state:

        st.session_state["auto_paper_direction"] = saved_auto_settings.get(
            "auto_paper_direction",
            "Both"
        )

    if "auto_paper_exit_enabled" not in st.session_state:

        st.session_state["auto_paper_exit_enabled"] = bool(
            saved_auto_settings.get(
                "auto_paper_exit_enabled",
                True
            )
        )

    if "auto_paper_eod_close_enabled" not in st.session_state:

        st.session_state["auto_paper_eod_close_enabled"] = _boolish(
            saved_auto_settings.get(
                "auto_paper_eod_close_enabled",
                False
            )
        )

    if "auto_paper_profit_r" not in st.session_state:

        st.session_state["auto_paper_profit_r"] = float(
            saved_auto_settings.get(
                "auto_paper_profit_r",
                1.0
            )
        )



def _render_auto_paper_controls():

    st.sidebar.subheader("Paper Automation")

    auto_paper_enabled = st.sidebar.toggle(
        "Auto Paper Trading",
        key="auto_paper_enabled"
    )
    max_daily = st.sidebar.number_input(
        "Max Auto Paper Trades Per Day",
        min_value=1,
        max_value=10,
        step=1,
        key="auto_paper_max_daily"
    )
    min_setup = st.sidebar.number_input(
        "Minimum Setup %",
        min_value=0,
        max_value=100,
        step=1,
        key="auto_paper_min_setup"
    )
    min_rr = st.sidebar.number_input(
        "Minimum RR",
        min_value=0.0,
        max_value=10.0,
        step=0.1,
        key="auto_paper_min_rr"
    )
    direction = st.sidebar.selectbox(
        "Allowed Direction",
        options=["Both", "Calls", "Puts"],
        key="auto_paper_direction"
    )
    auto_exit_enabled = st.sidebar.toggle(
        "Auto Exit",
        key="auto_paper_exit_enabled"
    )
    eod_close_enabled = st.sidebar.toggle(
        "End-of-day Auto Close",
        key="auto_paper_eod_close_enabled"
    )

    profit_r = st.sidebar.number_input(
        "Auto Profit Exit R",
        min_value=0.5,
        max_value=5.0,
        step=0.25,
        key="auto_paper_profit_r"
    )

    st.sidebar.caption(
        "Paper only. Real orders remain manual."
    )

    controls = {
        "auto_paper_enabled": auto_paper_enabled,
        "max_daily": int(max_daily),
        "min_setup": float(min_setup),
        "min_rr": float(min_rr),
        "direction": direction,
        "auto_exit_enabled": auto_exit_enabled,
        "eod_close_enabled": eod_close_enabled,
        "profit_r": float(profit_r)
    }

    _save_auto_paper_settings({
        "auto_paper_enabled": controls["auto_paper_enabled"],
        "auto_paper_max_daily": controls["max_daily"],
        "auto_paper_min_setup": controls["min_setup"],
        "auto_paper_min_rr": controls["min_rr"],
        "auto_paper_direction": controls["direction"],
        "auto_paper_exit_enabled": controls["auto_exit_enabled"],
        "auto_paper_eod_close_enabled": controls["eod_close_enabled"],
        "auto_paper_profit_r": controls["profit_r"]
    })

    return controls



def _render_auto_refresh_controls():

    _auto_refresh_defaults()

    market_open = _is_market_hours()

    st.sidebar.subheader("Auto Refresh")

    auto_refresh_enabled = st.sidebar.toggle(
        "Auto Refresh",
        key="auto_refresh_enabled"
    )

    interval_label = st.sidebar.selectbox(
        "Refresh Interval",
        options=list(REFRESH_INTERVALS.keys()),
        key="refresh_interval_label"
    )

    interval_minutes = REFRESH_INTERVALS[interval_label]
    scanner_cadence_label = st.sidebar.selectbox(
        "Full Scanner Cadence",
        options=list(SCANNER_CADENCE_INTERVALS.keys()),
        key="scanner_cadence_label"
    )
    scanner_cadence_minutes = SCANNER_CADENCE_INTERVALS[
        scanner_cadence_label
    ]
    age_minutes = _scanner_output_age_minutes()

    session_label = (
        "OPEN"
        if market_open
        else "CLOSED"
    )

    st.sidebar.caption(
        f"Market hours: {session_label}"
    )

    if age_minutes is None:

        st.sidebar.caption(
            "Scanner output age: missing"
        )

    else:

        st.sidebar.caption(
            f"Scanner output age: {age_minutes} min"
        )

    if auto_refresh_enabled and st_autorefresh is None:

        st.sidebar.warning(
            "Install streamlit-autorefresh to enable timed dashboard refresh."
        )

    refresh_count = None

    if auto_refresh_enabled and st_autorefresh is not None:

        refresh_count = st_autorefresh(
            interval=interval_minutes * 60 * 1000,
            key="scanner_refresh"
        )

    should_run_scanner = (
        auto_refresh_enabled
        and not _scanner_is_running()
        and not _scanner_recently_attempted(scanner_cadence_minutes)
        and (
            age_minutes is None
            or age_minutes >= scanner_cadence_minutes
        )
    )

    return {
        "enabled": auto_refresh_enabled,
        "interval_minutes": interval_minutes,
        "scanner_cadence_minutes": scanner_cadence_minutes,
        "age_minutes": age_minutes,
        "refresh_count": refresh_count,
        "should_run_scanner": should_run_scanner
    }


def _maybe_auto_run_scanner(refresh_state):

    if not refresh_state["should_run_scanner"]:

        return

    scanner_file = (
        LIVE_SCANNER_CSV_FILE
        if LIVE_SCANNER_CSV_FILE.exists()
        else LIVE_SCANNER_FILE
        if LIVE_SCANNER_FILE.exists()
        else SCANNER_FILE
    )
    marker = (
        scanner_file.stat().st_mtime
        if scanner_file.exists()
        else "missing"
    )

    if st.session_state.get("last_auto_run_marker") == marker:

        return

    st.session_state["last_auto_run_marker"] = marker

    with st.spinner("Auto-running scanner..."):

        try:

            result = _run_scanner_once()

            if result.get("ran"):

                st.sidebar.success(
                    "Scanner auto-run completed."
                )

            else:

                st.sidebar.info(
                    "Scanner already running; showing latest available results."
                )

        except Exception as exc:

            st.sidebar.error(
                f"Scanner auto-run failed: {exc}"
            )


def _run_scanner_once():

    if not _acquire_scanner_lock():

        return {
            "ran": False,
            "reason": "SCANNER_ALREADY_RUNNING"
        }

    _mark_scanner_started()

    try:

        sync_streamlit_secrets_to_env()

        import importlib

        from app.config import settings as settings_module

        settings_module.settings = settings_module.get_settings()

        try:

            polygon_client = importlib.import_module(
                "app.utils.polygon_client"
            )
            polygon_client.POLYGON_API_KEY = (
                settings_module.settings.polygon_api_key
            )

        except Exception:

            pass

        from app.main import run_scanner

        run_scanner()

        _mark_scanner_completed("COMPLETED")

        return {
            "ran": True,
            "reason": "COMPLETED"
        }

    except Exception:

        _mark_scanner_completed("FAILED")
        raise

    finally:

        _release_scanner_lock()


def _scanner_context_from_row(row):

    context_fields = [
        "Setup Grade",
        "Setup %",
        "Final Signal",
        "15m Score",
        "Alignment Score",
        "Candidate Direction",
        "Candidate Entry Price",
        "Candidate Stop Price",
        "Candidate Target Price",
        "Candidate RR",
        "Candidate Trigger",
        "RS Rank Score",
        "RS vs QQQ",
        "RS vs SPY",
        "Relative Volume",
        "ATR %",
        "Market Regime",
        "Reference Regime",
        "Regime Blocked",
        "Regime Block Reason",
        "Sector Group",
        "Sector Reference",
        "Sector RS",
        "Sector Strength",
        "Strength Rank",
        "Weakness Rank",
        "Top 5 Strongest",
        "Top 5 Weakest",
        "Watchlist Advancers",
        "Watchlist Decliners",
        "Watchlist Breadth Score",
        "Above VWAP %",
        "Above EMA20 %",
        "Market Data Delay Minutes",
        "Realtime Confirmation Needed",
        "TradingView Check Status",
        "Option Ticker",
        "Option Strike",
        "Option Expiration",
        "Short DTE Option",
        "Short DTE Option Ticker",
        "Short DTE Expiration",
        "Short DTE Strike",
        "Short DTE Bucket",
        "Short DTE Mid Price",
        "Short DTE Spread %",
        "Short DTE Quality Score",
        "Short DTE Quote Freshness",
        "Longer DTE Option",
        "Longer DTE Option Ticker",
        "Longer DTE Expiration",
        "Longer DTE Strike",
        "Longer DTE Bucket",
        "Longer DTE Mid Price",
        "Longer DTE Spread %",
        "Longer DTE Quality Score",
        "Longer DTE Quote Freshness",
        "Option Mid Price",
        "Option Spread %",
        "Option Volume",
        "Option Open Interest",
        "Option Delta",
        "Option Theta",
        "Option IV",
        "Option Gamma",
        "Expiration Bucket",
        "Expiration Risk",
        "Option Quality Score",
        "Option Liquidity Grade",
        "Option Quality Reasons",
        "Option Quote Freshness",
        "Option Quote Age Minutes",
        "Option Contract Cost",
        "Option Risk At Stop",
        "Current Capital",
        "Max Allowed Contract Cost",
        "Preferred Max Contract Cost",
        "Affordability Status",
        "Affordable",
        "Preferred Affordable",
        "Affordability Mode",
        "Capital Profile",
        "Best Quality Option Ticker",
        "Best Quality Contract Cost",
        "Best Quality Affordability Status",
        "Affordable Option Ticker",
        "Affordable Option Contract Cost",
        "Paper Affordability Override",
        "Original Affordable",
        "Original Affordability Status",
        "Original Option Contract Cost",
        "Original Max Allowed Contract Cost",
        "Event Blocked",
        "Event Block Reason",
        "Action Status",
        "Blocked By",
        "Action Reason",
        "Next Condition",
        "Reasons"
    ]

    scanner_context = {
        field: row.get(field)
        for field in context_fields
        if field in row.index
    }

    return scanner_context


def _open_paper_trade_from_row(row):

    from app.state.paper_trade_manager import open_paper_trade
    from app.alerts.telegram_alerts import maybe_send_paper_entry_alert

    try:

        from app.state.suggested_trade_manager import promote_suggestion_to_paper_trade

    except Exception:

        promote_suggestion_to_paper_trade = None

    row_for_trade = _annotate_paper_affordability_override(row)
    scanner_context = _scanner_context_from_row(row_for_trade)

    opened_trade = open_paper_trade(
        symbol=row_for_trade.get("Symbol"),
        direction=row_for_trade.get("Candidate Direction"),
        entry_price=row_for_trade.get("Candidate Entry Price"),
        stop_loss=row_for_trade.get("Candidate Stop Price"),
        take_profit=row_for_trade.get("Candidate Target Price"),
        entry_type=row_for_trade.get("Entry"),
        option_ticker=row_for_trade.get("Option Ticker"),
        option_bid=row_for_trade.get("Option Bid"),
        option_ask=row_for_trade.get("Option Ask"),
        scanner_context=scanner_context,
        entry_source="MANUAL_PAPER",
        trade_mode="PAPER",
        include_in_strategy_stats=False
    )

    if promote_suggestion_to_paper_trade:

        promote_suggestion_to_paper_trade(
            symbol=row_for_trade.get("Symbol"),
            direction=row_for_trade.get("Candidate Direction"),
            setup_type=row_for_trade.get("Entry"),
            option_ticker=row_for_trade.get("Option Ticker"),
            opened_at=opened_trade.get("opened_at"),
            trade_key=opened_trade.get("trade_key")
        )

    maybe_send_paper_entry_alert(
        opened_trade,
        scanner_context,
        reason="Manual dashboard paper entry"
    )


def _close_paper_trade(
    symbol,
    close_price,
    scanner_context=None,
    exit_reason="Manual dashboard paper exit"
):

    from app.state.paper_trade_manager import close_paper_trade

    close_paper_trade(
        symbol,
        close_price=close_price,
        exit_reason=exit_reason,
        scanner_context=scanner_context
    )


def _current_et():

    return datetime.now(
        ZoneInfo("America/New_York")
    )


def _auto_paper_trade_count_today(paper_trades):

    today = _current_et().date()
    count = 0

    for trade in paper_trades.values():

        opened_at = trade.get("opened_at")

        if not opened_at:

            continue

        try:

            opened_date = datetime.strptime(
                opened_at,
                "%Y-%m-%d %H:%M:%S"
            ).date()

        except Exception:

            continue

        if (
            opened_date == today
            and str(trade.get("notes", "")).startswith("Auto paper")
        ):

            count += 1

    return count


def _open_paper_symbols(paper_trades):

    return {
        trade.get("symbol") for trade in paper_trades.values()
        if trade.get("status") == "OPEN"
        and trade.get("symbol")
    }


def _closed_paper_trades(paper_trades):

    return [
        trade for trade in (paper_trades or {}).values()
        if trade.get("status") == "CLOSED"
    ]


def _auto_paper_entry_reason(row, controls, paper_trades):

    now_et = _current_et()

    if not controls["auto_paper_enabled"]:

        return False, "auto paper disabled"

    if now_et.weekday() >= 5:

        return False, "market day closed"

    if not (
        AUTO_PAPER_ENTRY_START
        <= now_et.time()
        <= AUTO_PAPER_ENTRY_END
    ):

        return False, "outside auto-entry window"

    action_status = str(
        row.get("Action Status")
    ).strip().upper()

    realtime_ready = str(
        row.get("Realtime Ready")
    ).strip().lower() in [
        "true",
        "1",
        "yes"
    ]

    execution_ready = (
        action_status in ["ENTER", "ENTER_PAPER"]
        and realtime_ready
    )
    review_validation_candidate = (
        action_status == "REVIEW_TV_CHART"
        and _allow_review_tv_chart_auto_paper()
    )

    top_candidate = row.get("Top Candidate")

    if top_candidate not in AUTO_PAPER_TOP_CANDIDATES:

        if not _high_quality_index_review_exception(row):

            return False, "not top candidate"

    if _safe_float(row.get("Setup %"), None) is None:

        row = row.copy()
        row["Setup %"] = _compute_setup_percent(row)

    gate_row = _paper_gate_row(row)

    gate_allowed, gate_reason = evaluate_entry_gate(
        gate_row,
        EntryGateConfig(
            min_rr=controls["min_rr"],
            min_setup_percent=controls["min_setup"],
            min_option_quality=DEFAULT_AUTO_PAPER_MIN_OPTION_QUALITY,
            max_spread_pct=DEFAULT_AUTO_PAPER_MAX_SPREAD_PCT
        ),
        mode="paper"
    )

    if not gate_allowed:

        return False, gate_reason

    if review_validation_candidate:

        if now_et.time() >= time(14, 45):

            return False, "REVIEW_VALIDATION_TOO_LATE_IN_DAY"

        if str(row.get("Late Entry Risk") or "").upper() == "LATE_CHASE_RISK":

            return False, "REVIEW_VALIDATION_LATE_CHASE_RISK"

        missed_move_type = str(row.get("Missed Move Type") or "").strip()
        if missed_move_type and missed_move_type.lower() not in ["nan", "none"]:

            return False, "REVIEW_VALIDATION_MISSED_MOVE_ALREADY_HAPPENED"

        if top_candidate not in AUTO_PAPER_TOP_CANDIDATES and not _high_quality_index_review_exception(row):

            return False, "REVIEW_VALIDATION_NOT_TOP_CANDIDATE"

    if not realtime_ready and not review_validation_candidate:

        return False, row.get("Realtime Block Reason") or "realtime not ready"

    if _safe_float(row.get("Option Bid"), 0) <= 0 or _safe_float(row.get("Option Ask"), 0) <= 0:

        return False, "missing option bid/ask"

    if _boolish(row.get("Event Blocked")):

        return False, "event blocked"

    if _boolish(row.get("Regime Blocked")):

        return False, "regime blocked"

    if str(row.get("Blocked By")) in [
        "STALE_MARKET_DATA",
        "NO_5M_DATA",
        "SCANNER_ERROR"
    ]:

        return False, "market data blocked"

    if row.get("Expiration Bucket") not in [
        "PREFERRED_14_30",
        "FALLBACK_31_45",
        "SHORT_SWING_7_13"
    ]:

        return False, "expiration bucket not allowed"

    direction = row.get("Candidate Direction")

    if controls["direction"] == "Calls" and direction != "CALL":

        return False, "calls only"

    if controls["direction"] == "Puts" and direction != "PUT":

        return False, "puts only"

    symbol = row.get("Symbol")

    if has_active_symbol_trade(paper_trades, symbol):

        return False, "DUPLICATE_OPEN_SYMBOL"

    cooldown_minutes = env_int(
        "AUTO_PAPER_SYMBOL_COOLDOWN_MINUTES",
        60
    )

    if is_symbol_in_cooldown(
        symbol,
        _closed_paper_trades(paper_trades),
        now_et,
        cooldown_minutes
    ):

        return False, "SYMBOL_COOLDOWN_ACTIVE"

    max_trades_per_symbol = env_int(
        "MAX_TRADES_PER_SYMBOL_PER_DAY",
        1
    )

    if symbol_trade_count_today(
        paper_trades,
        symbol,
        now_et
    ) >= max_trades_per_symbol:

        return False, "MAX_TRADES_PER_SYMBOL_PER_DAY_REACHED"

    open_trades = [
        trade for trade in paper_trades.values()
        if trade.get("status") == "OPEN"
    ]

    if len(open_trades) >= 3:

        return False, "MAX_ACTIVE_PAPER_TRADES_REACHED"

    same_direction = [
        trade for trade in open_trades
        if trade.get("direction") == direction
    ]

    if len(same_direction) >= 1:

        return False, "DIRECTION_ALREADY_ACTIVE"

    if _auto_paper_trade_count_today(paper_trades) >= controls["max_daily"]:

        return False, "DAILY_AUTO_PAPER_LIMIT_REACHED"

    if review_validation_candidate:

        return True, "REVIEW_TV_CHART_VALIDATION_ELIGIBLE"

    return True, gate_reason


def _scanner_block_reason(row):

    action_status = str(
        row.get("Action Status")
    ).strip().upper()

    if action_status in [
        "ENTER",
        "ENTER_PAPER",
        "REVIEW_TV_CHART"
    ]:

        for column in [
            "Option Rejection Reason",
            "Realtime Block Reason",
            "Regime Block Reason",
            "Event Block Reason",
            "Blocked By",
            "Action Reason"
        ]:

            value = row.get(column)

            if value is not None and str(value).strip() not in [
                "",
                "nan",
                "None",
                action_status
            ]:

                return str(value)

        return "NO_AUTO_PAPER_CANDIDATE"

    for column in [
        "Option Rejection Reason",
        "Realtime Block Reason",
        "Action Reason",
        "Regime Block Reason",
        "Event Block Reason",
        "Blocked By",
        "Action Status"
    ]:

        value = row.get(column)

        if value is not None and str(value).strip() not in [
            "",
            "nan",
            "None"
        ]:

            return str(value)

    return "auto paper enabled; no eligible entry candidate"


def _decision_log_rows(df):

    if df.empty:

        return pd.DataFrame()

    rows = _last_seen_candidates(df)

    if not rows.empty:

        return rows

    if "Symbol" not in df.columns:

        return pd.DataFrame()

    output = df[
        df["Symbol"].notna()
    ].copy()

    if output.empty:

        return pd.DataFrame()

    return output


def _run_auto_paper_entries(df, controls):
    try:

        from app.state.paper_trade_manager import load_paper_trades

        paper_trades = load_paper_trades()

    except Exception:

        paper_trades = {}

    candidates = _paper_trade_candidates(df)

    if candidates.empty:

        log_rows = _decision_log_rows(df)

        if controls["auto_paper_enabled"]:

            if df.empty:

                _record_auto_paper_decision(
                    "SYSTEM",
                    "SKIPPED",
                    "auto paper enabled; scanner output empty",
                    controls=controls
                )

                return []

            market_closed_rows = pd.DataFrame()
            if "Action Status" in df.columns:

                market_closed_rows = df[
                    df["Action Status"].isin([
                        "NO_TRADE_MARKET_CLOSED",
                        "OPTION_MARKET_CLOSED"
                    ])
                ]

            if not market_closed_rows.empty:

                market_log_rows = _decision_log_rows(
                    market_closed_rows
                )

                if market_log_rows.empty:

                    market_log_rows = log_rows

                if not market_log_rows.empty:

                    for _, row in market_log_rows.iterrows():

                        _record_auto_paper_decision(
                            row.get("Symbol"),
                            "SKIPPED",
                            "market closed",
                            row,
                            controls=controls
                        )

                else:

                    _record_auto_paper_decision(
                        "SYSTEM",
                        "SKIPPED",
                        "auto paper enabled; market closed but no symbol rows found",
                        controls=controls
                    )

                return []

            if not log_rows.empty:

                for _, row in log_rows.iterrows():

                    _record_auto_paper_decision(
                        row.get("Symbol"),
                        "SKIPPED",
                        _scanner_block_reason(row),
                        row,
                        controls=controls
                    )

                return []

            _record_auto_paper_decision(
                "SYSTEM",
                "SKIPPED",
                "auto paper enabled; no eligible entry candidates and no symbol rows found",
                controls=controls
            )

            return []

        if not log_rows.empty:

            for _, row in log_rows.iterrows():

                _record_auto_paper_decision(
                    row.get("Symbol"),
                    "SKIPPED",
                    "auto paper disabled",
                    row,
                    controls=controls
                )

            return []

        _record_auto_paper_decision(
            "SYSTEM",
            "SKIPPED",
            "auto paper disabled; no current candidates and no symbol rows found",
            controls=controls
        )

        return []

    if not controls["auto_paper_enabled"]:

        for _, row in candidates.iterrows():

            _record_auto_paper_decision(
                row.get("Symbol"),
                "SKIPPED",
                "auto paper disabled",
                row,
                controls=controls
            )

        return []

    opened = []

    for _, row in candidates.iterrows():

        allowed, reason = _auto_paper_entry_reason(
            row,
            controls,
            paper_trades
        )

        if not allowed:

            _record_auto_paper_decision(
                row.get("Symbol"),
                "BLOCKED",
                reason,
                row,
                controls=controls
            )

            continue

        from app.state.paper_trade_manager import open_paper_trade
        from app.alerts.telegram_alerts import maybe_send_paper_entry_alert

        try:

            from app.state.suggested_trade_manager import promote_suggestion_to_paper_trade

        except Exception:

            promote_suggestion_to_paper_trade = None

        row_for_trade = _annotate_paper_affordability_override(row)
        scanner_context = _scanner_context_from_row(row_for_trade)
        is_review_validation = (
            str(row_for_trade.get("Action Status") or "").strip().upper() == "REVIEW_TV_CHART"
            and _allow_review_tv_chart_auto_paper()
        )
        entry_source = (
            "AUTO_PAPER_REVIEW_VALIDATION"
            if is_review_validation
            else "AUTO_PAPER"
        )
        notes_prefix = (
            "Auto paper review validation entry"
            if is_review_validation
            else "Auto paper entry"
        )
        spread_note = (
            "; missing spread allowed for paper"
            if _safe_float(row.get("Option Spread %"), None) is None
            else ""
        )
        opened_trade = open_paper_trade(
            symbol=row_for_trade.get("Symbol"),
            direction=row_for_trade.get("Candidate Direction"),
            entry_price=row_for_trade.get("Candidate Entry Price"),
            stop_loss=row_for_trade.get("Candidate Stop Price"),
            take_profit=row_for_trade.get("Candidate Target Price"),
            entry_type=row_for_trade.get("Entry"),
            option_ticker=row_for_trade.get("Option Ticker"),
            option_bid=row_for_trade.get("Option Bid"),
            option_ask=row_for_trade.get("Option Ask"),
            notes=f"{notes_prefix}: {reason}{spread_note}",
            scanner_context=scanner_context,
            entry_source=entry_source,
            trade_mode="PAPER",
            include_in_strategy_stats=not is_review_validation
        )
        paper_trades = load_paper_trades()
        opened.append(row.get("Symbol"))

        if promote_suggestion_to_paper_trade:

            promote_suggestion_to_paper_trade(
                symbol=row_for_trade.get("Symbol"),
                direction=row_for_trade.get("Candidate Direction"),
                setup_type=row_for_trade.get("Entry"),
                option_ticker=row_for_trade.get("Option Ticker"),
                opened_at=opened_trade.get("opened_at"),
                trade_key=opened_trade.get("trade_key")
            )

        telegram_entry_result = maybe_send_paper_entry_alert(
            opened_trade,
            scanner_context,
            reason=f"{notes_prefix}: {reason}"
        )
        opened_log_row = row_for_trade.copy()
        opened_log_row["Paper Trade Opened"] = True
        opened_log_row["Real Trade Readiness"] = _real_trade_readiness(opened_log_row)
        opened_log_row["Real Entry Checklist"] = _real_entry_checklist(opened_log_row)

        _record_auto_paper_decision(
            row.get("Symbol"),
            "TELEGRAM_ENTRY_ALERT",
            telegram_entry_result.get("reason"),
            opened_log_row,
            controls=controls
        )

        _record_auto_paper_decision(
            row.get("Symbol"),
            "OPENED",
            reason,
            opened_log_row,
            trade=opened_trade,
            controls=controls
        )

        if _auto_paper_trade_count_today(paper_trades) >= controls["max_daily"]:

            break

    return opened


def _is_swing_hold_eligible(trade, scanner_row):

    if scanner_row is None:

        return False

    expiration_bucket = str(scanner_row.get("Expiration Bucket") or "").upper()
    setup = _safe_float(scanner_row.get("Setup %"), 0)
    rr = _safe_float(scanner_row.get("RR"), 0)
    option_quality = _safe_float(scanner_row.get("Option Quality Score"), 0)

    if expiration_bucket not in ["PREFERRED_14_30", "LONGER_DTE"]:

        return False

    if setup < 80 or rr < 1.8 or option_quality < 75:

        return False

    if str(scanner_row.get("Late Entry Risk") or "").upper() == "LATE_CHASE_RISK":

        return False

    missed_move = str(scanner_row.get("Missed Move Type") or "").strip()
    if missed_move and missed_move.lower() not in ["nan", "none"]:

        return False

    if _boolish(scanner_row.get("Live Exit Signal")):

        return False

    return True


def _auto_exit_reason(trade, current_price, scanner_row, controls):

    if not controls["auto_exit_enabled"]:

        return None

    entry = _safe_float(trade.get("entry_price"), None)
    stop = _safe_float(trade.get("stop_loss"), None)
    target = _safe_float(trade.get("take_profit"), None)
    current = _safe_float(current_price, None)

    if entry is None or current is None:

        return None

    direction = _infer_trade_direction(
        trade.get("direction")
        or trade.get("entry_type")
    )

    if direction == "SHORT":

        if stop is not None and current >= stop:

            return "Auto paper exit: stop hit"

        if target is not None and current <= target:

            return "Auto paper exit: target hit"

    else:

        if stop is not None and current <= stop:

            return "Auto paper exit: stop hit"

        if target is not None and current >= target:

            return "Auto paper exit: target hit"

    if scanner_row is not None:

        if _boolish(scanner_row.get("Live Exit Signal")):

            return "Auto paper exit: live exit signal"

        live_exit_reason = str(scanner_row.get("Live Exit Reason") or "")
        if any(
            token in live_exit_reason.lower()
            for token in [
                "momentum",
                "vwap",
                "ema20",
                "failed breakout",
                "breakdown"
            ]
        ):

            return f"Auto paper exit: {live_exit_reason}"

    if _calculate_trade_r_progress(trade, current) >= controls.get(
        "profit_r",
        1.0
    ):

        return "Auto paper exit: profit threshold reached"

    if (
        controls["eod_close_enabled"]
        and _current_et().time() >= AUTO_PAPER_EOD_CLOSE
    ):

        if _is_swing_hold_eligible(trade, scanner_row):

            return None

        return "Auto paper exit: end-of-day close"

    return None


def _run_auto_paper_exits(df, controls):

    if not controls["auto_exit_enabled"]:

        return []

    try:

        from app.state.paper_trade_manager import load_paper_trades

        paper_trades = load_paper_trades()

    except Exception:

        paper_trades = {}

    current_prices = {}
    if not df.empty and "Symbol" in df.columns:

        current_prices = df.set_index("Symbol")["Price"].to_dict()

    closed = []

    for _, trade in paper_trades.items():

        symbol = trade.get("symbol")

        if trade.get("status") != "OPEN":

            continue

        current_price = current_prices.get(
            symbol,
            trade.get("entry_price")
        )
        scanner_row = None

        if not df.empty and "Symbol" in df.columns:

            matching_rows = df[df["Symbol"] == symbol]
            if not matching_rows.empty:

                scanner_row = matching_rows.iloc[0]

        reason = _auto_exit_reason(
            trade,
            current_price,
            scanner_row,
            controls
        )

        if not reason:

            continue

        scanner_context = (
            _scanner_context_from_row(scanner_row)
            if scanner_row is not None
            else None
        )
        _close_paper_trade(
            symbol,
            current_price,
            scanner_context=scanner_context,
            exit_reason=reason
        )
        closed.append(symbol)

    return closed


def _render_auto_paper_decision_log(show_full_expander=True):

    entries = _load_auto_paper_decision_log()

    if not entries:

        st.info("No auto-paper decisions logged yet.")
        return

    decisions = pd.DataFrame(entries)

    if "decision" in decisions.columns:

        counts = (
            decisions["decision"]
            .fillna("UNKNOWN")
            .astype(str)
            .str.upper()
            .value_counts()
        )
        cols = st.columns(3)
        cols[0].metric("OPENED", int(counts.get("OPENED", 0)))
        cols[1].metric("BLOCKED", int(counts.get("BLOCKED", 0)))
        cols[2].metric("SKIPPED", int(counts.get("SKIPPED", 0)))

    reason_column = None

    for candidate_column in ["reason", "blocked_by", "action_reason"]:

        if candidate_column in decisions.columns:

            reason_column = candidate_column
            break

    if reason_column:

        top_reasons = (
            decisions[reason_column]
            .fillna("UNKNOWN")
            .astype(str)
            .value_counts()
            .head(5)
            .rename_axis("Reason")
            .reset_index(name="Count")
        )
        st.dataframe(
            _display_safe_dataframe(top_reasons),
            width="stretch",
            hide_index=True
        )

    recent = pd.DataFrame(entries[-50:])

    if show_full_expander:

        with st.expander("Full auto-paper decision log", expanded=False):

            st.dataframe(
                _display_safe_dataframe(recent.iloc[::-1]),
                width="stretch",
                hide_index=True
            )

        return

    st.dataframe(
        _display_safe_dataframe(recent.iloc[::-1]),
        width="stretch",
        hide_index=True
    )


def _build_trade_opportunities(df):

    if df.empty:

        return pd.DataFrame(columns=TRADE_COLUMNS)

    columns = [
        column for column in TRADE_COLUMNS
        if column in df.columns
    ]

    opportunities = df[columns].copy()

    sort_columns = [
        column for column in [
            "Top Candidate",
            "Setup %",
            "RS Rank Score",
            "RR"
        ]
        if column in opportunities.columns
    ]

    ascending = [
        column == "Top Candidate"
        for column in sort_columns
    ]

    if sort_columns:

        opportunities = opportunities.sort_values(
            by=sort_columns,
            ascending=ascending,
            na_position="last"
        )

    return opportunities


def _reason_not_entered(row):

    geometry_error = price_geometry_error(row)

    if geometry_error:

        return geometry_error

    for column in [
        "Realtime Block Reason",
        "Option Rejection Reason",
        "Blocked By",
        "Action Reason",
        "Do Not Enter Reason",
        "Action Status"
    ]:

        try:

            value = row.get(column)

        except Exception:

            value = None

        if value is not None and str(value).strip().lower() not in {
            "",
            "nan",
            "none",
            "eligible"
        }:

            return value

    if str(row.get("Realtime Ready", "")).lower() not in {"true", "1", "yes"}:

        return "REVIEW_ONLY_NOT_REALTIME_READY"

    return "REVIEW_ONLY_NOT_ENTERED"


def _new_calls_puts(df):

    rows = _candidate_rows_for_suggestions(df)

    if not rows:

        return pd.DataFrame()

    output = pd.DataFrame(rows).copy()
    output["Status"] = output["Candidate Direction"].map(
        lambda direction: "NEW_CALL" if direction == "CALL" else "NEW_PUT"
    )
    output["Review Badge"] = "REVIEW ONLY - NOT ENTERED"
    output["Reason Not Entered"] = output.apply(
        _reason_not_entered,
        axis=1
    )
    columns = [
        "Symbol",
        "Candidate Direction",
        "Status",
        "Review Badge",
        "Reason Not Entered",
        "Top Candidate",
        "Setup Grade",
        "Setup %",
        "RR",
        "Candidate Entry Price",
        "Candidate Stop Price",
        "Candidate Target Price",
        "Option Ticker",
        "Option Strike",
        "Option Expiration",
        "Expiration Bucket",
        "Option Bid",
        "Option Ask",
        "Option Spread %",
        "Option Contract Cost",
        "Option Risk At Stop",
        "Max Allowed Contract Cost",
        "Affordability Status",
        "Affordable",
        "Option Quote Age Minutes",
        "Early Watch Status",
        "Early Watch Reason",
        "Would Pass Gate If RR 1.7",
        "Would Pass Gate If Setup 65",
        "Would Pass Gate If Review Allowed",
        "Late Entry Risk",
        "Missed Move Type",
        "Real Trade Readiness",
        "Real Review Scan Count",
        "Real Entry Checklist",
        "Realtime Ready",
        "Action Status"
    ]
    return output[[column for column in columns if column in output.columns]]


def _suggestions_with_status(status_filter):

    suggestions = _load_suggested_trades_df()

    if suggestions.empty:

        return pd.DataFrame()

    status = suggestions["status"].fillna("").astype(str).str.upper()
    rows = suggestions[
        status_filter(status)
    ].copy()

    if rows.empty:

        return pd.DataFrame()

    rows["reason_not_entered"] = rows.apply(
        lambda row: row.get("realtime_block_reason")
        or row.get("action_reason")
        or row.get("blocked_by")
        or row.get("validity_reason")
        or "review only; not entered",
        axis=1
    )

    columns = [
        "symbol",
        "direction",
        "status",
        "reason_not_entered",
        "validity_reason",
        "first_seen_at",
        "last_seen_at",
        "suggestion_age_minutes",
        "times_seen",
        "current_setup_percent",
        "current_rr",
        "current_action_status",
        "current_price",
        "recommended_option",
        "option_quality_score",
        "option_quote_freshness"
    ]
    return rows[[column for column in columns if column in rows.columns]]


def _still_valid_suggestions():

    return _suggestions_with_status(
        lambda status: status.str.startswith("STILL_VALID")
    )


def _expired_not_entered_suggestions():

    return _suggestions_with_status(
        lambda status: status.eq("EXPIRED_NOT_ENTERED")
    )


def _exit_now_alerts(df, controls):

    try:

        from app.state.paper_trade_manager import load_paper_trades
        paper_trades = load_paper_trades()

    except Exception:

        paper_trades = {}

    if not paper_trades:

        return pd.DataFrame()

    current_prices = {}
    if not df.empty and "Symbol" in df.columns:

        current_prices = df.set_index("Symbol")["Price"].to_dict()

    rows = []
    for _, trade in paper_trades.items():

        symbol = trade.get("symbol")

        if trade.get("status") != "OPEN":

            continue

        scanner_row = None
        if not df.empty and "Symbol" in df.columns:

            matching = df[df["Symbol"] == symbol]
            if not matching.empty:

                scanner_row = matching.iloc[0]

        current_price = current_prices.get(symbol, trade.get("entry_price"))
        reason = _auto_exit_reason(
            trade,
            current_price,
            scanner_row,
            controls
        )

        if not reason:

            continue

        if "stop" in reason.lower():

            status = "EXIT_NOW_STOP_HIT"

        elif "target" in reason.lower():

            status = "EXIT_NOW_TARGET_HIT"

        elif "profit" in reason.lower():

            status = "EXIT_NOW_PROFIT_R"

        elif "end-of-day" in reason.lower():

            status = "EXIT_NOW_EOD"

        elif "quote" in reason.lower():

            status = "EXIT_REVIEW_QUOTE_STALE"

        else:

            status = "EXIT_NOW_SETUP_INVALID"

        rows.append({
            "Symbol": symbol,
            "Direction": trade.get("direction"),
            "Exit Status": status,
            "Exit Reason": reason,
            "Entry Price": trade.get("entry_price"),
            "Current Price": current_price,
            "Stop": trade.get("stop_loss"),
            "Target": trade.get("take_profit"),
            "Live R": _calculate_trade_r_progress(trade, current_price)
        })

    return pd.DataFrame(rows)


def _style_trade_rows(row):

    setup_pct = _safe_float(row.get("Setup %"))
    rr = _safe_float(row.get("RR"))
    action = str(row.get("Action", "")).upper()

    if (
        setup_pct >= 80
        and rr >= 2
        and action in ["WATCH", "ENTER", "ENTER_PAPER", "REVIEW_TV_CHART"]
    ):

        color = "background-color: #14532d; color: white"

    elif setup_pct >= 60:

        color = "background-color: #713f12; color: white"

    else:

        color = "background-color: #7f1d1d; color: white"

    return [color] * len(row)


def _style_opportunities(opportunities):

    opportunities = _display_safe_dataframe(
        opportunities
    )

    return (
        opportunities
        .style
        .apply(
            _style_trade_rows,
            axis=1
        )
        .map(
            _style_setup_grade,
            subset=["Setup Grade"]
        )
    )


def _market_health(df):

    if df.empty:

        return {
            "SPY Trend": "N/A",
            "QQQ Trend": "N/A",
            "Market Breadth": "N/A",
            "Reference Regime": "N/A",
            "VIX Move %": "N/A",
            "Above VWAP %": "N/A",
            "Bullish Symbols": 0,
            "Bearish Symbols": 0
        }

    signals = df.set_index("Symbol")["Signal"].to_dict()

    bullish_count = sum(
        _normalize_signal(signal) == "BULLISH"
        for signal in df["Signal"]
    )

    bearish_count = sum(
        _normalize_signal(signal) == "BEARISH"
        for signal in df["Signal"]
    )

    if bullish_count > bearish_count:

        breadth = "Bullish"

    elif bearish_count > bullish_count:

        breadth = "Bearish"

    else:

        breadth = "Mixed"

    reference_regime = df.get(
        "Reference Regime",
        pd.Series(["N/A"])
    ).dropna()
    vix_move = df.get(
        "VIX Move %",
        pd.Series(["N/A"])
    ).dropna()
    above_vwap_pct = df.get(
        "Above VWAP %",
        pd.Series(["N/A"])
    ).dropna()

    return {
        "SPY Trend": _trend_from_signal(signals.get("SPY")),
        "QQQ Trend": _trend_from_signal(signals.get("QQQ")),
        "Market Breadth": breadth,
        "Reference Regime": (
            reference_regime.iloc[0]
            if not reference_regime.empty
            else "N/A"
        ),
        "VIX Move %": (
            vix_move.iloc[0]
            if not vix_move.empty
            else "N/A"
        ),
        "Above VWAP %": (
            above_vwap_pct.iloc[0]
            if not above_vwap_pct.empty
            else "N/A"
        ),
        "Bullish Symbols": bullish_count,
        "Bearish Symbols": bearish_count
    }


def _paper_trade_counts():

    try:

        from app.state.paper_trade_manager import load_paper_trades

        paper_trades = load_paper_trades()

    except Exception:

        paper_trades = {}

    open_count = sum(
        1 for trade in paper_trades.values()
        if trade.get("status") == "OPEN"
    )

    return open_count, _auto_paper_trade_count_today(paper_trades)


def _compact_value(value, max_len=28):

    if value is None:

        return "-"

    text = str(value)

    if text.lower() in ["nan", "none", ""]:

        return "-"

    return text if len(text) <= max_len else text[: max_len - 1] + "..."


def parse_market_timestamp(value):

    if value is None or pd.isna(value):

        return pd.NaT

    if isinstance(value, pd.Timestamp):

        timestamp = value

    else:

        text = str(value).strip()
        text = re.sub(
            r"\s+(EDT|EST)$",
            "",
            text,
            flags=re.IGNORECASE
        )
        timestamp = pd.to_datetime(
            text,
            errors="coerce"
        )

    if pd.isna(timestamp):

        return pd.NaT

    if timestamp.tzinfo is None:

        return timestamp.tz_localize(
            ET_TZ,
            ambiguous="NaT",
            nonexistent="shift_forward"
        )

    return timestamp.tz_convert(ET_TZ)


def _short_datetime(value):

    try:

        timestamp = parse_market_timestamp(value)

        if pd.isna(timestamp):

            return _compact_value(value)

        return timestamp.strftime("%m/%d %H:%M ET")

    except Exception:

        return _compact_value(value)


def _status_tone(value):

    text = str(value or "").upper()

    if text in ["ON", "BULLISH", "LIVE", "REVIEW ONLY", "REVIEW_ONLY"]:

        return "ok"

    if text in ["OFF", "CLOSED", "AFTERHOURS", "AFTER_HOURS"]:

        return "neutral"

    if text in ["BEARISH", "STALE", "BLOCKED"]:

        return "bad"

    if "RANGE" in text or "WAIT" in text:

        return "warn"

    return "neutral"


def _inject_compact_dashboard_css():

    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.3rem;
        }

        h1 {
            font-size: 2.1rem !important;
            margin-bottom: 0.3rem !important;
        }

        h2, h3 {
            font-size: 1.35rem !important;
            margin-top: 1.15rem !important;
            margin-bottom: 0.55rem !important;
        }

        .compact-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(135px, 1fr));
            gap: 0.55rem;
            margin: 0.35rem 0 1.1rem 0;
        }

        .compact-card {
            border: 1px solid rgba(148, 163, 184, 0.25);
            border-radius: 10px;
            padding: 0.45rem 0.6rem;
            background: rgba(255, 255, 255, 0.035);
            min-height: 52px;
        }

        .compact-label {
            font-size: 0.70rem;
            font-weight: 600;
            color: rgba(229, 231, 235, 0.70);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .compact-value {
            font-size: 0.92rem;
            line-height: 1.25;
            font-weight: 700;
            margin-top: 0.18rem;
            color: rgba(255, 255, 255, 0.94);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .compact-ok {
            border-left: 4px solid #22c55e;
        }

        .compact-warn {
            border-left: 4px solid #f59e0b;
        }

        .compact-bad {
            border-left: 4px solid #ef4444;
        }

        .compact-neutral {
            border-left: 4px solid #64748b;
        }
        </style>
        """,
        unsafe_allow_html=True
    )


def _render_compact_card_grid(cards):

    parts = ['<div class="compact-grid">']

    for label, value in cards:

        compact_value = _compact_value(value)
        tone = _status_tone(compact_value)
        parts.append(
            '<div class="compact-card compact-{tone}">'
            '<div class="compact-label">{label}</div>'
            '<div class="compact-value" title="{value}">{value}</div>'
            '</div>'.format(
                tone=escape(str(tone)),
                label=escape(str(label)),
                value=escape(str(compact_value)),
            )
        )

    parts.append("</div>")
    st.markdown(
        "".join(parts),
        unsafe_allow_html=True
    )


def _render_compact_status_cards(df, auto_paper_controls):

    latest_run = _latest_scanner_run(df)
    open_paper_count, today_opened_count = _paper_trade_counts()
    real_mode = (
        "REAL ON"
        if _real_trading_enabled()
        else "REVIEW ONLY"
        if _real_alerts_only()
        else "OFF"
    )
    cards = [
        ("Market Session", _dashboard_market_session()),
        ("Last Run", _short_datetime(latest_run)),
        ("Auto Paper", "ON" if auto_paper_controls.get("auto_paper_enabled") else "OFF"),
        ("Review Paper", "ON" if _allow_review_tv_chart_auto_paper() else "OFF"),
        ("EOD Close", "ON" if auto_paper_controls.get("eod_close_enabled") else "OFF"),
        ("Real Mode", real_mode),
        ("Open Paper", open_paper_count),
        ("Today Opened", today_opened_count),
    ]
    _render_compact_card_grid(cards)


def _render_compact_market_health(df):

    health = _market_health(df)
    st.subheader("Market Health")
    spy = health.get("SPY Trend")
    qqq = health.get("QQQ Trend")
    breadth = health.get("Market Breadth")
    regime = health.get("Reference Regime")
    above_vwap = health.get("Above VWAP %")
    bull_bear = f"{health.get('Bullish Symbols')} / {health.get('Bearish Symbols')}"

    if spy == "Bullish" and qqq == "Bullish" and breadth == "Bullish":

        bias = "BULLISH"

    elif spy == "Bearish" and qqq == "Bearish" and breadth == "Bearish":

        bias = "BEARISH"

    else:

        bias = "MIXED"

    cards = [
        ("Market Bias", bias),
        ("SPY / QQQ", f"{spy} / {qqq}"),
        ("Breadth", health.get("Market Breadth")),
        ("Regime", regime),
        ("Above VWAP", f"{above_vwap}%"),
        ("Bull / Bear", bull_bear),
    ]
    _render_compact_card_grid(cards)


def _real_review_candidates(df):

    if df.empty or "Real Trade Readiness" not in df.columns:

        return pd.DataFrame()

    rows = df[
        df["Real Trade Readiness"].astype(str).str.upper().eq("A_PLUS_REAL_REVIEW")
    ].copy()
    columns = [
        "Symbol",
        "Top Candidate",
        "Candidate Direction",
        "Setup Grade",
        "Setup %",
        "RR",
        "Option Quality Score",
        "Option Spread %",
        "Option Quote Freshness",
        "Option Quote Age Minutes",
        "Paper Trade Opened",
        "Real Review Scan Count",
        "Real Entry Checklist",
    ]

    return rows[[column for column in columns if column in rows.columns]]


def _eligible_auto_paper_candidates(df):

    candidates = _paper_trade_candidates(df)

    if candidates.empty:

        return candidates

    columns = [
        "Symbol",
        "Top Candidate",
        "Candidate Direction",
        "Setup Grade",
        "Setup %",
        "RR",
        "Action Status",
        "Real Trade Readiness",
        "Option Quality Score",
        "Option Quote Freshness",
        "Expiration Bucket",
        "Next Condition",
    ]

    return candidates[[column for column in columns if column in candidates.columns]]


def _render_action_center(df, auto_paper_controls):

    st.subheader("Action Center")

    if _real_loss_limit_reached():

        st.warning(
            "Daily real loss limit reached. No more real-review candidates today."
        )

    real_review = _real_review_candidates(df)
    auto_paper_candidates = _eligible_auto_paper_candidates(df)
    active_trades = _active_trades(df)
    exit_alerts = _exit_now_alerts(
        df,
        auto_paper_controls
    )
    has_action = any(
        not frame.empty
        for frame in [real_review, auto_paper_candidates, active_trades, exit_alerts]
    )

    if not has_action:

        st.info("No action needed right now.")
        return

    if not real_review.empty:

        st.markdown("**A+ Real Review Candidates**")
        st.dataframe(
            _display_safe_dataframe(real_review),
            width="stretch",
            hide_index=True
        )

    if not auto_paper_candidates.empty:

        st.markdown("**Eligible Auto-Paper Candidates**")
        st.dataframe(
            _display_safe_dataframe(auto_paper_candidates),
            width="stretch",
            hide_index=True
        )

    if not active_trades.empty:

        st.markdown("**Active Paper Trades**")
        st.dataframe(
            _display_safe_dataframe(active_trades),
            width="stretch",
            hide_index=True
        )

    if not exit_alerts.empty:

        st.markdown("**Exit Now Alerts**")
        st.dataframe(
            _display_safe_dataframe(exit_alerts),
            width="stretch",
            hide_index=True
        )


def _scanner_watchlist(df, limit=10):

    if df.empty:

        return pd.DataFrame()

    columns = [
        "Symbol",
        "Signal",
        "Top Candidate",
        "Real Trade Readiness",
        "Setup Grade",
        "RR",
        "Action Status",
        "Blocked By",
        "Next Trigger",
    ]
    rows = df[[column for column in columns if column in df.columns]].copy()
    top_priority = {
        "BULLISH_TOP_1": 1,
        "BEARISH_TOP_1": 1,
        "BULLISH_TOP_2": 2,
        "BEARISH_TOP_2": 2,
        "BULLISH_TOP_3": 3,
        "BEARISH_TOP_3": 3,
    }
    rows["_top_priority"] = rows.get(
        "Top Candidate",
        pd.Series(dtype=object)
    ).map(top_priority).fillna(99)

    if "RR" in rows.columns:

        rows["_rr_sort"] = pd.to_numeric(rows["RR"], errors="coerce").fillna(-1)

    else:

        rows["_rr_sort"] = -1

    rows = rows.sort_values(
        by=["_top_priority", "_rr_sort"],
        ascending=[True, False],
        na_position="last"
    )
    rows = rows.drop(
        columns=["_top_priority", "_rr_sort"],
        errors="ignore"
    )

    return rows.head(limit)


def _render_scanner_watchlist(df):

    st.subheader("Scanner Watchlist")
    watchlist = _scanner_watchlist(df)

    if watchlist.empty:

        st.info("No scanner watchlist rows right now.")
        return

    st.dataframe(
        _display_safe_dataframe(watchlist),
        width="stretch",
        hide_index=True
    )


def _latest_decisions_df(minutes=30):

    entries = _load_auto_paper_decision_log()

    if not entries:

        return pd.DataFrame()

    decisions = pd.DataFrame(entries)

    if "trading_day" in decisions.columns:

        decisions = decisions[
            decisions["trading_day"].astype(str).eq(_current_trading_day())
        ].copy()

    if decisions.empty or "timestamp" not in decisions.columns:

        return decisions

    timestamps = pd.to_datetime(
        decisions["timestamp"],
        errors="coerce"
    )
    cutoff = pd.Timestamp(_current_et().replace(tzinfo=None)) - pd.Timedelta(minutes=minutes)

    return decisions[
        timestamps >= cutoff
    ].copy()


def _render_compact_auto_paper_summary():

    st.subheader("Paper / Real Validation Summary")
    decisions = _latest_decisions_df(minutes=30)

    if decisions.empty or "decision" not in decisions.columns:

        st.info("No recent auto-paper decisions in the latest 30 minutes.")
        return

    counts = (
        decisions["decision"]
        .fillna("UNKNOWN")
        .astype(str)
        .str.upper()
        .value_counts()
    )
    cols = st.columns(4)
    cols[0].metric("Opened", int(counts.get("OPENED", 0)))
    cols[1].metric("Blocked", int(counts.get("BLOCKED", 0)))
    cols[2].metric("Skipped", int(counts.get("SKIPPED", 0)))

    reason = "N/A"

    if "reason" in decisions.columns and not decisions["reason"].dropna().empty:

        reason = decisions["reason"].fillna("UNKNOWN").astype(str).value_counts().index[0]

    cols[3].metric("Top Reason", reason)



def _read_csv_safe(path):

    try:

        path = Path(path)

        if not path.exists() or path.stat().st_size == 0:

            return pd.DataFrame()

        return pd.read_csv(path)

    except Exception:

        return pd.DataFrame()


def _paper_trade_state_paths():

    paths = []
    state_path = ROOT_DIR / "app" / "state" / "paper_trade_state.json"

    if state_path.exists():

        paths.append(state_path)

    daily_root = ROOT_DIR / "data" / "daily"

    if daily_root.exists():

        paths.extend(
            sorted(daily_root.glob("*/paper_trade_state.json"))
        )

    return paths


def _load_paper_trade_state_records():

    records = {}

    for path in _paper_trade_state_paths():

        try:

            payload = load_json_file(
                str(path),
                {}
            )

        except Exception:

            payload = {}

        if not isinstance(payload, dict):

            continue

        for fallback_key, trade in payload.items():

            if not isinstance(trade, dict):

                continue

            trade_key = str(
                trade.get("trade_key")
                or fallback_key
                or ""
            ).strip()

            if not trade_key:

                continue

            records[trade_key] = trade

    return records


def _trade_context_value(trade, *field_names):

    if not isinstance(trade, dict):

        return None

    scanner_context = trade.get("scanner_context") or {}
    close_scanner_context = trade.get("close_scanner_context") or {}

    for field_name in field_names:

        for source in [trade, scanner_context, close_scanner_context]:

            try:

                value = source.get(field_name)

            except Exception:

                value = None

            if _has_value(value):

                return value

    return None


def _paper_trade_risk_dollars(trade):

    risk_value = _trade_context_value(
        trade,
        "Option Risk At Stop",
        "option_risk_at_stop",
        "risk_at_stop"
    )
    risk_value = _safe_float(
        risk_value,
        None
    )

    if risk_value is not None and risk_value > 0:

        return risk_value

    account_size = _env_float(
        "ACCOUNT_SIZE",
        _env_float("DAILY_START_CAPITAL", 0.0)
    )
    risk_percent = _env_float(
        "RISK_PERCENT",
        _env_float("OPTION_MAX_RISK_PER_TRADE_PCT", 0.0) * 100
    )

    if account_size > 0 and risk_percent > 0:

        return round(
            account_size * risk_percent / 100,
            2
        )

    return None


def _paper_trade_contracts(trade):

    contracts = _trade_context_value(
        trade,
        "option_contracts",
        "contracts",
        "Contracts"
    )

    contracts = _safe_float(
        contracts,
        1
    )

    if contracts is None or contracts <= 0:

        return 1

    return contracts


def _estimated_trade_pnl_dollars(trade, r_multiple):

    direct_pnl = _trade_context_value(
        trade,
        "realized_pnl",
        "pnl_dollars",
        "option_pl_dollars",
        "Option P/L $"
    )
    direct_pnl = _safe_float(
        direct_pnl,
        None
    )

    if direct_pnl is not None:

        return round(
            direct_pnl,
            2
        )

    risk_dollars = _paper_trade_risk_dollars(trade)

    if risk_dollars is None or r_multiple is None:

        return None

    return round(
        risk_dollars * r_multiple * _paper_trade_contracts(trade),
        2
    )


def _paper_trade_event_paths():

    paths = []
    root_event_path = ROOT_DIR / "paper_trade_events.csv"

    if root_event_path.exists():

        paths.append(root_event_path)

    daily_root = ROOT_DIR / "data" / "daily"

    if daily_root.exists():

        paths.extend(
            sorted(daily_root.glob("*/paper_trade_events.csv"))
        )

    return paths


def _closed_paper_trade_history():

    frames = []

    for path in _paper_trade_event_paths():

        frame = _read_csv_safe(path)

        if frame.empty:

            continue

        frame = frame.copy()
        frame["_source_path"] = str(path)
        frames.append(frame)

    state_records = _load_paper_trade_state_records()

    if frames:

        events = pd.concat(
            frames,
            ignore_index=True,
            sort=False
        )

    else:

        events = pd.DataFrame()

    closed_rows = []

    if not events.empty:

        event_type = (
            events.get("event_type", pd.Series(dtype=object))
            .fillna("")
            .astype(str)
            .str.upper()
        )
        status = (
            events.get("status", pd.Series(dtype=object))
            .fillna("")
            .astype(str)
            .str.upper()
        )
        closed_mask = (
            event_type.isin([
                "AUTO_EXIT",
                "MANUAL_CLOSE",
                "CLOSE",
                "CLOSED",
                "EXIT"
            ])
            | status.eq("CLOSED")
        )
        closed_events = events[closed_mask].copy()

        if not closed_events.empty:

            closed_events["r_multiple"] = pd.to_numeric(
                closed_events.get("r_multiple"),
                errors="coerce"
            )
            closed_events = closed_events[
                closed_events["r_multiple"].notna()
            ].copy()

        for _, row in closed_events.iterrows():

            trade_key = str(row.get("trade_key") or "").strip()
            trade = state_records.get(trade_key, {})
            r_multiple = _safe_float(row.get("r_multiple"), None)
            trading_day = str(row.get("trading_day") or "").strip()

            if not trading_day:

                event_time = str(row.get("event_time") or "")
                trading_day = event_time[:10] if len(event_time) >= 10 else None

            closed_rows.append({
                "trade_key": trade_key,
                "trading_day": trading_day,
                "closed_at": row.get("event_time"),
                "symbol": row.get("symbol") or trade.get("symbol"),
                "direction": row.get("direction") or trade.get("direction"),
                "option_ticker": row.get("option_ticker") or trade.get("option_ticker"),
                "r_multiple": r_multiple,
                "estimated_pnl_dollars": _estimated_trade_pnl_dollars(trade, r_multiple),
                "exit_reason": row.get("exit_reason") or trade.get("exit_reason"),
                "paper_affordability_override": _trade_context_value(
                    trade,
                    "Paper Affordability Override",
                    "paper_affordability_override"
                ),
                "source": "event_log"
            })

    seen_trade_keys = {
        str(row.get("trade_key") or "")
        for row in closed_rows
        if row.get("trade_key")
    }

    for trade_key, trade in state_records.items():

        if trade_key in seen_trade_keys:

            continue

        if str(trade.get("status") or "").upper() != "CLOSED":

            continue

        r_multiple = _safe_float(
            trade.get("r_multiple"),
            None
        )

        if r_multiple is None:

            continue

        closed_at = str(
            trade.get("closed_at")
            or trade.get("closed_at_et")
            or ""
        )
        trading_day = str(
            trade.get("trading_day")
            or closed_at[:10]
            or ""
        )

        closed_rows.append({
            "trade_key": trade_key,
            "trading_day": trading_day,
            "closed_at": closed_at,
            "symbol": trade.get("symbol"),
            "direction": trade.get("direction"),
            "option_ticker": trade.get("option_ticker"),
            "r_multiple": r_multiple,
            "estimated_pnl_dollars": _estimated_trade_pnl_dollars(trade, r_multiple),
            "exit_reason": trade.get("exit_reason"),
            "paper_affordability_override": _trade_context_value(
                trade,
                "Paper Affordability Override",
                "paper_affordability_override"
            ),
            "source": "paper_state"
        })

    if not closed_rows:

        return pd.DataFrame()

    history = pd.DataFrame(closed_rows)
    history["r_multiple"] = pd.to_numeric(
        history["r_multiple"],
        errors="coerce"
    )
    history["estimated_pnl_dollars"] = pd.to_numeric(
        history["estimated_pnl_dollars"],
        errors="coerce"
    )
    history = history[history["r_multiple"].notna()].copy()

    if "trade_key" in history.columns:

        history = history.drop_duplicates(
            subset=["trade_key"],
            keep="last"
        )

    return history


def _format_rate(value):

    if value is None:

        return "N/A"

    return f"{value:.0f}%"


def _format_r(value):

    if value is None:

        return "N/A"

    sign = "+" if value > 0 else ""

    return f"{sign}{value:.2f}R"


def _format_dollars(value):

    if value is None or pd.isna(value):

        return "N/A"

    sign = "+" if value > 0 else ""

    return f"{sign}${value:,.2f}"


def _paper_performance_summary(closed_trades):

    if closed_trades is None or closed_trades.empty:

        return {
            "closed_trades": 0,
            "wins": 0,
            "losses": 0,
            "flats": 0,
            "win_rate": None,
            "loss_rate": None,
            "total_r": None,
            "avg_r": None,
            "estimated_pnl_dollars": None
        }

    r_values = pd.to_numeric(
        closed_trades["r_multiple"],
        errors="coerce"
    ).dropna()
    total = int(len(r_values))
    wins = int((r_values > 0).sum())
    losses = int((r_values < 0).sum())
    flats = int((r_values == 0).sum())

    pnl_series = pd.to_numeric(
        closed_trades.get("estimated_pnl_dollars", pd.Series(dtype=float)),
        errors="coerce"
    )

    estimated_pnl = None

    if pnl_series.notna().any():

        estimated_pnl = float(pnl_series.sum())

    return {
        "closed_trades": total,
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "win_rate": (wins / total * 100) if total else None,
        "loss_rate": (losses / total * 100) if total else None,
        "total_r": float(r_values.sum()) if total else None,
        "avg_r": float(r_values.mean()) if total else None,
        "estimated_pnl_dollars": estimated_pnl
    }


def _render_performance_metric_row(label, summary):

    st.markdown(f"**{label}**")
    cols = st.columns(6)
    cols[0].metric("Closed", summary["closed_trades"])
    cols[1].metric("Win %", _format_rate(summary["win_rate"]))
    cols[2].metric("Loss %", _format_rate(summary["loss_rate"]))
    cols[3].metric("Total R", _format_r(summary["total_r"]))
    cols[4].metric("Est. $ P/L", _format_dollars(summary["estimated_pnl_dollars"]))
    cols[5].metric("Avg R", _format_r(summary["avg_r"]))


def _render_paper_validation_performance():

    st.subheader("Paper Validation Performance")
    closed_trades = _closed_paper_trade_history()

    if closed_trades.empty:

        st.info("No closed paper trades found yet. Performance metrics will appear after the first paper trade closes.")
        return

    today = _current_trading_day()
    today_trades = closed_trades[
        closed_trades["trading_day"].astype(str).eq(today)
    ].copy()

    _render_performance_metric_row(
        "Today",
        _paper_performance_summary(today_trades)
    )
    _render_performance_metric_row(
        "Overall",
        _paper_performance_summary(closed_trades)
    )

    override_count = 0

    if "paper_affordability_override" in closed_trades.columns:

        override_count = int(
            closed_trades["paper_affordability_override"]
            .astype(str)
            .str.lower()
            .isin(["true", "1", "yes"])
            .sum()
        )

    st.caption(
        "Paper P/L is validation-only. Estimated $ P/L uses actual trade P/L when available, "
        "otherwise Option Risk At Stop × R multiple × contracts, with configured risk as fallback. "
        f"Affordability-override closed trades included: {override_count}."
    )

    with st.expander("Closed paper trades used for performance", expanded=False):

        columns = [
            column for column in [
                "trading_day",
                "closed_at",
                "symbol",
                "direction",
                "r_multiple",
                "estimated_pnl_dollars",
                "paper_affordability_override",
                "exit_reason",
                "source"
            ]
            if column in closed_trades.columns
        ]
        display = closed_trades[columns].sort_values(
            by=["trading_day", "closed_at"],
            ascending=[False, False],
            na_position="last"
        )
        st.dataframe(
            _display_safe_dataframe(display),
            width="stretch",
            hide_index=True
        )


def _render_suggestion_lifecycle(df):

    st.markdown("**New Suggested Calls / Puts - Review Only**")
    new_calls_puts = _new_calls_puts(df)

    if not new_calls_puts.empty:

        st.dataframe(
            _display_safe_dataframe(new_calls_puts),
            width="stretch",
            hide_index=True
        )

    st.markdown("**Still Valid Suggested Trades**")
    still_valid = _still_valid_suggestions()

    if not still_valid.empty:

        st.dataframe(
            _display_safe_dataframe(still_valid),
            width="stretch",
            hide_index=True
        )

    st.markdown("**Expired / Not Entered Suggestions**")
    expired_not_entered = _expired_not_entered_suggestions()

    if not expired_not_entered.empty:

        st.dataframe(
            _display_safe_dataframe(expired_not_entered),
            width="stretch",
            hide_index=True
        )

    if new_calls_puts.empty and still_valid.empty and expired_not_entered.empty:

        st.info("No suggestion lifecycle rows right now.")


def _infer_trade_direction(entry_type):

    entry_type = str(entry_type or "").upper()

    if (
        "PUT" in entry_type
        or "SHORT" in entry_type
        or "REJECTION" in entry_type
        or "BREAKDOWN" in entry_type
    ):

        return "SHORT"

    return "LONG"


def _calculate_trade_pl_pct(trade, current_price):

    entry_price = _safe_float(
        trade.get("entry_price"),
        None
    )

    current_price = _safe_float(
        current_price,
        None
    )

    if not entry_price or not current_price:

        return None

    direction = _infer_trade_direction(
        trade.get("direction")
        or trade.get("entry_type")
    )

    if direction == "SHORT":

        pl_pct = (
            (entry_price - current_price)
            / entry_price
        ) * 100

    else:

        pl_pct = (
            (current_price - entry_price)
            / entry_price
        ) * 100

    return round(pl_pct, 2)


def _calculate_trade_r_progress(trade, current_price):

    entry_price = _safe_float(
        trade.get("entry_price"),
        None
    )
    stop_loss = _safe_float(
        trade.get("stop_loss"),
        None
    )
    current_price = _safe_float(
        current_price,
        None
    )

    if (
        entry_price is None
        or stop_loss is None
        or current_price is None
    ):

        return trade.get(
            "rr_progress",
            0
        )

    direction = _infer_trade_direction(
        trade.get("direction")
        or trade.get("entry_type")
    )

    if direction == "SHORT":

        risk = stop_loss - entry_price
        progress = entry_price - current_price

    else:

        risk = entry_price - stop_loss
        progress = current_price - entry_price

    if risk <= 0:

        return trade.get(
            "rr_progress",
            0
        )

    return round(
        progress / risk,
        2
    )


def _calculate_option_pl(trade):

    from app.options.option_metrics import calculate_option_pl

    return calculate_option_pl(
        trade.get("option_entry_mid") or trade.get("option_mid"),
        trade.get("option_current_mid") or trade.get("option_mid"),
        trade.get("option_contracts") or 1
    )


def _active_trades(df):

    try:

        from app.state.paper_trade_manager import load_paper_trades

        paper_trade_state = load_paper_trades()

    except Exception:

        paper_trade_state = {}

    if not paper_trade_state:

        return pd.DataFrame(columns=ACTIVE_TRADE_COLUMNS)

    current_prices = {}

    if not df.empty and "Symbol" in df.columns:

        current_prices = df.set_index("Symbol")["Price"].to_dict()

    rows = []

    for _, trade in paper_trade_state.items():

        symbol = trade.get("symbol")

        if trade.get("status") != "OPEN":

            continue

        current_price = current_prices.get(
            symbol,
            trade.get("entry_price")
        )

        rows.append({
            "Symbol": f"{symbol} PAPER",
            "Entry Price": trade.get("entry_price"),
            "Current Price": current_price,
            "P/L %": _calculate_trade_pl_pct(
                trade,
                current_price
            ),
            "Option Entry Mid": trade.get("option_entry_mid") or trade.get(
                "option_mid"
            ),
            "Option Current Mid": trade.get("option_current_mid") or trade.get(
                "option_mid"
            ),
            "Option P/L %": _calculate_option_pl(trade).get(
                "option_pl_pct"
            ),
            "Option P/L $": _calculate_option_pl(trade).get(
                "option_pl_dollars"
            ),
            "Option Quality": trade.get("option_liquidity_grade"),
            "Quote Freshness": trade.get("option_quote_freshness"),
            "Stop": trade.get("stop_loss"),
            "Target": trade.get("take_profit"),
            "Exit Signal": "PAPER HOLD",
            "RR Progress": _calculate_trade_r_progress(
                trade,
                current_price
            ),
            "Bars In Trade": trade.get("bars_in_trade", 0)
        })

    if not rows:

        return pd.DataFrame(columns=ACTIVE_TRADE_COLUMNS)

    output = pd.DataFrame(rows)

    return output[ACTIVE_TRADE_COLUMNS]


def _paper_trade_candidates(df):

    if df.empty:

        return pd.DataFrame()

    required_columns = [
        "Symbol",
        "Setup Valid",
        "Candidate Direction",
        "Candidate Entry Price",
        "Candidate Stop Price",
        "Candidate Target Price",
        "Candidate RR",
        "Entry",
        "Action Status",
        "Next Condition",
        "Live Chart Checklist"
    ]

    missing_columns = [
        column for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:

        return pd.DataFrame()

    allowed_statuses = ["ENTER", "ENTER_PAPER"]

    if _allow_review_tv_chart_auto_paper():

        allowed_statuses.append("REVIEW_TV_CHART")

    affordability_ok = _affordability_mask(
        df,
        _ignore_affordability_for_paper_validation()
    )

    candidates = df[
        (df["Setup Valid"] == True)
        & (df["Candidate Direction"].isin(["CALL", "PUT"]))
        & (df["Action Status"].isin(allowed_statuses))
        & affordability_ok
    ].copy()

    candidates = candidates[
        candidates["Entry"].map(_is_valid_new_entry_type)
    ].copy()

    if "Realtime Ready" in candidates.columns:

        realtime_ready = (
            candidates["Realtime Ready"].astype(str).str.lower().isin(["true", "1", "yes"])
            | (candidates["Realtime Ready"] == True)
        )
        review_validation = (
            _allow_review_tv_chart_auto_paper()
            & candidates["Action Status"].astype(str).str.upper().eq("REVIEW_TV_CHART")
        )
        candidates = candidates[
            realtime_ready | review_validation
        ].copy()

    if not candidates.empty:

        candidates["Price Geometry Error"] = candidates.apply(
            price_geometry_error,
            axis=1
        )
        candidates = candidates[
            candidates["Price Geometry Error"].isna()
        ].copy()

    age_minutes = _scanner_output_age_minutes()

    if age_minutes is None or age_minutes > 10:

        return pd.DataFrame()

    return candidates


def _last_seen_candidates(df):

    if df.empty:

        return pd.DataFrame()

    required_columns = [
        "Symbol",
        "Top Candidate",
        "Candidate Direction",
        "Setup Grade",
        "Setup %",
        "RR",
        "Setup Valid",
        "Execution Ready",
        "Realtime Ready",
        "Affordable",
        "Action Status",
        "Blocked By",
        "Recommended Option",
        "Option Quality Score",
        "Option Quote Freshness",
        "Expiration Bucket",
        "Early Watch Status",
        "Early Watch Reason",
        "Would Pass Gate If RR 1.7",
        "Would Pass Gate If Setup 65",
        "Would Pass Gate If Review Allowed",
        "Late Entry Risk",
        "Missed Move Type",
        "Real Trade Readiness",
        "Real Review Scan Count",
        "Real Entry Checklist",
        "Next Condition"
    ]

    available_columns = [
        column for column in required_columns
        if column in df.columns
    ]

    if not available_columns:

        return pd.DataFrame()

    watch_rows = df[
        df.get("Candidate Direction", pd.Series(dtype=object)).isin(["CALL", "PUT"])
        | df.get("Top Candidate", pd.Series(dtype=object)).notna()
        | df.get("Setup Valid", pd.Series(dtype=bool)).fillna(False)
    ].copy()

    if watch_rows.empty:

        return pd.DataFrame()

    current_candidates = _paper_trade_candidates(df)

    if not current_candidates.empty and "Symbol" in current_candidates.columns:

        current_symbols = set(current_candidates["Symbol"].dropna())
        watch_rows = watch_rows[
            ~watch_rows["Symbol"].isin(current_symbols)
        ]

    if watch_rows.empty:

        return pd.DataFrame()

    watch_rows["Watch Reason"] = watch_rows.apply(
        lambda row: row.get("Blocked By")
        or row.get("Action Status")
        or "historical/watch-only",
        axis=1
    )

    columns = [
        "Symbol",
        "Top Candidate",
        "Candidate Direction",
        "Setup Grade",
        "Setup %",
        "RR",
        "Setup Valid",
        "Execution Ready",
        "Realtime Ready",
        "Affordable",
        "Action Status",
        "Blocked By",
        "Watch Reason",
        "Recommended Option",
        "Option Quality Score",
        "Option Quote Freshness",
        "Expiration Bucket",
        "Early Watch Status",
        "Early Watch Reason",
        "Would Pass Gate If RR 1.7",
        "Would Pass Gate If Setup 65",
        "Would Pass Gate If Review Allowed",
        "Late Entry Risk",
        "Missed Move Type",
        "Real Trade Readiness",
        "Real Review Scan Count",
        "Real Entry Checklist",
        "Next Condition"
    ]

    return watch_rows[
        [column for column in columns if column in watch_rows.columns]
    ]


def _render_last_seen_candidates(df):

    last_seen = _last_seen_candidates(df)

    if last_seen.empty:

        st.info("No historical/watch-only candidates right now.")
        return

    st.caption(
        "Watch-only context. These rows do not show entry buttons."
    )

    st.dataframe(
        _display_safe_dataframe(last_seen),
        width="stretch",
        hide_index=True
    )


def _render_paper_trade_controls(df):

    manual_entries_enabled = _manual_paper_entries_enabled()
    show_manual_buttons = _show_manual_paper_buttons()

    candidates = _paper_trade_candidates(df)

    if candidates.empty:

        st.info("No auto-paper candidates requiring review right now.")
        return

    st.caption(
        "System validation path is Auto Paper + Telegram alerts. "
        "Manual paper entry is hidden by default to keep telemetry clean."
    )

    age_minutes = _scanner_output_age_minutes()

    if age_minutes is not None:

        st.caption(
            f"Current scanner output age: {age_minutes} minutes"
        )

    for _, row in candidates.iterrows():

        symbol = row.get("Symbol")
        confirm_key = f"confirm_{symbol}"
        button_key = f"paper_enter_{symbol}"

        with st.expander(
            f"{symbol} {row.get('Candidate Direction')} candidate"
        ):

            ai_eligible, ai_reason = _ai_candidate_eligibility(row)

            st.write(
                {
                    "Entry": row.get("Candidate Entry Price"),
                    "Stop": row.get("Candidate Stop Price"),
                    "Target": row.get("Candidate Target Price"),
                    "RR": row.get("Candidate RR"),
                    "Setup": row.get("Entry"),
                    "Next": row.get("Next Condition"),
                    "Recommended Option": row.get("Recommended Option"),
                    "Alternate Short DTE": row.get("Short DTE Option"),
                    "Alternate Longer DTE": row.get("Longer DTE Option"),
                    "Option Ticker": row.get("Option Ticker"),
                    "Option Expiration": row.get("Option Expiration"),
                    "Option Strike": row.get("Option Strike"),
                    "Option Moneyness": row.get("Option Moneyness"),
                    "Expiration Bucket": row.get("Expiration Bucket"),
                    "Expiration Risk": row.get("Expiration Risk"),
                    "Option Mid": row.get("Option Mid Price"),
                    "Option Spread %": row.get("Option Spread %"),
                    "Option Quality": row.get("Option Liquidity Grade"),
                    "Quote Freshness": row.get("Option Quote Freshness")
                }
            )
            st.caption(row.get("Live Chart Checklist"))

            ai_button_key = f"ai_explain_{symbol}"

            if st.button(
                "Explain this candidate with AI",
                key=ai_button_key,
                disabled=not ai_eligible
            ):

                summary, from_cache = _generate_candidate_ai_summary(row)
                cache_note = (
                    "cached"
                    if from_cache
                    else "new"
                )
                st.info(
                    f"AI summary ({cache_note}):\n\n{summary}"
                )

            if not ai_eligible:

                st.caption(
                    f"AI explanation disabled: {ai_reason}"
                )

            if not show_manual_buttons:

                continue

            if not manual_entries_enabled:

                st.caption(
                    "Manual paper entry is disabled. Set "
                    "ENABLE_MANUAL_PAPER_ENTRIES=true and "
                    "SHOW_MANUAL_PAPER_BUTTONS=true for debug-only use."
                )
                continue

            live_confirmed = st.checkbox(
                "I manually confirmed this setup on the live chart",
                key=confirm_key
            )

            if st.button(
                "Paper enter",
                key=button_key,
                disabled=not live_confirmed
            ):

                _open_paper_trade_from_row(row)
                st.success(f"Opened paper trade for {symbol}")
                st.rerun()


def _render_paper_exit_controls(df):

    if not _allow_manual_paper_close():

        return

    try:

        from app.state.paper_trade_manager import load_paper_trades

        paper_trades = load_paper_trades()

    except Exception:

        paper_trades = {}

    open_paper_trades = [
        trade for trade in paper_trades.values()
        if trade.get("status") == "OPEN"
    ]

    if not open_paper_trades:

        return

    current_prices = {}

    if not df.empty and "Symbol" in df.columns:

        current_prices = df.set_index("Symbol")["Price"].to_dict()

    st.caption("Manual close/correction for tracked paper trades.")

    for trade in open_paper_trades:

        symbol = trade.get("symbol")

        close_price = current_prices.get(
            symbol,
            trade.get("entry_price")
        )

        scanner_context = None

        if not df.empty and "Symbol" in df.columns:

            matching_rows = df[
                df["Symbol"] == symbol
            ]

            if not matching_rows.empty:

                scanner_context = _scanner_context_from_row(
                    matching_rows.iloc[0]
                )

        if st.button(
            f"Close {symbol} paper trade",
            key=f"paper_close_{symbol}"
        ):

            _close_paper_trade(
                symbol,
                close_price,
                scanner_context=scanner_context
            )
            st.success(f"Closed paper trade for {symbol}")
            st.rerun()


def _telemetry_summary():

    telemetry = _load_telemetry()

    if telemetry.empty:

        return {
            "Telemetry Trades": 0,
            "Win Rate": "N/A",
            "Avg R": "N/A"
        }

    trade_count = len(telemetry)

    avg_r = "N/A"
    win_rate = "N/A"

    if "r_multiple" in telemetry.columns:

        r_values = pd.to_numeric(
            telemetry["r_multiple"],
            errors="coerce"
        ).dropna()

        if not r_values.empty:

            avg_r = round(r_values.mean(), 2)
            win_rate = f"{round((r_values > 0).mean() * 100, 1)}%"

    return {
        "Telemetry Trades": trade_count,
        "Win Rate": win_rate,
        "Avg R": avg_r
    }


def _file_row_count(path, reader):

    try:

        if not path.exists() or path.stat().st_size == 0:

            return 0

        return len(reader(path))

    except Exception:

        return 0


def _daily_candidate_snapshot_count(trading_day):

    parquet_path = daily_path(trading_day, "candidate_snapshots.parquet")
    csv_path = daily_path(trading_day, "candidate_snapshots.csv")

    if parquet_path.exists():

        return _file_row_count(parquet_path, pd.read_parquet)

    return _file_row_count(csv_path, pd.read_csv)


def _render_validation_data_health(df):

    st.subheader("Validation Data Health")
    trading_day = _current_trading_day()
    decisions = _load_auto_paper_decision_log()
    decision_df = pd.DataFrame(decisions) if decisions else pd.DataFrame()

    try:

        from app.state.paper_trade_manager import load_paper_trades
        paper_trades = load_paper_trades()

    except Exception:

        paper_trades = {}

    suggested = _load_suggested_trades_df()
    opened_count = 0

    if not decision_df.empty and "decision" in decision_df.columns:

        opened_count = int(
            decision_df["decision"]
            .astype(str)
            .str.upper()
            .eq("OPENED")
            .sum()
        )

    paper_events_path = daily_path(trading_day, "paper_trade_events.csv")
    health = {
        "Scanner rows": len(df),
        "Candidate snapshots": _daily_candidate_snapshot_count(trading_day),
        "Telemetry rows": len(_load_telemetry()),
        "Paper trade events": _file_row_count(paper_events_path, pd.read_csv),
        "Paper trade state records": len(paper_trades),
        "Auto-paper decisions": len(decisions),
        "OPENED decisions": opened_count,
        "Suggested trades": len(suggested),
        "Invalid geometry count": int(
            df.apply(price_geometry_error, axis=1).notna().sum()
        ) if not df.empty else 0,
    }
    health_df = pd.DataFrame(
        list(health.items()),
        columns=["Metric", "Value"]
    )
    st.dataframe(
        _display_safe_dataframe(health_df),
        width="stretch",
        hide_index=True
    )


def _render_daily_validation_report_panel():

    st.subheader("Daily Validation Report")
    trading_day = _current_trading_day()
    report_path = daily_path(
        trading_day,
        "daily_validation_report.html"
    )
    report_data = _read_download_file(report_path)

    if report_data is None:

        st.info(
            "No daily validation report generated yet. Use the sidebar Daily Validation controls."
        )
        return

    st.download_button(
        label="Download daily_validation_report.html",
        data=report_data,
        file_name=f"daily_validation_{trading_day}.html",
        mime="text/html",
        key="download_daily_validation_report_main"
    )


def main():

    st.set_page_config(
        page_title="Dravya Wallstreet Edge",
        layout="wide"
    )

    _inject_compact_dashboard_css()

    st.title("Dravya Wallstreet Edge")
    st.caption("Decision dashboard only. Full engine diagnostics stay in Excel/backend.")

    refresh_state = _render_auto_refresh_controls()
    auto_paper_controls = _render_auto_paper_controls()
    _render_runtime_key_status()
    _render_download_exports()
    _maybe_auto_run_scanner(refresh_state)

    if st.button("Run scanner now"):

        with st.spinner("Running scanner..."):

            try:

                result = _run_scanner_once()

                if result.get("ran"):

                    st.success("Scanner completed. Dashboard refreshed.")

                else:

                    st.info("Scanner already running; showing latest available results.")

            except Exception as exc:

                st.error(f"Scanner failed: {exc}")

    df = _load_scanner_output()

    if df.empty:

        st.warning("scanner_output.xlsx not found or empty. Run python -m app.main first.")
        return

    _sync_suggested_trades(df)
    df = _add_paper_trade_opened(df)
    df = _add_real_trade_readiness(df)
    df = _enrich_with_suggestion_lifecycle(df)

    latest_time = df.get("Current ET")
    latest_scanner_run = "N/A"

    if latest_time is not None and len(latest_time) > 0:

        latest_scanner_run = latest_time.iloc[0]
        st.caption(f"Last scanner run: {latest_scanner_run}")

    auto_closed = _run_auto_paper_exits(
        df,
        auto_paper_controls
    )

    if auto_closed:

        st.success(
            "Auto-closed paper trades: "
            + ", ".join(auto_closed)
        )
        st.rerun()

    auto_opened = _run_auto_paper_entries(
        df,
        auto_paper_controls
    )

    if auto_opened:

        st.success(
            "Auto-opened paper trades: "
            + ", ".join(auto_opened)
        )
        st.rerun()

    _render_compact_status_cards(
        df,
        auto_paper_controls
    )

    _render_compact_market_health(df)

    _render_action_center(
        df,
        auto_paper_controls
    )

    _render_scanner_watchlist(df)

    _render_paper_exit_controls(df)

    _render_compact_auto_paper_summary()

    _render_paper_validation_performance()

    with st.expander("Suggestion Lifecycle", expanded=False):

        _render_suggestion_lifecycle(df)

    with st.expander("Full Auto-Paper Decision Log", expanded=False):

        _render_auto_paper_decision_log(show_full_expander=False)

    with st.expander("Validation Data Health", expanded=False):

        _render_validation_data_health(df)

    with st.expander("Daily Report", expanded=False):

        _render_daily_validation_report_panel()

    with st.expander("Telemetry & Debug", expanded=False):

        st.subheader("Last Seen Candidates")
        _render_last_seen_candidates(df)

        st.subheader("Alert + Paper Performance Review")
        telemetry_metrics = _telemetry_summary()
        telemetry_cols = st.columns(3)

        for col, (label, value) in zip(telemetry_cols, telemetry_metrics.items()):

            col.metric(label, value)

    st.caption("Auto-refresh controls are in the sidebar. Market-hours default is ON at 5 minutes; after-hours default is OFF.")


if __name__ == "__main__":

    main()
