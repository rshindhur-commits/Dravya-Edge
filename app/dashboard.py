from pathlib import Path
import os
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo
import json
from io import BytesIO

import pandas as pd
import streamlit as st


def _bootstrap_streamlit_secrets_to_env():

    try:

        for key, value in st.secrets.items():

            if isinstance(value, (str, int, float, bool)):

                os.environ[str(key)] = str(value)

    except Exception:

        return


_bootstrap_streamlit_secrets_to_env()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
import streamlit as st

from app.config.settings import settings
from app.gates.entry_gate import (
    EntryGateConfig,
    env_int,
    has_active_symbol_trade,
    is_symbol_in_cooldown,
    symbol_trade_count_today,
    evaluate_entry_gate
)
from app.options.option_affordability import add_affordability_metrics
from app.utils.json_store import (
    load_json_file,
    save_json_file
)

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
AUTO_PAPER_ENTRY_START = time(9, 45)
AUTO_PAPER_ENTRY_END = time(15, 30)
AUTO_PAPER_EOD_CLOSE = time(15, 55)
DEFAULT_AUTO_PAPER_MIN_RR = 1.8
DEFAULT_AUTO_PAPER_MIN_OPTION_QUALITY = 65.0
DEFAULT_AUTO_PAPER_MAX_SPREAD_PCT = 10.0


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
    "Setup Grade",
    "Setup %",
    "Action Status",
    "Blocked By",
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

    if not SCANNER_FILE.exists():

        return pd.DataFrame()

    try:

        df = pd.read_excel(SCANNER_FILE)

    except Exception as exc:

        bad_file = SCANNER_FILE.with_suffix(".bad.xlsx")

        try:

            SCANNER_FILE.replace(bad_file)

        except Exception:

            pass

        st.error(
            f"scanner_output.xlsx is corrupted or was partially written. "
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

    rows = df[
        (df["Setup Valid"] == True)
        & (df["Candidate Direction"].isin(["CALL", "PUT"]))
        & (df["Action Status"].isin(["REVIEW_TV_CHART", "ENTER", "ENTER_PAPER"]))
        & (df.get("Affordable", True) == True)
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

            return round(
                (
                    now.replace(tzinfo=None)
                    - datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
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

    return data or _read_download_file(SCANNER_FILE)


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


def _record_auto_paper_decision(symbol, decision, reason, row=None):

    entries = _load_auto_paper_decision_log()
    entry = {
        "timestamp": _current_et().strftime("%Y-%m-%d %H:%M:%S"),
        "symbol": symbol,
        "decision": decision,
        "reason": reason,
        "top_candidate": row.get("Top Candidate") if row is not None else None,
        "setup_percent": row.get("Setup %") if row is not None else None,
        "rr": row.get("RR") if row is not None else None,
        "action_status": row.get("Action Status") if row is not None else None,
        "blocked_by": row.get("Blocked By") if row is not None else None,
        "action_reason": row.get("Action Reason") if row is not None else None,
        "option_rejection_reason": row.get("Option Rejection Reason") if row is not None else None,
        "realtime_block_reason": row.get("Realtime Block Reason") if row is not None else None,
        "option_quality_score": row.get("Option Quality Score") if row is not None else None,
        "option_spread_pct": row.get("Option Spread %") if row is not None else None,
        "option_quote_freshness": row.get("Option Quote Freshness") if row is not None else None,
        "expiration_bucket": row.get("Expiration Bucket") if row is not None else None
    }
    entries.append(entry)
    _save_auto_paper_decision_log(entries)


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

            st.sidebar.caption(
                f"{export['label']}: not found"
            )
            continue

        st.sidebar.download_button(
            label=f"Download {export['label']}",
            data=data,
            file_name=export["label"],
            mime=export["mime"],
            key=f"download_{export['label']}"
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

    if not SCANNER_FILE.exists():

        return None

    modified_at = datetime.fromtimestamp(
        SCANNER_FILE.stat().st_mtime,
        tz=ZoneInfo("America/New_York")
    )

    current_et = datetime.now(
        ZoneInfo("America/New_York")
    )

    return round(
        (current_et - modified_at).total_seconds() / 60,
        2
    )


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

        st.session_state["auto_paper_eod_close_enabled"] = bool(
            saved_auto_settings.get(
                "auto_paper_eod_close_enabled",
                True
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

    marker = (
        SCANNER_FILE.stat().st_mtime
        if SCANNER_FILE.exists()
        else "missing"
    )

    if st.session_state.get("last_auto_run_marker") == marker:

        return

    st.session_state["last_auto_run_marker"] = marker

    with st.spinner("Auto-running scanner..."):

        try:

            _run_scanner_once()
            st.sidebar.success(
                "Scanner auto-run completed."
            )

        except Exception as exc:

            st.sidebar.error(
                f"Scanner auto-run failed: {exc}"
            )


def _sync_streamlit_secrets_to_env():

    try:

        for key, value in st.secrets.items():

            if isinstance(value, (str, int, float, bool)):

                os.environ[str(key)] = str(value)

    except Exception:

        return


def _run_scanner_once():

    _sync_streamlit_secrets_to_env()

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

    scanner_context = _scanner_context_from_row(row)

    opened_trade = open_paper_trade(
        symbol=row.get("Symbol"),
        direction=row.get("Candidate Direction"),
        entry_price=row.get("Candidate Entry Price"),
        stop_loss=row.get("Candidate Stop Price"),
        take_profit=row.get("Candidate Target Price"),
        entry_type=row.get("Entry"),
        option_ticker=row.get("Option Ticker"),
        option_bid=row.get("Option Bid"),
        option_ask=row.get("Option Ask"),
        scanner_context=scanner_context,
        entry_source="MANUAL_PAPER",
        trade_mode="PAPER",
        include_in_strategy_stats=False
    )

    if promote_suggestion_to_paper_trade:

        promote_suggestion_to_paper_trade(row.get("Symbol"))

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

    if (
        row.get("Top Candidate") not in AUTO_PAPER_TOP_CANDIDATES
        and not execution_ready
    ):

        return False, "not top candidate"

    if _safe_float(row.get("Setup %"), None) is None:

        row = row.copy()
        row["Setup %"] = _compute_setup_percent(row)

    gate_allowed, gate_reason = evaluate_entry_gate(
        row,
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

    if not realtime_ready:

        return False, row.get("Realtime Block Reason") or "realtime not ready"

    if _safe_float(row.get("Option Bid"), 0) <= 0 or _safe_float(row.get("Option Ask"), 0) <= 0:

        return False, "missing option bid/ask"

    if bool(row.get("Event Blocked")):

        return False, "event blocked"

    if bool(row.get("Regime Blocked")):

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
                    "auto paper enabled; scanner output empty"
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
                            row
                        )

                else:

                    _record_auto_paper_decision(
                        "SYSTEM",
                        "SKIPPED",
                        "auto paper enabled; market closed but no symbol rows found"
                    )

                return []

            if not log_rows.empty:

                for _, row in log_rows.iterrows():

                    _record_auto_paper_decision(
                        row.get("Symbol"),
                        "SKIPPED",
                        _scanner_block_reason(row),
                        row
                    )

                return []

            _record_auto_paper_decision(
                "SYSTEM",
                "SKIPPED",
                "auto paper enabled; no eligible entry candidates and no symbol rows found"
            )

            return []

        if not log_rows.empty:

            for _, row in log_rows.iterrows():

                _record_auto_paper_decision(
                    row.get("Symbol"),
                    "SKIPPED",
                    "auto paper disabled",
                    row
                )

            return []

        _record_auto_paper_decision(
            "SYSTEM",
            "SKIPPED",
            "auto paper disabled; no current candidates and no symbol rows found"
        )

        return []

    if not controls["auto_paper_enabled"]:

        for _, row in candidates.iterrows():

            _record_auto_paper_decision(
                row.get("Symbol"),
                "SKIPPED",
                "auto paper disabled",
                row
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
                row
            )

            continue

        from app.state.paper_trade_manager import open_paper_trade
        from app.alerts.telegram_alerts import maybe_send_paper_entry_alert

        try:

            from app.state.suggested_trade_manager import promote_suggestion_to_paper_trade

        except Exception:

            promote_suggestion_to_paper_trade = None

        scanner_context = _scanner_context_from_row(row)
        spread_note = (
            "; missing spread allowed for paper"
            if _safe_float(row.get("Option Spread %"), None) is None
            else ""
        )
        opened_trade = open_paper_trade(
            symbol=row.get("Symbol"),
            direction=row.get("Candidate Direction"),
            entry_price=row.get("Candidate Entry Price"),
            stop_loss=row.get("Candidate Stop Price"),
            take_profit=row.get("Candidate Target Price"),
            entry_type=row.get("Entry"),
            option_ticker=row.get("Option Ticker"),
            option_bid=row.get("Option Bid"),
            option_ask=row.get("Option Ask"),
            notes=f"Auto paper entry: {reason}{spread_note}",
            scanner_context=scanner_context,
            entry_source="AUTO_PAPER",
            trade_mode="PAPER",
            include_in_strategy_stats=True
        )
        paper_trades = load_paper_trades()
        opened.append(row.get("Symbol"))

        if promote_suggestion_to_paper_trade:

            promote_suggestion_to_paper_trade(row.get("Symbol"))

        telegram_entry_result = maybe_send_paper_entry_alert(
            opened_trade,
            scanner_context,
            reason=f"Auto paper entry: {reason}"
        )

        _record_auto_paper_decision(
            row.get("Symbol"),
            "TELEGRAM_ENTRY_ALERT",
            telegram_entry_result.get("reason"),
            row
        )

        _record_auto_paper_decision(
            row.get("Symbol"),
            "OPENED",
            reason,
            row
        )

        if _auto_paper_trade_count_today(paper_trades) >= controls["max_daily"]:

            break

    return opened


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

        if bool(scanner_row.get("Live Exit Signal")):

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


def _render_auto_paper_decision_log():

    entries = _load_auto_paper_decision_log()

    if not entries:

        st.info("No auto-paper decisions logged yet.")
        return

    recent = pd.DataFrame(entries[-50:])

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


def _new_calls_puts(df):

    candidates = _paper_trade_candidates(df)

    if candidates.empty:

        return pd.DataFrame()

    output = candidates.copy()
    output["Status"] = output["Candidate Direction"].map(
        lambda direction: "NEW_CALL" if direction == "CALL" else "NEW_PUT"
    )
    columns = [
        "Symbol",
        "Candidate Direction",
        "Status",
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
        "Realtime Ready",
        "Action Status"
    ]
    return output[[column for column in columns if column in output.columns]]


def _still_valid_suggestions():

    suggestions = _load_suggested_trades_df()

    if suggestions.empty:

        return pd.DataFrame()

    active_statuses = [
        "NEW_CALL",
        "NEW_PUT",
        "STILL_VALID_CALL",
        "STILL_VALID_PUT",
        "WATCH_WEAKENING",
        "EXPIRED_NOT_ENTERED",
        "DO_NOT_CHASE",
        "CONTRACT_CHANGED"
    ]
    rows = suggestions[
        suggestions["status"].isin(active_statuses)
    ].copy()

    if rows.empty:

        return pd.DataFrame()

    columns = [
        "symbol",
        "direction",
        "status",
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

    trade_state = _load_trade_state()

    try:

        from app.state.paper_trade_manager import load_paper_trades

        paper_trade_state = load_paper_trades()

    except Exception:

        paper_trade_state = {}

    if not trade_state and not paper_trade_state:

        return pd.DataFrame(columns=ACTIVE_TRADE_COLUMNS)

    current_prices = {}

    if not df.empty and "Symbol" in df.columns:

        current_prices = df.set_index("Symbol")["Price"].to_dict()

    rows = []

    for symbol, trade in trade_state.items():

        if trade.get("status") != "OPEN":

            continue

        current_price = current_prices.get(
            symbol,
            trade.get("entry_price")
        )

        rows.append({
            "Symbol": symbol,
            "Entry Price": trade.get("entry_price"),
            "Current Price": current_price,
            "P/L %": _calculate_trade_pl_pct(
                trade,
                current_price
            ),
            "Option Entry Mid": trade.get("option_entry_mid"),
            "Option Current Mid": trade.get("option_current_mid"),
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
            "Exit Signal": "HOLD",
            "RR Progress": _calculate_trade_r_progress(
                trade,
                current_price
            ),
            "Bars In Trade": trade.get("bars_in_trade", 0)
        })

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

    candidates = df[
        (df["Setup Valid"] == True)
        & (df["Candidate Direction"].isin(["CALL", "PUT"]))
        & (df["Action Status"].isin(["REVIEW_TV_CHART", "ENTER", "ENTER_PAPER"]))
        & (df.get("Affordable", True) == True)
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
        "Action Status",
        "Blocked By",
        "Recommended Option",
        "Option Quality Score",
        "Option Quote Freshness",
        "Expiration Bucket",
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
        "Action Status",
        "Blocked By",
        "Watch Reason",
        "Recommended Option",
        "Option Quality Score",
        "Option Quote Freshness",
        "Expiration Bucket",
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


def main():

    st.set_page_config(
        page_title="AI Trading Scanner",
        layout="wide"
    )

    st.title("AI Trading Scanner")
    st.caption("Decision dashboard only. Full engine diagnostics stay in Excel/backend.")

    refresh_state = _render_auto_refresh_controls()
    auto_paper_controls = _render_auto_paper_controls()
    _render_runtime_key_status()
    _render_download_exports()
    _maybe_auto_run_scanner(refresh_state)

    if st.button("Run scanner now"):

        with st.spinner("Running scanner..."):

            try:

                _run_scanner_once()
                st.success("Scanner completed. Dashboard refreshed.")

            except Exception as exc:

                st.error(f"Scanner failed: {exc}")

    df = _load_scanner_output()

    if df.empty:

        st.warning("scanner_output.xlsx not found or empty. Run python -m app.main first.")
        return

    _sync_suggested_trades(df)
    df = _enrich_with_suggestion_lifecycle(df)

    latest_time = df.get("Current ET")

    if latest_time is not None and len(latest_time) > 0:

        st.caption(f"Last scanner run: {latest_time.iloc[0]}")

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

    health = _market_health(df)

    st.subheader("Trade Opportunities")
    opportunities = _build_trade_opportunities(df)

    st.dataframe(
        _style_opportunities(opportunities),
        width="stretch",
        hide_index=True
    )

    st.subheader("New Calls / Puts")
    new_calls_puts = _new_calls_puts(df)
    if new_calls_puts.empty:

        st.info("No fresh current calls/puts right now.")

    else:

        st.dataframe(
            _display_safe_dataframe(new_calls_puts),
            width="stretch",
            hide_index=True
        )

    st.subheader("Still Valid Suggested Trades")
    still_valid = _still_valid_suggestions()
    if still_valid.empty:

        st.info("No persisted suggested trades yet.")

    else:

        st.dataframe(
            _display_safe_dataframe(still_valid),
            width="stretch",
            hide_index=True
        )

    st.subheader("Auto Paper Candidates")
    _render_paper_trade_controls(df)

    st.subheader("Last Seen Candidates")
    _render_last_seen_candidates(df)

    st.subheader("Market Health Panel")

    market_df = pd.DataFrame(
        list(health.items()),
        columns=["Metric", "Value"]
    )

    market_df = _display_safe_dataframe(
        market_df
    )

    st.dataframe(
        market_df,
        width="stretch",
        hide_index=True
    )

    st.subheader("Active Paper Trades")
    active_trades = _active_trades(df)

    st.dataframe(
        _display_safe_dataframe(active_trades),
        width="stretch",
        hide_index=True
    )

    st.subheader("Exit Now Alerts")
    exit_alerts = _exit_now_alerts(
        df,
        auto_paper_controls
    )
    if exit_alerts.empty:

        st.info("No exit-now alerts.")

    else:

        st.dataframe(
            _display_safe_dataframe(exit_alerts),
            width="stretch",
            hide_index=True
        )

    _render_paper_exit_controls(df)

    st.subheader("Auto Paper Decision Log")
    _render_auto_paper_decision_log()

    st.subheader("Alert + Paper Performance Review")
    telemetry_metrics = _telemetry_summary()
    telemetry_cols = st.columns(3)

    for col, (label, value) in zip(telemetry_cols, telemetry_metrics.items()):

        col.metric(label, value)

    st.caption("Auto-refresh controls are in the sidebar. Market-hours default is ON at 5 minutes; after-hours default is OFF.")


if __name__ == "__main__":

    main()
