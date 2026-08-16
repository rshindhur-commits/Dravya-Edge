from pathlib import Path
import base64
import os
import re
import sys
from datetime import datetime, time, timezone
from html import escape
from zoneinfo import ZoneInfo
import json
from io import BytesIO

import pandas as pd
import streamlit as st

try:

    import plotly.express as px

except Exception:

    px = None


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
from app.config.performance import (
    DEVELOPER_CACHE_TTL,
    TRADING_CACHE_TTL,
    TRADING_DASHBOARD_STATE_ONLY,
    VALIDATION_CACHE_TTL,
)
from app.gates import (
    EntryGateConfig,
    env_int,
    has_active_symbol_trade,
    is_symbol_in_cooldown,
    symbol_trade_count_today,
    evaluate_entry_gate,
    price_geometry_error
)
from app.strategies.setup_registry import KNOWN_SETUPS
from app.gates.setup_quality import (
    MIN_SETUP_BASE,
    setup_grade as _setup_quality_grade,
    setup_percent_from_row,
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
from app.runtime import get_runtime_scheduler, measure_runtime
from app.storage.daily_paths import (
    daily_path,
    get_daily_dir,
    live_path,
    state_path,
    telemetry_path,
)
from app.storage.session_manager import get_scan_id, get_session_id, get_trading_day
from app.ui.components import kpi_card

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


# Deliberately not an f-string: the CSS is mostly braces, and doubling every one
# of them to survive .format() is how a stylesheet acquires silent typos.
_HEADER_CSS = """<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');

  .de-header { display:flex; align-items:center; gap:22px; width:100%;
    padding:14px 2px 18px;
    /* .block-container's top padding is trimmed to 1.3rem, under the ~3.75rem
       Streamlit reserves for its fixed toolbar, and this is the first element
       on the page. The 2rem buys that clearance back on the header alone. */
    margin:2rem 0 6px;
    border-bottom:1px solid rgba(22,164,112,.32);
    font-family:'Inter',-apple-system,'Helvetica Neue',Arial,sans-serif; }

  .de-header > img { height:78px; width:78px; flex:0 0 auto; }

  .de-title { display:flex; flex-direction:column; gap:7px; min-width:0; }
  .de-name { font-size:clamp(23px,3.1vw,44px); font-weight:300; letter-spacing:.11em;
    color:#F2EFE6; line-height:1; white-space:nowrap; }
  .de-name b { font-weight:700; color:#D4A93C; }
  .de-sub { font-size:clamp(8.5px,.9vw,12.5px); font-weight:400; letter-spacing:.34em;
    color:#7C9BB2; text-transform:uppercase; line-height:1; white-space:nowrap; }

  /* The rule is the piece that makes this read as a page header rather than a
     card: it absorbs whatever width is left, so the header always spans the
     container instead of ending wherever the artwork happened to end. */
  .de-rule { flex:1 1 auto; min-width:24px; height:1px;
    background:linear-gradient(90deg, rgba(212,169,60,.5), rgba(212,169,60,.06)); }
  .de-dot { flex:0 0 auto; width:9px; height:9px; transform:rotate(45deg);
    background:#D4A93C; opacity:.85; }

  @media (max-width:760px) {
    .de-header { gap:14px; padding:10px 2px 13px; margin-top:1.4rem; }
    .de-header > img { height:50px; width:50px; }
    .de-rule, .de-dot, .de-long { display:none; }
    .de-sub { letter-spacing:.2em; }
  }
</style>"""


def render_app_header():
    """A header that spans the page, and paints no background of its own.

    The wordmark is text, not artwork. That is the whole point: an image has a
    fixed width and a baked-in plate, so it ends where it ends and its navy sits
    visibly on top of the page's own. This header stretches to the container and
    inherits whatever the page background is.

    The mark is the one piece that stays an image -- a transparent SVG, 12KB
    against the 132KB lockup it replaces, and it re-encodes on every rerun.
    """

    mark = ROOT_DIR / "assets" / "dravya-edge-mark.svg"

    if not mark.exists():

        st.title("Dravya Edge")
        st.caption("Directional signals · Nasdaq options")
        return

    encoded_mark = base64.b64encode(mark.read_bytes()).decode("ascii")

    st.markdown(_HEADER_CSS, unsafe_allow_html=True)

    st.markdown(
        f"""
        <div class="de-header">
          <img src="data:image/svg+xml;base64,{encoded_mark}" alt="Dravya Edge">
          <div class="de-title">
            <div class="de-name">DRAVYA <b>EDGE</b></div>
            <div class="de-sub">Directional signals<span class="de-long"> &middot; NASDAQ options</span></div>
          </div>
          <div class="de-rule"></div><div class="de-dot"></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# Resolved through the storage roots, not from ROOT_DIR.
#
# These were eleven independent restatements of paths that daily_paths already
# owns, and being built from ROOT_DIR meant they ignored DRAVYA_DATA_DIR and
# DRAVYA_STATE_DIR entirely -- so the test sandbox did not cover a single one of
# them. That is the same gap that let the suite append real rows to
# telemetry/trade_telemetry.csv, and the same one the database sandbox was added
# for. Today they are only read here, so nothing had leaked through them yet.
SCANNER_FILE = ROOT_DIR / "scanner_output.xlsx"
LIVE_SCANNER_FILE = live_path("scanner_output_latest.xlsx")
LIVE_SCANNER_CSV_FILE = live_path("scanner_output_latest.csv")
LIVE_DASHBOARD_STATE_FILE = live_path("dashboard_state.json")
TRADE_STATE_FILE = state_path("trade_state.json")
TELEMETRY_FILE = telemetry_path("trade_telemetry.csv")
PAPER_TRADE_STATE_FILE = state_path("paper_trade_state.json")
AI_SUMMARY_CACHE_FILE = ROOT_DIR / settings.ai_summary_cache_file
AUTO_PAPER_DECISION_LOG_FILE = state_path("auto_paper_decision_log.json")
SUGGESTED_TRADE_STATE_FILE = state_path("suggested_trade_state.json")
TELEGRAM_DISPATCH_AUDIT_FILE = live_path("telegram_dispatch_audit.jsonl")

REFRESH_INTERVALS = {
    "1 min": 1,
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
# Was a stale long-only copy of the scanner's constant, containing three setups
# that cannot be emitted. Imported now so the two cannot disagree again.
REVIEW_VALIDATION_ENTRY_TYPES = KNOWN_SETUPS
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
    """Thin alias. The metric lives in app/gates/setup_quality.py.

    This was a byte-identical copy of the scanner's version, so the dashboard and
    the gate could silently disagree about what a candidate scored.
    """

    return setup_percent_from_row(row)


def _setup_grade(setup_pct):
    """Grade bands rescaled with the metric, in app/gates/setup_quality.py."""

    return _setup_quality_grade(setup_pct)


def _style_setup_grade(value):

    text = str(value or "")

    if text.startswith("A+"):

        return "background-color: rgba(34, 197, 94, 0.18); color: inherit; font-weight: 700"

    if text.startswith("A"):

        return "background-color: rgba(34, 197, 94, 0.14); color: inherit; font-weight: 700"

    if text.startswith("B"):

        return "background-color: rgba(245, 158, 11, 0.18); color: inherit; font-weight: 700"

    return "background-color: rgba(239, 68, 68, 0.18); color: inherit; font-weight: 700"


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


def _load_dashboard_state(df=None):

    cached = _load_cached_state("dashboard_state.json", profile="trading")

    if cached:

        return cached

    if df is not None and not df.empty:

        from app.ui.dashboard_state import build_dashboard_state

        return build_dashboard_state(df)

    return {}


def _state_file_mtime(filename):

    path = ROOT_DIR / "data" / "live" / filename

    try:

        if path.exists() and path.stat().st_size > 0:

            return path.stat().st_mtime

    except Exception:

        return None

    return None


def _read_cached_state_file(filename):

    path = ROOT_DIR / "data" / "live" / filename

    try:

        if path.exists() and path.stat().st_size > 0:

            return json.loads(path.read_text(encoding="utf-8"))

    except Exception:

        return {}

    return {}


@st.cache_data(ttl=TRADING_CACHE_TTL)
def _load_trading_cached_state(filename, mtime):

    return _read_cached_state_file(filename)


@st.cache_data(ttl=VALIDATION_CACHE_TTL)
def _load_validation_cached_state(filename, mtime):

    return _read_cached_state_file(filename)


@st.cache_data
def _load_static_cached_state(filename, mtime):

    return _read_cached_state_file(filename)


@st.cache_data(ttl=DEVELOPER_CACHE_TTL)
def _load_developer_cached_state(filename, mtime):

    return _read_cached_state_file(filename)


def _load_cached_state(filename, profile="static"):

    mtime = _state_file_mtime(filename)

    if mtime is None:

        return {}

    if profile == "trading":

        return _load_trading_cached_state(filename, mtime)

    if profile == "validation":

        return _load_validation_cached_state(filename, mtime)

    if profile == "developer":

        return _load_developer_cached_state(filename, mtime)

    return _load_static_cached_state(filename, mtime)


def _render_cached_recommendations(recommendations):

    recommendations = recommendations or []

    if not recommendations:

        return

    st.markdown("### Engineering Recommendations")
    st.dataframe(
        _display_safe_dataframe(pd.DataFrame(recommendations)),
        width="stretch",
        hide_index=True
    )


def _render_validation_diagnosis(diagnosis):

    diagnosis = diagnosis or {}

    if not diagnosis:

        return

    st.markdown("## Trade Doctor")
    st.caption("Today's Diagnosis — evidence-backed review only. No rule changes are made from this panel.")

    for area in ["scanner", "entry", "exit", "replay", "missed_winners", "tomorrow"]:

        item = diagnosis.get(area) or {}
        findings = item.get("findings") or []

        if not findings:

            continue

        st.markdown(f"### {area.replace('_', ' ').title()}")
        st.caption(str(item.get("status", "OBSERVE")).replace("_", " "))
        rows = [
            {
                "Status": finding.get("status", "OBSERVE").replace("_", " "),
                "Reason": finding.get("reason", "-"),
                "Evidence": finding.get("evidence", "-"),
                "Action": finding.get("action", "Observe; DO NOT CHANGE RULE."),
            }
            for finding in findings
        ]
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(rows)),
            width="stretch",
            hide_index=True,
        )


def _render_strategy_confidence(confidence):

    confidence = confidence or {}

    if not confidence:

        return

    st.markdown("### Strategy Confidence")
    st.caption("Evidence strength, not a prediction of future returns.")
    _render_compact_card_grid([
        ("Evidence", f"{confidence.get('evidence_days', 0)} Day(s)"),
        ("Completed Trades", confidence.get("completed_trades", 0)),
        ("Confidence", f"{confidence.get('confidence_pct', 0)}%"),
        ("Decision Status", str(confidence.get("level", "INSUFFICIENT_EVIDENCE")).replace("_", " ")),
    ])

    message = confidence.get("message")

    if message:

        if confidence.get("rule_change_allowed"):

            st.success(message)

        else:

            st.warning(message)


def _trade_doctor_display_frame(trades):

    frame = pd.DataFrame(trades).copy()

    if frame.empty:

        return frame

    frame = frame.rename(columns={"Trade Key": "Trade"})
    columns = [
        "Trade", "Symbol", "Direction", "Setup", "Entry Grade", "Exit Grade",
        "Exit Verdict", "Exit Verdict Reason", "Exit Trigger",
        "Trend Capture %", "Left On Table", "Engineering Recommendation",
    ]

    return frame[[column for column in columns if column in frame.columns]]


def _render_trade_doctor(trades):

    doctor_rows = _trade_doctor_display_frame(trades)

    if doctor_rows.empty:

        return

    st.markdown("### Trade Doctor")
    st.caption(
        "Post-exit review only. Grades, verdicts, triggers, and recommendations do not alter trade execution."
    )
    st.dataframe(
        _format_trend_capture_table(doctor_rows),
        width="stretch",
        hide_index=True,
    )


def _render_entry_exit_v2_comparison(comparison):

    comparison = comparison or {}
    summary = comparison.get("summary") or {}

    if not summary:

        return

    st.markdown("## Entry/Exit V2 Comparison")
    st.caption("Completed-trade shadow comparison only. V1 remains the execution engine.")
    _render_compact_card_grid([
        ("Trades Compared", summary.get("Trades compared", 0)),
        ("V2 Higher R", summary.get("V2 higher R", 0)),
        ("Avg R Delta", _format_efficiency_number(summary.get("Avg R improvement"))),
        ("Entry Delta (min)", _format_efficiency_number(summary.get("Avg entry delay minutes"))),
        ("Exit Delta (min)", _format_efficiency_number(summary.get("Avg exit delay minutes"))),
        ("MFE Delta", _format_efficiency_number(summary.get("Avg MFE improvement"))),
    ])

    trades = comparison.get("trades") or []

    if trades:

        columns = [
            "symbol", "direction", "entry_time_v1", "entry_time_v2",
            "entry_price_v1", "entry_price_v2", "exit_time_v1", "exit_time_v2",
            "final_r_v1", "final_r_v2", "final_r_delta", "mfe_r_delta",
            "entry_delta_minutes", "exit_delta_minutes",
        ]
        frame = pd.DataFrame(trades)
        st.dataframe(
            _display_safe_dataframe(frame[[column for column in columns if column in frame.columns]]),
            width="stretch",
            hide_index=True,
        )

    outcomes = comparison.get("trend_outcomes") or []

    if outcomes:

        st.markdown("### Trend Outcome Attribution")
        columns = [
            "engine_version", "symbol", "stock_direction", "trade_direction",
            "stock_finish", "trade_finish", "trend_outcome",
            "engine_captured_trend", "final_r", "trend_capture_pct",
        ]
        frame = pd.DataFrame(outcomes)
        st.dataframe(
            _display_safe_dataframe(frame[[column for column in columns if column in frame.columns]]),
            width="stretch",
            hide_index=True,
        )

    failures = comparison.get("execution_failures") or []

    if failures:

        st.markdown("### Strong Trend, Failed Execution")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(failures)),
            width="stretch",
            hide_index=True,
        )


def _render_v2_learning_summary(learning):

    summary = (learning or {}).get("summary") or {}

    if not summary:

        return

    st.markdown("### V2 Learning Summary")
    _render_compact_card_grid([
        ("Records", summary.get("Completed learning records", 0)),
        ("Avg Trend Age", _format_efficiency_number(summary.get("Avg Trend Age"))),
        ("Avg Entry Efficiency", _format_efficiency_number(summary.get("Avg Entry Efficiency"))),
        ("Avg Exit Trend Health", _format_efficiency_number(summary.get("Avg Exit Trend Health"))),
        ("Avg MFE R", _format_efficiency_number(summary.get("Avg MFE R"))),
        ("Avg MAE R", _format_efficiency_number(summary.get("Avg MAE R"))),
        ("Avg Trend Capture", _format_efficiency_pct(summary.get("Avg Trend Capture %"))),
        ("Avg TES", _format_efficiency_number(summary.get("Avg TES"))),
    ])


def _render_validation_decision_analysis(analysis):

    analysis = analysis or {}
    summary = analysis.get("summary") or {}

    if not summary:

        return

    st.markdown("## Decision Analysis")
    _render_compact_card_grid(list(summary.items()))

    blockers = analysis.get("top_blockers") or []

    if blockers:

        st.markdown("### Top Blockers")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(blockers)),
            width="stretch",
            hide_index=True,
        )

    missed = analysis.get("missed_candidates") or []

    if missed:

        st.markdown("### High-Quality Non-Entries")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(missed)),
            width="stretch",
            hide_index=True,
        )
def _render_candidate_intelligence(intelligence):

    intelligence = intelligence or {}
    summary = intelligence.get("summary") or {}

    if not summary:

        return

    st.markdown("## Candidate Intelligence")
    st.caption("Research evidence only. These classifications do not alter scanner, risk, or execution decisions.")
    _render_compact_card_grid([
        ("Good Candidates", summary.get("good_candidates", 0)),
        ("Opened", summary.get("opened", 0)),
        ("Correct Skips", summary.get("correct_skips", 0)),
        ("Correct Blocks", summary.get("correct_blocks", 0)),
        ("Missed Winners", summary.get("missed_winners", 0)),
        ("Investigate", summary.get("investigate", 0)),
    ])

    sections = [
        ("High Quality Blocked Candidates", "high_quality_blocked"),
        ("Candidate Outcome Matrix", "outcome_matrix"),
        ("Missed Winner Attribution", "missed_winner_breakdown"),
        ("Investigation Queue", "investigation_queue"),
    ]

    for title, key in sections:

        rows = intelligence.get(key) or []

        if rows:

            st.markdown(f"### {title}")
            st.dataframe(
                _display_safe_dataframe(pd.DataFrame(rows)),
                width="stretch",
                hide_index=True,
            )


def _render_spread_calibration(calibration):
    """Does the option quality score predict what the round trip actually costs?

    Open question from 2026-08-01, watchlist 2.6. The claim that it does not was
    built on pre-`eb56f75` data where every figure was the measurement artifact,
    and was retracted. This panel exists so the clean version answers itself as
    trades close, rather than being re-argued from whatever is to hand.

    Zero measurable trades is the expected state until trades open *and* close
    after that fix, so it is stated rather than hidden -- an empty panel would
    read as "nothing wrong" when it means "nothing measured yet".
    """

    calibration = calibration or {}
    measurable = int(calibration.get("measurable_trades") or 0)

    st.markdown("### Option Quality vs Spread Paid")

    if not measurable:

        st.info(
            "No measurable trades yet. Premium economics can only be "
            "reconstructed for trades opened after the entry ask was frozen "
            "(`eb56f75`, 2026-07-31); earlier trades report as unpriced rather "
            "than contributing a meaningless average. Waiting on the first "
            "full open-to-close cycle since then."
        )

        return

    flagged = int(calibration.get("high_score_wide_spread_count") or 0)
    gap = calibration.get("quality_vs_cost_gap")

    _render_compact_card_grid([
        ("Measurable Trades", measurable),
        ("High Score, Wide Spread", flagged),
        ("Entry vs Realised Gap", f"{gap:+.2f}%" if gap is not None else "-"),
    ])

    if flagged >= 2:

        st.warning(
            f"{flagged} trades scored 80+ on option quality yet cost more than "
            "6% to round-trip. That is the threshold at which the quality score "
            "needs the entry spread folded into it (watchlist 2.6)."
        )

    if gap is not None and gap > 2:

        st.warning(
            f"Realised cost is running {gap:+.2f}% above the spread quoted at "
            "entry, which means spreads are widening while positions are held. "
            "No entry-time score can catch that, and nothing currently models it."
        )

    rows = calibration.get("rows") or []

    if rows:

        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(rows)),
            width="stretch",
            hide_index=True,
        )


def _render_cached_validation_state(state):

    st.subheader("Validation")
    st.caption(f"Cached validation state generated: {state.get('generated_at', 'unknown')}")
    kpis = state.get("kpis", {})
    scanner = kpis.get("scanner", {})
    paper = kpis.get("paper", {})
    trend = kpis.get("trend_capture", {})
    _render_compact_card_grid([
        ("Rows", scanner.get("rows", 0)),
        ("ENTER_PAPER", scanner.get("enter_paper", 0)),
        ("Review", scanner.get("review", 0)),
        ("Closed Trades", paper.get("closed_trades", 0)),
        ("Win Rate", f"{paper.get('win_rate')}%" if paper.get("win_rate") is not None else "-"),
        ("Total R", paper.get("total_r", 0)),
        ("Avg Capture", _format_efficiency_pct(trend.get("average_capture"))),
        ("TES", _format_efficiency_number(trend.get("trade_efficiency_score"))),
    ])
    trend_payload = state.get("trend_capture", {})

    _render_validation_diagnosis(state.get("diagnosis"))
    _render_strategy_confidence(state.get("strategy_confidence"))

    telegram_quality = state.get("telegram_quality") or {}
    _render_compact_card_grid([
        ("Telegram Misses", telegram_quality.get("misses", 0)),
        ("False Alerts", telegram_quality.get("false_alerts", 0)),
    ])

    # Spread calibration was rendered here, from the cached state. The page now
    # renders it from Postgres before this function is called, so it appears
    # whether or not a state file exists -- see `pages/validation.py`.

    delay_attribution = state.get("delay_attribution") or []

    if delay_attribution:

        st.markdown("### Delay Attribution")
        st.dataframe(_display_safe_dataframe(pd.DataFrame(delay_attribution)), width="stretch", hide_index=True)

    candidate_outcomes = state.get("candidate_outcomes") or []

    if candidate_outcomes:

        st.markdown("### Candidate Outcomes")
        columns = [
            column for column in [
                "symbol", "setup", "entered", "became_winner", "became_loser",
                "telegram_sent", "telegram_miss", "false_alert",
            ] if column in candidate_outcomes[0]
        ]
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(candidate_outcomes)[columns]),
            width="stretch",
            hide_index=True,
        )

    _render_entry_exit_v2_comparison(state.get("entry_exit_v2"))
    _render_observational_analytics(
        state.get("observational_analytics")
    )
    _render_decision_waterfalls(
        (state.get("observational_analytics") or {})
    )
    _render_v2_learning_summary(state.get("v2_learning"))
    _render_validation_decision_analysis(state.get("decision_analysis"))
    _render_candidate_intelligence(state.get("candidate_intelligence"))

    for title, key in [
        ("Exit Verdict Distribution", "exit_verdict_distribution"),
        ("Trend Capture by Setup", "by_setup"),
        ("Trend Capture by Regime", "by_regime"),
        ("Trend Capture by Exit Reason", "by_exit_reason"),
    ]:

        rows = trend_payload.get(key) or []

        if rows:

            st.markdown(f"### {title}")
            st.dataframe(
                _display_safe_dataframe(pd.DataFrame(rows)),
                width="stretch",
                hide_index=True
            )

    _render_cached_recommendations(state.get("recommendations"))
    _render_trade_efficiency(state.get("trade_efficiency"))


def _render_observational_analytics(analytics):

    analytics = analytics or {}
    timing = analytics.get("entry_timing") or {}
    ranking = analytics.get("trade_ranking") or []
    waterfalls = analytics.get("exit_waterfalls") or []

    st.subheader("Entry Timing And Trade Ranking")
    _render_compact_card_grid([
        ("Average Entry Timing", _format_efficiency_number(
            timing.get("average_score")
        )),
        ("Late Entries", len(timing.get("late_entries") or [])),
        ("Ranked Candidates", len(ranking)),
    ])

    grades = timing.get("grades") or []

    if grades:

        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(grades)),
            width="stretch",
            hide_index=True,
        )

    if ranking:

        st.markdown("### Trade Quality Ranking")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(ranking)),
            width="stretch",
            hide_index=True,
        )

    if timing.get("late_entries"):

        st.markdown("### Late Entry Analysis")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(timing["late_entries"])),
            width="stretch",
            hide_index=True,
        )

    if waterfalls:

        st.markdown("### Exit Waterfalls")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(waterfalls)),
            width="stretch",
            hide_index=True,
        )


def _render_decision_waterfalls(analytics):

    analytics = analytics or {}
    waterfalls = analytics.get("decision_waterfalls") or []
    blocking_summary = analytics.get("blocking_stage_summary") or {}
    st.subheader("Decision Waterfall")

    if not waterfalls:

        st.info("Decision paths will appear after scanner diagnostics are persisted.")
        return

    options = [
        f"{item.get('symbol') or 'UNKNOWN'} | {item.get('setup') or 'NO_SETUP'}"
        for item in waterfalls
    ]
    selected_option = st.selectbox(
        "Inspect candidate path",
        options=options,
        key="decision_waterfall_candidate",
    )
    selected = waterfalls[options.index(selected_option)]
    blocker = selected.get("first_blocker") or {}
    _render_compact_card_grid([
        ("Candidate", selected.get("symbol") or "UNKNOWN"),
        ("Action", selected.get("final_action") or "UNKNOWN"),
        ("Final Reason", selected.get("final_reason") or "None"),
        ("First Blocker", selected.get("blocking_rule") or "None"),
        ("Blocker Stage", selected.get("blocking_stage") or "None"),
    ])
    stages = []

    for stage in selected.get("stages") or []:

        status = (
            "PASS"
            if stage.get("passed") is True
            else "FAIL"
            if stage.get("passed") is False
            else "-"
        )
        stages.append({
            "Stage": stage.get("stage"),
            "Status": status,
            "Summary": stage.get("summary"),
            "Passed Rules": ", ".join(stage.get("passed_rules") or []),
            "Failed Rules": ", ".join(
                rule.get("rule", "")
                for rule in stage.get("failed_rules") or []
            ),
        })
    st.dataframe(
        _display_safe_dataframe(pd.DataFrame(stages)),
        width="stretch",
        hide_index=True,
    )
    failed_rules = [
        {
            "Stage": stage.get("stage"),
            "Rule": rule.get("rule"),
            "Actual": rule.get("actual"),
            "Required": rule.get("required"),
        }
        for stage in selected.get("stages") or []
        for rule in stage.get("failed_rules") or []
    ]

    if failed_rules:

        st.markdown("#### Failed Rules")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(failed_rules)),
            width="stretch",
            hide_index=True,
        )

    blocking_stages = blocking_summary.get("stages") or []

    if blocking_stages:

        st.markdown("#### Today's Blocking Stages")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(blocking_stages)),
            width="stretch",
            hide_index=True,
        )

    comparisons = analytics.get("v1_v2_waterfalls") or []
    comparison = next(
        (
            item for item in comparisons
            if item.get("symbol") == selected.get("symbol")
        ),
        None,
    )

    if comparison:

        st.markdown("#### V1 vs V2 Decision Path")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame([{
                "V1 Action": comparison.get("v1", {}).get("final_action"),
                "V2 Action": comparison.get("v2", {}).get("final_action"),
                "Actions Disagree": comparison.get("actions_disagree"),
                "First Disagreement": comparison.get("first_disagreement"),
            }])),
            width="stretch",
            hide_index=True,
        )
        comparison_stages = []

        for v1_stage, v2_stage in zip(
            comparison.get("v1", {}).get("stages") or [],
            comparison.get("v2", {}).get("stages") or [],
        ):

            comparison_stages.append({
                "Stage": v1_stage.get("stage"),
                "V1": v1_stage.get("summary"),
                "V2": v2_stage.get("summary"),
                "V1 Pass": v1_stage.get("passed"),
                "V2 Pass": v2_stage.get("passed"),
            })

        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(comparison_stages)),
            width="stretch",
            hide_index=True,
        )


def _render_trade_efficiency(efficiency):

    efficiency = efficiency or {}
    summary = efficiency.get("summary") or {}

    st.subheader("Trade Efficiency Analytics")
    _render_compact_card_grid([
        ("Average Capture", _format_efficiency_pct(summary.get("average_capture"))),
        ("Today's Capture", _format_efficiency_pct(summary.get("today_capture"))),
        ("Best Capture", _format_efficiency_pct(summary.get("best_capture"))),
        ("Worst Capture", _format_efficiency_pct(summary.get("worst_capture"))),
        ("Average TES", _format_efficiency_number(summary.get("average_tes"))),
        ("Average R", _format_efficiency_number(summary.get("average_r"))),
        ("Average Left On Table", _format_efficiency_number(summary.get("average_left_on_table"))),
    ])

    trades = efficiency.get("trades") or []

    if trades:

        st.markdown("### Trade Efficiency Table")
        st.dataframe(_display_safe_dataframe(pd.DataFrame(trades)), width="stretch", hide_index=True)
        _render_trade_doctor(trades)

    charts = efficiency.get("charts") or {}

    if charts:

        st.markdown("### Charts")

        for title, key, category, value in [
            ("Capture %", "capture_histogram", "Trade Key", "Trend Capture %"),
            ("TES Histogram", "tes_histogram", "Trade Key", "Trade Efficiency Score"),
            ("Capture by Setup", "capture_by_setup", "Setup", "Average Trend Capture %"),
            ("Capture by Regime", "capture_by_regime", "Market Regime", "Average Trend Capture %"),
            ("Exit Verdict", "exit_verdict", "Exit Verdict", "Count"),
            ("Opportunity Cost", "opportunity_cost", "Trade Key", "Left On Table"),
            ("Trend Health Scatter", "trend_health_scatter", "Trend Health Score", "Trend Capture %"),
        ]:

            rows = charts.get(key) or []

            if rows and category in rows[0] and value in rows[0]:

                st.markdown(f"**{title}**")
                chart = pd.DataFrame(rows).set_index(category)
                st.bar_chart(chart[[value]])

    _render_cached_recommendations(efficiency.get("recommendations"))


def _render_cached_replay_state(state, trading_day):
    st.subheader("Replay")
    st.caption(f"Cached replay state generated: {state.get('generated_at', 'unknown')}")
    _render_compact_card_grid([
        ("Status", state.get("status", "UNKNOWN")),
        ("Scanner Rows", state.get("scanner_rows", 0)),
        ("Replay Rows", state.get("replay_rows", 0)),
        ("Coverage", f"{state.get('coverage_pct', 0)}%"),
        ("Missing Indicators", state.get("missing_indicators", 0)),
        ("Partial Replay", state.get("partial_replay", 0)),
    ])

    if st.button("Generate Replay", key="generate_offline_replay_cached"):

        try:

            _generate_offline_replay(trading_day)
            st.success("Offline replay generated.")

        except Exception as exc:

            st.error(f"Offline replay failed: {exc}")

    if state.get("errors"):

        for error in state.get("errors", []):

            st.info(error)

    blockers = state.get("blockers") or []

    if blockers:

        st.markdown("### Today's Biggest Blockers")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(blockers)),
            width="stretch",
            hide_index=True
        )

    top_misses = state.get("top_misses") or []

    if top_misses:

        st.markdown("### Top Misses")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(top_misses)),
            width="stretch",
            hide_index=True
        )

    summary = state.get("replay_summary") or []

    if summary:

        st.markdown("### Replay Summary")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(summary)),
            width="stretch",
            hide_index=True
        )


def _render_cached_report_state(state):
    st.subheader("Reports")
    st.caption(f"Cached report state generated: {state.get('generated_at', 'unknown')}")
    daily_report = state.get("daily_report", {})
    root_report = state.get("root_report", {})
    _render_compact_card_grid([
        ("Status", state.get("status", "UNKNOWN")),
        ("Daily Report", "YES" if daily_report.get("exists") else "NO"),
        ("Root Report", "YES" if root_report.get("exists") else "NO"),
        ("Daily Size", daily_report.get("size_bytes", 0)),
    ])

    if state.get("errors"):

        for error in state.get("errors", []):

            st.info(error)

    _render_trade_efficiency_history(state.get("historical_trade_efficiency"))
    _render_v2_learning_history(state.get("historical_v2_learning"))
    _render_observational_analytics_history(
        state.get("historical_observational_analytics")
    )
    _render_blocking_stage_history(
        state.get("historical_blocking_trends")
    )


def _render_trade_efficiency_history(history):

    history = history or {}
    daily = pd.DataFrame(history.get("daily") or [])
    st.subheader("Trade Efficiency Summary")

    if daily.empty:

        st.info("Trade efficiency history will appear after validation caches are available.")
        return

    periods = [
        ("Today", daily.tail(1)),
        ("Yesterday", daily.iloc[-2:-1]),
        ("5 Day", daily.tail(5)),
        ("20 Day", daily.tail(20)),
    ]
    cards = []

    for label, window in periods:
        cards.extend([
            (f"{label} Capture", _format_efficiency_pct(window["Capture"].mean())),
            (f"{label} TES", _format_efficiency_number(window["TES"].mean())),
            (f"{label} Avg R", _format_efficiency_number(window["Average R"].mean())),
            (f"{label} Win Rate", _format_efficiency_pct(window["Win Rate"].mean())),
        ])

    _render_compact_card_grid(cards)
    st.markdown("### Daily Trend Capture %")
    st.line_chart(daily.set_index("Trading Day")[["Capture", "Rolling Average Capture"]])

    for title, key in [
        ("Weekly TES", "weekly"), ("Monthly TES", "monthly"),
        ("Capture by Setup", "setup"), ("Capture by Regime", "regime"),
        ("Capture by Exit", "exit"), ("Capture by Weekday", "weekday"),
    ]:

        rows = history.get(key) or []

        if rows:

            st.markdown(f"### {title}")
            st.dataframe(_display_safe_dataframe(pd.DataFrame(rows)), width="stretch", hide_index=True)


def _render_v2_learning_history(history):
    history = history or {}
    daily = pd.DataFrame(history.get("daily") or [])
    st.subheader("Execution Learning Trends")

    if daily.empty:
        st.info("V2 execution-learning trends will appear after completed shadow trades.")
        return

    cards = [
        ("Avg Trend Age", _format_efficiency_number(daily["Trend Age"].mean())),
        ("Avg Entry Efficiency", _format_efficiency_number(daily["Entry Efficiency"].mean())),
        ("Avg Trend Capture", _format_efficiency_pct(daily["Trend Capture %"].mean())),
        ("Avg TES", _format_efficiency_number(daily["TES"].mean())),
    ]
    _render_compact_card_grid(cards)
    st.line_chart(
        daily.set_index("Trading Day")[[
            "Trend Age", "Entry Efficiency", "Trend Capture %", "TES",
        ]]
    )
    phases = history.get("exit_phase") or []
    if phases:
        st.markdown("### V2 Exit Phase")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(phases)),
            width="stretch",
            hide_index=True,
        )


def _render_observational_analytics_history(history):

    daily = pd.DataFrame((history or {}).get("daily") or [])
    st.subheader("Entry Timing And Ranking Trends")

    if daily.empty:

        st.info("Entry Timing and Trade Quality trends will appear after validation caches are available.")
        return

    _render_compact_card_grid([
        ("Avg Entry Timing", _format_efficiency_number(
            pd.to_numeric(daily["Average Entry Timing"], errors="coerce").mean()
        )),
        ("Avg TQS", _format_efficiency_number(
            pd.to_numeric(daily["Average TQS"], errors="coerce").mean()
        )),
        ("Avg Rank", _format_efficiency_number(
            pd.to_numeric(daily["Average Rank"], errors="coerce").mean()
        )),
    ])
    st.dataframe(
        _display_safe_dataframe(daily),
        width="stretch",
        hide_index=True,
    )


def _render_blocking_stage_history(history):

    history = history or {}
    daily = pd.DataFrame(history.get("daily") or [])
    dominant = pd.DataFrame(history.get("dominant_daily") or [])
    st.subheader("Blocking Stage Trends")

    if daily.empty:

        st.info("Blocking-stage trends will appear after validation caches are available.")
        return

    if not dominant.empty:

        st.markdown("### Daily Dominant Blocking Stage")
        st.dataframe(
            _display_safe_dataframe(dominant),
            width="stretch",
            hide_index=True,
        )

    st.markdown("### Blocking Stages By Day")
    st.dataframe(
        _display_safe_dataframe(daily),
        width="stretch",
        hide_index=True,
    )


def _render_market_coverage_lazy(report_date):

    from app.dashboard_components.market_coverage import render_market_coverage

    render_market_coverage(report_date)


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
    """DEPRECATED no-op. The scanner owns the suggestion lifecycle.

    Advancing suggestion state from a dashboard render meant it depended on a
    browser tab being open, and mutated shared state from the read-only side.
    `app/runtime/paper_position_lifecycle.py::sync_scan_suggestions()` now runs it
    during scan finalization. Retained as a no-op so any remaining caller is inert.
    """

    return None


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

            container.markdown(
                """
                <div class="download-status download-status-missing">
                    <div class="download-status-label">{label}</div>
                    <div class="download-status-note">Not available yet</div>
                </div>
                """.format(label=escape(str(label))),
                unsafe_allow_html=True
            )
            return False

        stat = file_path.stat()
        container.markdown(
            """
            <div class="download-status download-status-ready">
                <div class="download-status-label">{label}</div>
                <div class="download-status-note">Ready to download</div>
            </div>
            """.format(label=escape(str(label))),
            unsafe_allow_html=True
        )
        container.download_button(
            label=label,
            data=file_path.read_bytes(),
            file_name=file_name or file_path.name,
            mime=mime,
            key=f"{key_base}_{stat.st_mtime_ns}"
        )
        return True

    except Exception as exc:

        container.markdown(
            """
            <div class="download-status download-status-error">
                <div class="download-status-label">{label}</div>
                <div class="download-status-note">Unavailable</div>
            </div>
            """.format(label=escape(str(label))),
            unsafe_allow_html=True
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


def _load_auto_paper_decision_log():

    return load_json_file(
        str(AUTO_PAPER_DECISION_LOG_FILE),
        []
    )


def _save_auto_paper_decision_log(entries):

    save_json_file(
        str(AUTO_PAPER_DECISION_LOG_FILE),
        entries[-500:]
    )


def _record_auto_paper_decision(symbol, decision, reason, row=None, trade=None, controls=None):

    # Imported here rather than at module scope to match how this file already
    # reaches into the runtime package (see eod_force_close_reason) and to keep
    # Streamlit's import of the dashboard independent of the runtime package.
    from app.runtime.paper_automation_support import (
        _decision_rr,
        _effective_gate_floors,
        write_auto_paper_decision,
    )

    decision_time = _current_et()
    trading_day = get_trading_day(decision_time)
    scan_timestamp = decision_time.strftime("%Y-%m-%d %H:%M:%S")
    controls = controls or {}
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
        "scan_id": get_scan_id(trading_day, decision_time),
        "scan_timestamp": scan_timestamp,
        # See the scan-path recorder: the naive value is ET and reads as UTC once
        # it reaches a timestamptz column. These carry the unambiguous times.
        "scan_timestamp_et": decision_time.isoformat(),
        "scan_timestamp_utc": decision_time.astimezone(timezone.utc).isoformat(),
        **classify_decision_time(decision_time),
        "gate_mode": "auto_paper",
        # Shared with the scan-path recorder: the scanner gate's regime-escalated
        # floor when the row carries one, and the auto-paper control only when it
        # does not. Recording the control as "the floor used" made every
        # SETUP_BELOW_THRESHOLD row read as a contradiction.
        **_effective_gate_floors(row, controls),
        "auto_paper_min_rr": controls.get("min_rr"),
        "auto_paper_min_setup": controls.get("min_setup"),
        "symbol": symbol,
        "decision": decision,
        "reason": reason,
        "trade_key": trade.get("trade_key") if trade else None,
        "entry_source": trade.get("entry_source") if trade else None,
        "top_candidate": row.get("Top Candidate") if row is not None else None,
        "setup_percent": row.get("Setup %") if row is not None else None,
        # Shared with the scan-path recorder. "RR" exists only on frames that went
        # through _load_scanner_output(), which synthesises it at line 1245; rows
        # reaching here from elsewhere carry "Candidate RR" instead.
        "rr": _decision_rr(row),
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
        "action_status": action_status,
        "blocked_by": blocked_by,
        "scanner_blocked_by": scanner_blocked_by,
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
    write_auto_paper_decision(entry, trading_day)


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

    if _safe_float(row.get("Setup %"), 0) < MIN_SETUP_BASE:

        return False, f"setup below {MIN_SETUP_BASE:.0f}"

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


def _render_daily_review_export(report_date, container):
    """The whole trading day in one archive, built only when asked for.

    Building it reads every daily artifact and rebuilds the decision waterfall
    and rule evaluations from the scanner frame, so doing it eagerly charged the
    container that work on every rerun for a button nobody had clicked.
    """

    session_key = f"daily_review_export_{report_date}"

    if container.button("Build daily review export", key=f"build_{session_key}"):

        try:

            from app.analytics.daily_review_export import build_daily_review_export

            st.session_state[session_key] = build_daily_review_export(report_date)

        except Exception as exc:

            st.session_state.pop(session_key, None)
            container.caption(f"Daily review export unavailable: {exc}")

    prepared = st.session_state.get(session_key)

    if not prepared:

        container.caption(
            "Packages the day's analytics, audit and operator state, including "
            "the artifacts nothing but this container holds."
        )
        return

    archive, manifest = prepared
    available = sum(
        1 for item in manifest["artifacts"].values()
        if item.get("available")
    )

    container.download_button(
        label=f"Daily review export ({len(archive) // 1024} KB)",
        data=archive,
        file_name=f"review_{report_date}.zip",
        mime="application/zip",
        key=f"download_{session_key}",
        help=f"{available} artifacts with content.",
    )


def _render_operator_file_downloads(report_date, container):
    """Single-file grabs for what an operator reaches for on its own.

    Everything else the day produced is in the review export. Dropped from the
    old list: `scanner_output.xlsx` (root legacy file, and not what the page
    reads -- the dashboard prefers `data/live/scanner_output_latest.csv`, so the
    button could hand over a file that did not match the screen);
    `trade_state.json` (legacy, promoted once on first lookup);
    `auto_paper_decision_log.json` (the capped 500-row UI copy of a CSV that
    Postgres also holds); `candidate_snapshots.csv` (never written -- the writer
    prefers parquet and pyarrow is always present on Cloud); and
    `candidate_snapshots.parquet`, `auto_paper_decisions.csv` and
    `market_opportunity_audit.csv`, all of which are in Postgres.
    """

    # The `Live state` expander is gone. `paper_trade_state.json` is in
    # `paper_trades` and `telegram_dispatch_audit.jsonl` is in
    # `telegram_dispatch`, so both were a second copy of a durable record.
    # `suggested_trade_state.json` has no table -- it is the one piece of live
    # state with no database home -- but the review export carries it under
    # `state/`, so nothing is lost by dropping the button.
    container.caption(
        "Per-file downloads live in the review export. Trade and dispatch state "
        "are in Postgres."
    )


DASHBOARD_PAGES = ["Trading", "Validation", "Research", "Developer"]

# The four pages Research absorbed on 2026-07-31.
_FOLDED_INTO_RESEARCH = {"Replay", "Regression", "Reports", "Learning"}


def _migrate_dashboard_page(stored):
    """Keep a session open across the navigation change on a valid page.

    Streamlit raises when a radio's stored session value is not one of its
    options, so a browser tab left open across the redeploy that folded Replay,
    Regression, Reports and Learning into Research would break on its next
    rerun. Send those sessions to the page that absorbed them.
    """

    if stored in DASHBOARD_PAGES:

        return stored

    if stored in _FOLDED_INTO_RESEARCH:

        return "Research"

    return "Trading"


def _research_frame():
    """The scanner frame, loaded only if a Research tab actually needs it.

    Replay and Reports fall back to it when their cached state file is missing;
    Regression and Learning never touch it. Loading it up front would charge
    every Research visit for reading `scanner_output.xlsx`.
    """

    frame = _load_scanner_output()

    if frame.empty:

        st.warning("No scanner output available. Run a scan first.")
        return None

    return frame


@st.cache_data(ttl=20)
def _remote_engine_summary():
    """Heartbeats from Postgres, cached briefly.

    The sidebar renders on every rerun, including auto-refresh, so an uncached
    query here would be a Neon round trip every few seconds for a value that
    changes once per scan.

    Import guarded, not just the query. On 2026-08-01 Streamlit Cloud served a
    partial checkout -- a new `dashboard.py` against a stale `render_context.py`
    -- and the bare import raised ImportError out of `_render_system_status`,
    which runs before routing. That took the **whole dashboard** down over a
    status caption. Nothing about a heartbeat is worth more than the page it
    decorates: if it cannot be read, the panel says no remote engine and
    everything else still renders.
    """

    try:

        from app.ui.render_context import scan_engine_heartbeats

        return scan_engine_heartbeats() or {}

    except Exception as exc:

        print(f"[SYSTEM STATUS WARNING] heartbeat unavailable: {exc}")

        return {}


def _engine_from_heartbeat(row):
    """Heartbeat row shaped like `engine_status()`, times already in ET.

    Shaping is shared with `render_context.engine_status` so the sidebar and the
    Operator Console cannot disagree about whether an engine is running. The ET
    conversion used to live here, which meant it applied to the sidebar and
    nowhere else -- every other reader of `engine_status()` printed UTC under an
    "ET" label. It now happens in `heartbeat_to_engine_status`, at the one point
    where a database row becomes an engine status.
    """

    from app.runtime.scan_engine_heartbeat import heartbeat_to_engine_status

    return heartbeat_to_engine_status(row)


def _render_context_symbol(name, fallback):
    """Import one name from `render_context`, or fall back to a local stand-in.

    Streamlit Cloud can serve a partial checkout: on 2026-08-01 it ran a new
    `dashboard.py` against an older `render_context.py` twice, and each time a
    bare import of a newly added name raised ImportError out of
    `_render_system_status` -- which runs before page routing, so a missing
    caption took the whole dashboard down.

    The sidebar's job is to report health. Degrading one line of it is an
    acceptable cost for a stale deploy; refusing to render anything is not.
    """

    try:

        from app.ui import render_context

        return getattr(render_context, name)

    except Exception as exc:

        print(f"[DASHBOARD] render_context.{name} unavailable ({exc}); using fallback")

        return fallback


def _fallback_engine_label(engine, short=False):

    owner = str((engine or {}).get("owner") or "").strip()

    if not owner:

        return "ENGINE" if short else "Engine"

    return f"{owner} engine".upper() if short else f"{owner} engine".capitalize()


def _fallback_database_state():

    try:

        from app.db.persistence import db_writes_enabled

        return "ON" if db_writes_enabled() else "OFF"

    except Exception:

        return "OFF"


def _render_system_status(container):
    """Is the machine healthy, answered above the controls that change it.

    The engine panel, the key status and the database state were previously
    scattered across three sidebar sections or, in the case of runtime keys, a
    function that nothing called. An operator checking "is it working" had to
    read the whole sidebar to find out.

    Every `render_context` name is resolved through `_render_context_symbol`.
    This function runs before routing, so anything that raises here is not a
    broken panel, it is a blank site.
    """

    engine_status = _render_context_symbol("engine_status", lambda: {})
    engine_label = _render_context_symbol("engine_label", _fallback_engine_label)

    engine = engine_status()
    alive = bool(engine.get("thread_alive"))

    container.subheader("System")

    # A supervisor thread in this process is the local answer. Once scanning is
    # owned by the Render worker there is no such thread here, so fall back to
    # the heartbeat: "no thread" and "no engine anywhere" are different claims,
    # and reporting the first as the second is how a healthy system gets
    # diagnosed as broken.
    remote = _remote_engine_summary()

    if not alive and remote.get("live"):

        engine = _engine_from_heartbeat(remote["live"][0])

    failures = int(engine.get("failures") or 0)
    reporting = alive or bool(remote.get("live"))

    if remote.get("conflict"):

        container.error(
            "Two scan engines are running: "
            + ", ".join(remote.get("owners") or [])
            + ". They will double-open positions — the scan lock is a local file "
            "and cannot serialise across hosts. Set SCAN_ENGINE_OWNER on one."
        )

    if not reporting:

        container.error("Scan engine not running")

    else:

        interval = engine.get("interval_seconds")
        # Which engine, then what it is doing. One line each: the previous single
        # line read as one opaque string, and the owner in the middle of it was
        # the part an operator most needed and least saw.
        container.caption(f"**{engine_label(engine)}**")
        container.caption(
            f"Status {engine.get('status') or 'IDLE'}"
            + (f" · every {int(interval) // 60} min" if interval else "")
        )

    last_completed = engine.get("last_completed_at")

    if last_completed:

        # Duration matters on this container: a scan takes 200-285s against a
        # 300s cadence, so it is the number that says whether scans are about to
        # start overlapping.
        duration = engine.get("last_duration_seconds")
        container.caption(
            f"Last scan {str(last_completed)[11:19]} ET"
            + (f" in {int(duration)}s" if duration else "")
        )

    next_due = engine.get("next_due_at")

    if next_due and reporting:

        container.caption(f"Next due {str(next_due)[11:19]} ET")

    # "this run", because the counter lives in the loop's stack frame and resets
    # whenever the container restarts, while `Last scan` above survives a restart
    # by design. Unqualified, the two read as a contradiction: a worker showing
    # `Scans 0` directly beneath `Last scan 12:03:26 ET` looks broken and is not.
    container.caption(
        f"Scans {int(engine.get('scans') or 0)} this run"
        + (f" · {failures} failed" if failures else "")
    )

    # Reachability, not intent. "DB writes on" was true of a container that could
    # not reach Postgres at all, so the one indicator that should have caught a
    # blind process instead vouched for it.
    db_state = _render_context_symbol("database_state", _fallback_database_state)()
    database = {
        "ON": "DB writes on",
        "OFF": "DB writes OFF",
    }.get(db_state, "DB UNREACHABLE")
    polygon = "Polygon key set" if os.getenv("POLYGON_API_KEY", "").strip() else "Polygon key MISSING"
    container.caption(f"{database} · {polygon}")

    if db_state == "UNREACHABLE":

        container.error(
            "Database unreachable. Trade history, open positions and alert dedup "
            "all read as empty in this state — treat anything reporting zero as "
            "unknown, not as nothing."
        )

    if failures and engine.get("last_error"):

        container.warning(f"Last scan error: {engine['last_error']}")


def _render_download_exports():
    """The one home for downloads.

    These were previously split across the Operations block and a
    `Tools: Downloads` expander, which between them served the validation
    report, the replay summary and `scanner_output.xlsx` from two places each.
    """

    downloads = st.sidebar.expander("Downloads", expanded=False)

    report_date = st.session_state.get(
        "daily_validation_report_date",
        datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    )

    _render_daily_review_export(report_date, downloads)

    _render_file_download_button(
        "Post-market review (.html)",
        daily_path(report_date, "post_market_review.html"),
        file_name=f"post_market_review_{report_date}.html",
        mime="text/html",
        key=f"download_post_market_review_{report_date}",
        container=downloads
    )

    _render_file_download_button(
        "Validation report (.html)",
        daily_path(report_date, "daily_validation_report.html"),
        file_name=f"daily_validation_{report_date}.html",
        mime="text/html",
        key=f"download_validation_report_{report_date}",
        container=downloads
    )

    _render_file_download_button(
        "Replay summary (.csv)",
        daily_path(report_date, "offline_replay_summary.csv"),
        file_name="offline_replay_summary.csv",
        mime="text/csv",
        key=f"download_replay_summary_{report_date}",
        container=downloads
    )

    _render_operator_file_downloads(report_date, downloads)


def _generate_daily_validation_report(report_date, finalize_report=True):

    from types import SimpleNamespace
    from tools.daily_validation_report import build_report
    from app.ui.cache.report_state_builder import write_report_state

    with measure_runtime(
        "dashboard",
        "validation_report_generation",
        trading_day=report_date,
        page="Validation"
    ):

        output_path = build_report(
            SimpleNamespace(
                date=report_date,
                output=None,
                archive=True,
                update_daily=True,
                finalize=finalize_report
            )
        )
        write_report_state(report_date)
        from app.analytics.candidate_outcomes import write_candidate_outcomes
        get_runtime_scheduler().submit_normal(write_candidate_outcomes, report_date)

        return output_path


def _generate_offline_replay(report_date):

    from tools.replay_today import build_replay_summary, replay_scanner_snapshot
    from app.ui.cache.replay_state_builder import write_replay_state

    with measure_runtime(
        "dashboard",
        "offline_replay_generation",
        trading_day=report_date,
        page="Replay"
    ):

        input_path = daily_path(report_date, "scanner_output_close.csv")
        output_path = daily_path(report_date, "offline_replay.csv")
        replay, _summary = replay_scanner_snapshot(input_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        replay.to_csv(output_path, index=False)
        summary_path = output_path.with_name("offline_replay_summary.csv")
        build_replay_summary(replay).to_csv(
            summary_path,
            index=False
        )
        write_replay_state(report_date)

        return output_path, summary_path


def _render_daily_validation_report_controls():
    """Post-market generation.

    `Post Market: Generate Everything` is the button for the daily routine; the
    other two are strict subsets of it and now sit behind an expander, because
    three buttons where one always does the job is three chances to run the
    wrong one after a session.
    """

    st.sidebar.subheader("Operations")

    default_report_date = datetime.now(
        ZoneInfo("America/New_York")
    ).date().isoformat()
    report_date = st.sidebar.text_input(
        "Trading day",
        value=default_report_date,
        key="daily_validation_report_date"
    )

    if st.sidebar.button(
        "Post Market: Generate Everything",
        key="post_market_generate_everything",
        help="Validation report, replay, post-market review, and freeze the baseline."
    ):

        try:

            output_path = _generate_daily_validation_report(
                report_date,
                finalize_report=True
            )
            _replay_path, summary_path = _generate_offline_replay(report_date)
            from app.regression import freeze_baseline

            baseline_path = freeze_baseline(report_date)

            from app.analytics.post_market_review import write_review

            _review_path, review_summary = write_review(report_date)

            st.session_state["daily_validation_report_path"] = str(output_path)
            st.session_state["offline_replay_summary_path"] = str(summary_path)
            baseline_message = "Baseline frozen." if baseline_path else "Baseline not available yet."
            st.sidebar.success(
                f"Validation report, replay and post-market review generated "
                f"({review_summary['trades']} trades). {baseline_message}"
            )
            st.rerun()

        except Exception as exc:

            st.sidebar.error("Post-market generation failed.")
            st.sidebar.text(str(exc))

    individual = st.sidebar.expander("Run one at a time", expanded=False)

    finalize_report = individual.checkbox(
        "Finalize manifest",
        value=True,
        key="daily_validation_finalize_manifest",
        help="Use after market close. Uncheck for an intraday partial report."
    )

    if individual.button(
        "Validation report",
        key="generate_daily_validation_report"
    ):

        try:

            output_path = _generate_daily_validation_report(
                report_date,
                finalize_report=finalize_report
            )
            st.session_state["daily_validation_report_path"] = str(output_path)
            individual.success("Validation report generated.")

        except Exception as exc:

            individual.error("Report generation failed.")
            individual.text(str(exc))

    if individual.button(
        "Post-market review",
        key="generate_post_market_review"
    ):

        try:

            from app.analytics.post_market_review import write_review

            _path, summary = write_review(report_date)
            individual.success(f"Review generated ({summary['trades']} trades).")

        except Exception as exc:

            individual.error("Review generation failed.")
            individual.text(str(exc))

    if individual.button(
        "Replay",
        key="generate_sidebar_offline_replay"
    ):

        try:

            _output_path, summary_path = _generate_offline_replay(report_date)
            st.session_state["offline_replay_summary_path"] = str(summary_path)
            individual.success("Offline replay generated.")

        except Exception as exc:

            individual.error("Replay generation failed.")
            individual.text(str(exc))

    st.sidebar.caption("Generated files are in Downloads.")


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


# The `scanner_run_status.json` polling helpers that used to live here are gone
# with the browser-triggered scan they served. One of them also deleted the scan
# lock after 10 minutes, while the lock's own reclaim threshold is 15, so it could
# unlink a lock a live scan still held. app.runtime.scan_supervisor.status() is now
# the single source of scan-engine state.


def _auto_refresh_defaults():

    if "auto_refresh_enabled" not in st.session_state:

        st.session_state["auto_refresh_enabled"] = _is_market_hours()

    if "refresh_interval_label" not in st.session_state:

        st.session_state["refresh_interval_label"] = "1 min"



def _auto_paper_controls():
    """What the scanner will actually apply. Read-only; there is no widget.

    This used to render seven sidebar controls that wrote
    `app/state/auto_paper_settings.json`. That only ever worked while one process
    both rendered the sidebar and ran the scans. With `SCAN_ENGINE_OWNER=worker`
    the scanner is a Render Background Worker on a different host with its own
    (empty) disk, so nothing it read could ever be what the sidebar wrote -- the
    controls moved values the scanner never saw, while continuing to display them
    as though they were in force.

    Reading the same env-backed function the scanner reads means the dashboard
    cannot show a limit that is not the one being enforced. Changing any of these
    is a config change in Render and Streamlit, not a click.
    """

    from app.runtime.paper_automation_support import load_auto_paper_controls

    return load_auto_paper_controls()



def _ensure_scan_engine_started():
    """Start the in-process scan engine. Status is reported by the System block.

    `app.runtime.scan_supervisor` owns cadence on a daemon thread, so scans
    continue with no tab open and nothing to click -- but the thread only exists
    inside the Streamlit process, so every render has to make sure it is alive.

    This used to also render a `Scan Engine` panel of four captions. The System
    block at the top of the sidebar now answers the same question in the same
    place as the rest of the health, so the panel was two copies of one fact.

    **Does nothing when `SCAN_ENGINE_OWNER` is not `dashboard`.** That is the
    switch the always-on worker cutover turns. It has to be an environment
    variable rather than a code change because a deploy replaces the container,
    which kills the in-flight scan and drops open positions -- so ownership has
    to be movable during market hours, when pushing is barred.

    Leaving it on while the Render worker runs is the one genuinely dangerous
    configuration: two engines double-open positions, and `scan_lock` is a file
    on local disk, so it cannot serialise anything across two hosts. The System
    block raises that as an error if both are heartbeating.
    """

    from app.runtime.scan_supervisor import ensure_started, status as scan_engine_status

    # Same guard as `_remote_engine_summary`, and for the same reason: a partial
    # deploy must not be able to stop the sidebar rendering. Falling back to the
    # raw variable keeps the switch working even if the helper is missing, and
    # `dashboard` is the right default -- code old enough to lack the module is
    # code from before the worker existed, when this process was the only engine.
    try:

        from app.runtime.scan_engine_heartbeat import scan_engine_owner

        owner = scan_engine_owner()

    except Exception:

        owner = str(os.getenv("SCAN_ENGINE_OWNER", "dashboard")).strip().lower() or "dashboard"

    if owner != "dashboard":

        # Stop, not merely decline to start. The thread outlives the setting that
        # started it: flipping SCAN_ENGINE_OWNER to `worker` skipped
        # `ensure_started` but left an already-running supervisor looping and
        # scanning forever, so the cutover appeared done while this process was
        # still a second scanner. Nothing sets `_stop_event` on its own.
        engine = scan_engine_status()

        if engine.get("thread_alive"):

            from app.runtime.scan_supervisor import stop

            print("[SCAN ENGINE] ownership moved to the worker; stopping local engine.")
            stop()

            return scan_engine_status()

        return engine

    _prime_scanner_environment()

    # No cadence override. The `Full Scanner Cadence` control that supplied one
    # is gone: it fed this function, which returns above when the worker owns
    # scanning, so it had not changed the scanner since the cutover -- while
    # still looking like the place to slow the scanner down during a session.
    #
    # The supervisor's own session-aware schedule is the right default for the
    # case that remains: SCAN_ENGINE_OWNER flipped back to `dashboard` because
    # the worker is down and you want Streamlit scanning for the open.
    engine = scan_engine_status()

    if not engine.get("thread_alive"):

        engine = ensure_started()

    return engine


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
    # Seeded from the engine, not from this widget's default. `session_state` is
    # per browser session, so without this a second device -- a phone opened at
    # work while the laptop is still up -- initialises the selectbox to its first
    # option and that render pushes `set_interval_override(None)` for the whole
    # process. Merely *opening* the page changed the backend's cadence for
    # everyone, with nobody having clicked anything.
    #
    # The sidebar is a view of backend state. It only writes when the operator
    # actually picks something different.
    age_minutes = _scanner_output_age_minutes()

    session_label = (
        "OPEN"
        if market_open
        else "CLOSED"
    )

    st.sidebar.caption(
        f"Market hours: {session_label}"
    )
    # Scanner output age moved to the System block, which already reports the
    # last scan and is where the rest of the health lives.
    st.sidebar.caption(
        "Auto Refresh redraws the page only. Scans are run by the scan engine."
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

    engine = _ensure_scan_engine_started()
    engine_interval_seconds = engine.get("interval_seconds")

    return {
        "enabled": auto_refresh_enabled,
        "interval_minutes": interval_minutes,
        # Reported by whichever engine is actually running, rather than by a
        # control that claimed to set it.
        "scanner_cadence_minutes": (
            int(engine_interval_seconds) // 60 if engine_interval_seconds else 5
        ),
        "age_minutes": age_minutes,
        "refresh_count": refresh_count,
        "engine": engine
    }


def _prime_scanner_environment():
    """Publish Streamlit secrets into the environment before the engine starts.

    The scan engine runs on a background thread, and `st.secrets` is only reliably
    reachable from the script thread, so credentials are resolved here once per
    process. This ran inside the old per-scan trigger; the engine scans without a
    trigger, so it has to happen at dashboard start instead or scans would run
    with no Polygon key.
    """

    if st.session_state.get("scanner_environment_primed"):

        return

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

        st.session_state["scanner_environment_primed"] = True

    except Exception as exc:

        st.sidebar.error(f"Could not load scanner credentials: {exc}")


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

    telegram_entry_result = maybe_send_paper_entry_alert(
        opened_trade,
        scanner_context,
        reason="Manual dashboard paper entry"
    )
    opened_log_row = row_for_trade.copy()
    opened_log_row["Paper Trade Opened"] = True
    opened_log_row["Real Trade Readiness"] = _real_trade_readiness(opened_log_row)
    opened_log_row["Real Entry Checklist"] = _real_entry_checklist(opened_log_row)

    _record_auto_paper_decision(
        row_for_trade.get("Symbol"),
        "TELEGRAM_ENTRY_ALERT",
        telegram_entry_result.get("reason"),
        opened_log_row,
        trade=opened_trade
    )
    _record_auto_paper_decision(
        row_for_trade.get("Symbol"),
        "OPENED",
        "Manual dashboard paper entry",
        opened_log_row,
        trade=opened_trade
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

    # Shared with the scan path so the two cannot enforce different books.
    from app.runtime.paper_automation_support import (
        max_active_paper_trades,
        max_active_per_direction,
    )

    if len(open_trades) >= max_active_paper_trades():

        return False, "MAX_ACTIVE_PAPER_TRADES_REACHED"

    same_direction = [
        trade for trade in open_trades
        if trade.get("direction") == direction
    ]

    if len(same_direction) >= max_active_per_direction():

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


def _live_exit_reason(scanner_row, trade, controls):
    """Read the exit engine's own verdict for an open trade. Display only.

    The dashboard does not evaluate exits. Market exit decisions come from
    app/exit/exit_engine.py via the scanner's persisted `Live Exit Signal` /
    `Live Exit Reason` columns; the end-of-day case is holding policy.
    """

    from app.runtime.paper_automation_support import eod_force_close_reason

    if scanner_row is not None and _boolish(scanner_row.get("Live Exit Signal")):

        return str(
            scanner_row.get("Live Exit Reason")
            or "Exit engine signalled an exit"
        )

    return eod_force_close_reason(trade, controls)


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
        reason = _live_exit_reason(
            scanner_row,
            trade,
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

        color = "background-color: rgba(34, 197, 94, 0.18); color: inherit"

    elif setup_pct >= 60:

        color = "background-color: rgba(245, 158, 11, 0.18); color: inherit"

    else:

        color = "background-color: rgba(239, 68, 68, 0.18); color: inherit"

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
            background: var(--secondary-background-color);
            color: var(--text-color);
            min-height: 52px;
        }

        .compact-label {
            font-size: 0.70rem;
            font-weight: 600;
            color: inherit;
            opacity: 0.72;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .compact-value {
            font-size: 0.92rem;
            line-height: 1.25;
            font-weight: 700;
            margin-top: 0.18rem;
            color: inherit;
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

        .metric-card {
            border: 1px solid rgba(128, 128, 128, 0.25);
            border-radius: 10px;
            padding: 14px 16px;
            background: var(--secondary-background-color);
            color: var(--text-color);
            min-height: 84px;
            margin-bottom: 0.7rem;
        }

        .metric-label {
            font-size: 13px;
            color: inherit;
            opacity: 0.72;
            font-weight: 600;
            margin-bottom: 8px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .metric-value {
            font-size: 28px;
            font-weight: 700;
            color: inherit;
            line-height: 1.1;
            overflow-wrap: anywhere;
        }

        .download-status {
            border: 1px solid rgba(100, 116, 139, 0.35);
            border-radius: 8px;
            padding: 0.58rem 0.7rem;
            margin: 0.35rem 0 0.25rem 0;
            background: var(--secondary-background-color);
            color: var(--text-color);
        }

        .download-status-ready {
            border-left: 4px solid #16a34a;
        }

        .download-status-missing {
            border-left: 4px solid #94a3b8;
            opacity: 0.86;
        }

        .download-status-error {
            border-left: 4px solid #dc2626;
        }

        .download-status-label {
            font-size: 0.86rem;
            font-weight: 700;
            line-height: 1.25;
            color: var(--text-color);
        }

        .download-status-note {
            margin-top: 0.18rem;
            font-size: 0.76rem;
            font-weight: 650;
            color: #475569;
        }

        /* Operator console. Reuses the compact-card tones so the Trading page
           reads as the same system, and adds the pieces a console needs that a
           KPI grid does not: a page banner, a session pill, and position cards
           with an R gauge. */

        .op-bar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.6rem;
            border: 1px solid rgba(148, 163, 184, 0.25);
            border-radius: 10px;
            padding: 0.5rem 0.8rem;
            margin-bottom: 0.7rem;
            background: var(--secondary-background-color);
        }

        .op-bar-title {
            font-size: 0.95rem;
            font-weight: 800;
            letter-spacing: 0.02em;
        }

        .op-pill {
            font-size: 0.7rem;
            font-weight: 800;
            letter-spacing: 0.06em;
            padding: 0.16rem 0.55rem;
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.4);
            white-space: nowrap;
        }

        .op-pill-live { border-color: #22c55e; color: #16a34a; }
        .op-pill-post { border-color: #64748b; opacity: 0.9; }
        .op-pill-bad { border-color: #ef4444; color: #dc2626; }

        .pos-card {
            border: 1px solid rgba(148, 163, 184, 0.25);
            border-left-width: 4px;
            border-radius: 10px;
            padding: 0.55rem 0.7rem;
            margin-bottom: 0.5rem;
            background: var(--secondary-background-color);
        }

        .pos-head {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 0.5rem;
        }

        .pos-symbol { font-size: 1.02rem; font-weight: 800; }
        .pos-sub { font-size: 0.72rem; opacity: 0.75; font-weight: 650; }
        .pos-r { font-size: 1.02rem; font-weight: 800; }

        .r-track {
            position: relative;
            height: 7px;
            border-radius: 999px;
            background: rgba(148, 163, 184, 0.22);
            margin: 0.4rem 0 0.35rem 0;
            overflow: hidden;
        }

        .r-fill { position: absolute; top: 0; bottom: 0; border-radius: 999px; }
        .r-zero {
            position: absolute;
            top: -2px;
            bottom: -2px;
            width: 1px;
            background: rgba(148, 163, 184, 0.75);
        }

        .pos-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(78px, 1fr));
            gap: 0.3rem 0.55rem;
            margin-top: 0.15rem;
        }

        .pos-field { font-size: 0.72rem; opacity: 0.72; font-weight: 650; }
        .pos-figure { font-size: 0.84rem; font-weight: 750; }

        .stDataFrame th,
        .stDataFrame table thead th,
        .stDataFrame table th,
        .stDataFrame table td,
        .stDataFrame div[role="columnheader"],
        .stDataFrame div[role="columnheader"] span,
        .stTable th,
        .stTable table thead th,
        .stTable table th,
        .stTable table td,
        .stTable div[role="columnheader"],
        .stTable div[role="columnheader"] span,
        .dataframe th,
        .dataframe table thead th,
        .dataframe table th,
        .dataframe table td {
            color: var(--text-color) !important;
            opacity: 1 !important;
            font-weight: 700 !important;
            background-color: var(--secondary-background-color) !important;
            border-color: rgba(148, 163, 184, 0.25) !important;
        }

        .stDataFrame td,
        .stDataFrame table tbody td,
        .stTable td,
        .stTable table tbody td,
        .dataframe td,
        .dataframe table tbody td,
        .stDataFrame div[role="gridcell"],
        .stDataFrame div[role="gridcell"] span,
        .stTable div[role="gridcell"],
        .stTable div[role="gridcell"] span {
            color: var(--text-color) !important;
            opacity: 1 !important;
        }

        div[data-testid="stSidebar"],
        div[data-testid="stSidebar"] {
            color: var(--text-color) !important;
        }

        div[data-testid="stSidebar"] h2,
        div[data-testid="stSidebar"] h3,
        div[data-testid="stSidebar"] label,
        div[data-testid="stSidebar"] span,
        div[data-testid="stSidebar"] button,
        div[data-testid="stSidebar"] p {
            color: var(--text-color) !important;
            opacity: 1 !important;
        }

        div[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
        div[data-testid="stSidebar"] [data-testid="stCaptionContainer"] *,
        div[data-testid="stAppViewContainer"] [data-testid="stCaptionContainer"],
        div[data-testid="stAppViewContainer"] [data-testid="stCaptionContainer"] *,
        div[data-testid="stSidebar"] small,
        div[data-testid="stSidebar"] .caption,
        div[data-testid="stAppViewContainer"] small,
        div[data-testid="stAppViewContainer"] .caption {
            color: #475569 !important;
            opacity: 1 !important;
            font-weight: 600 !important;
        }

        @media (prefers-color-scheme: dark) {
            div[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
            div[data-testid="stSidebar"] [data-testid="stCaptionContainer"] *,
            div[data-testid="stAppViewContainer"] [data-testid="stCaptionContainer"],
            div[data-testid="stAppViewContainer"] [data-testid="stCaptionContainer"] *,
            div[data-testid="stSidebar"] small,
            div[data-testid="stSidebar"] .caption,
            div[data-testid="stAppViewContainer"] small,
            div[data-testid="stAppViewContainer"] .caption,
            .download-status-note {
                color: #cbd5e1 !important;
                opacity: 1 !important;
            }
        }

        div[data-testid="stToggle"] label,
        div[data-testid="stToggle"] p,
        div[data-testid="stCheckbox"] label,
        div[data-testid="stCheckbox"] p {
            color: var(--text-color) !important;
            opacity: 1 !important;
            font-weight: 650 !important;
        }

        div[data-testid="stToggle"] [role="switch"],
        div[data-testid="stCheckbox"] [role="checkbox"] {
            border: 2px solid #64748b !important;
            box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.10) !important;
        }

        div[data-testid="stToggle"] [role="switch"][aria-checked="true"],
        div[data-testid="stCheckbox"] [role="checkbox"][aria-checked="true"] {
            background-color: #16a34a !important;
            border-color: #15803d !important;
        }

        div[data-testid="stToggle"] [role="switch"][aria-checked="false"],
        div[data-testid="stCheckbox"] [role="checkbox"][aria-checked="false"] {
            background-color: #e2e8f0 !important;
            border-color: #64748b !important;
        }

        div[data-testid="stButton"] button,
        div[data-testid="stDownloadButton"] button {
            border: 1px solid rgba(71, 85, 105, 0.45) !important;
            color: var(--text-color) !important;
            background: var(--secondary-background-color) !important;
            font-weight: 650 !important;
        }

        div[data-testid="stButton"] button:hover,
        div[data-testid="stDownloadButton"] button:hover {
            border-color: #2563eb !important;
            color: #1d4ed8 !important;
        }

        div[data-testid="stButton"] button:disabled,
        div[data-testid="stDownloadButton"] button:disabled {
            color: #64748b !important;
            background: rgba(148, 163, 184, 0.16) !important;
            border-color: rgba(100, 116, 139, 0.35) !important;
            opacity: 1 !important;
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


def _metadata_status(age_minutes, refresh_minutes):

    if age_minutes is None:

        return "OUTDATED"

    current_limit = refresh_minutes + 0.5
    stale_limit = refresh_minutes * 2

    if age_minutes <= current_limit:

        return "CURRENT"

    if age_minutes <= stale_limit:

        return "STALE"

    return "OUTDATED"


def _status_label(status):

    status = str(status or "UNKNOWN").upper()

    if status == "CURRENT":

        return "CURRENT OK"

    if status == "STALE":

        return "STALE"

    if status == "READY":

        return "READY OK"

    if status == "LIVE":

        return "LIVE OK"

    return status


def _scan_metadata(df, refresh_state=None):

    trading_day = _current_trading_day()
    manifest = {}

    try:

        from app.storage.session_manager import get_or_create_session_manifest

        manifest = get_or_create_session_manifest(trading_day)

    except Exception:

        manifest = {}

    refresh_minutes = 5

    if refresh_state:

        refresh_minutes = int(refresh_state.get("scanner_cadence_minutes") or refresh_state.get("interval_minutes") or 5)

    age_minutes = _scanner_output_age_minutes()
    status = _metadata_status(age_minutes, refresh_minutes)
    latest_run = _latest_scanner_run(df) if df is not None and not df.empty else manifest.get("last_scan_at")
    scan_id = manifest.get("last_scan_id")

    if df is not None and not df.empty:

        for column in ["Scan ID", "Data Version", "scan_id"]:

            if column in df.columns and not df[column].dropna().empty:

                scan_id = df[column].dropna().iloc[0]
                break

    if not scan_id and latest_run:

        try:

            scan_id = get_scan_id(trading_day)

        except Exception:

            scan_id = "UNKNOWN"

    return {
        "trading_day": trading_day,
        "scan_id": scan_id or "UNKNOWN",
        "scanner_started": manifest.get("last_scan_at") or latest_run or "UNKNOWN",
        "scanner_finished": latest_run or manifest.get("last_scan_at") or "UNKNOWN",
        "last_refreshed": datetime.now(ZoneInfo("America/New_York")).strftime("%m/%d/%Y %H:%M:%S ET"),
        "scan_age": f"{age_minutes} min" if age_minutes is not None else "missing",
        "symbols": len(df) if df is not None else 0,
        "status": status,
        "refresh_minutes": refresh_minutes,
    }


def _render_metadata_card(title, rows):

    st.markdown(f"**{title}**")
    cols = st.columns(4)

    for index, (label, value) in enumerate(rows):

        with cols[index % 4]:

            kpi_card(label, str(value))


def _render_replay_page(df=None, refresh_state=None):

    trading_day = _current_trading_day()
    cached = _load_cached_state("replay_state.json", profile="replay")

    if cached:

        _render_cached_replay_state(cached, trading_day)
        return

    st.subheader("Replay")
    input_path = daily_path(trading_day, "scanner_output_close.csv")
    output_path = daily_path(trading_day, "offline_replay.csv")
    summary_path = output_path.with_name("offline_replay_summary.csv")
    metadata = _scan_metadata(df, refresh_state=refresh_state)
    replay_generated = (
        datetime.fromtimestamp(summary_path.stat().st_mtime, ZoneInfo("America/New_York")).strftime("%m/%d/%Y %H:%M:%S ET")
        if summary_path.exists()
        else "Not generated"
    )
    scanner_rows_for_metadata = _file_row_count(input_path, pd.read_csv)
    replay_rows_for_metadata = _file_row_count(output_path, pd.read_csv)
    coverage_for_metadata = (
        f"{replay_rows_for_metadata} / {scanner_rows_for_metadata} ({round((replay_rows_for_metadata / scanner_rows_for_metadata) * 100, 1)}%)"
        if scanner_rows_for_metadata
        else "pending"
    )

    _render_metadata_card(
        "Replay Session",
        [
            ("Replay Status", "READY OK" if summary_path.exists() else "MISSING"),
            ("Replay Scan ID", metadata["scan_id"]),
            ("Replay Generated", replay_generated),
            ("Based On Scan", metadata["scanner_finished"]),
            ("Replay Coverage", coverage_for_metadata),
            ("Replay Version", "v1"),
            ("Data Version", metadata["scan_id"]),
            ("Status", _status_label(metadata["status"])),
        ]
    )
    st.caption(f"Input: {input_path}")

    if st.button("Generate Replay", key="generate_offline_replay"):

        try:

            _generate_offline_replay(trading_day)
            st.success("Offline replay generated.")

        except Exception as exc:

            st.error(f"Offline replay failed: {exc}")

    st.markdown("**Today's Replay Analysis**")
    replay_df = pd.DataFrame()
    summary_df = pd.DataFrame()

    if output_path.exists() and output_path.stat().st_size > 0:

        try:

            replay_df = pd.read_csv(output_path)

        except Exception:

            replay_df = pd.DataFrame()

    if summary_path.exists() and summary_path.stat().st_size > 0:

        try:

            summary_df = pd.read_csv(summary_path)

        except Exception:

            summary_df = pd.DataFrame()

    if replay_df.empty and summary_df.empty:

        st.info("Generate replay after a scanner run to see coverage, blockers, and ticker-level replay results.")

    else:

        scanner_rows = _file_row_count(input_path, pd.read_csv)
        replay_rows = len(replay_df) if not replay_df.empty else len(summary_df)
        missing_indicators = 0

        if not replay_df.empty and "FAILED_ENTRY_CONDITIONS" in replay_df.columns:

            missing_indicators = int(
                replay_df["FAILED_ENTRY_CONDITIONS"]
                .astype(str)
                .str.contains("Missing replay indicators", na=False)
                .sum()
            )

        coverage_pct = round((replay_rows / scanner_rows) * 100, 2) if scanner_rows else 0
        cards = [
            ("Symbols Replayed", replay_rows),
            ("Coverage", f"{coverage_pct}%"),
            ("Missing Indicators", missing_indicators),
            ("Partial Replay", missing_indicators),
        ]
        _render_compact_card_grid(cards)

        blocker_source = summary_df if not summary_df.empty else replay_df

        if "Gate Failure Stage" in blocker_source.columns:

            blockers = (
                blocker_source["Gate Failure Stage"]
                .fillna("Unknown")
                .astype(str)
                .value_counts(normalize=True)
                .mul(100)
                .round(1)
                .reset_index()
            )
            blockers.columns = ["Blocker", "Share %"]
            st.markdown("**Today's Biggest Blockers**")
            st.dataframe(
                _display_safe_dataframe(blockers),
                width="stretch",
                hide_index=True
            )

        if not summary_df.empty:

            st.markdown("**Replay Summary**")
            preferred_columns = [
                "Symbol",
                "Closest Setup",
                "Readiness",
                "First Failed Rule",
                "Recommendation",
                "Trade Block Details",
                "Final Decision",
                "Gate Failure Stage",
            ]
            display_summary = summary_df[
                [column for column in preferred_columns if column in summary_df.columns]
            ].copy()
            st.dataframe(
                _display_safe_dataframe(display_summary),
                width="stretch",
                hide_index=True
            )

    _render_file_download_button(
        "Download offline_replay.csv",
        output_path,
        file_name="offline_replay.csv",
        mime="text/csv",
        key="download_offline_replay",
        container=st
    )
    _render_file_download_button(
        "Download offline_replay_summary.csv",
        summary_path,
        file_name="offline_replay_summary.csv",
        mime="text/csv",
        key="download_offline_replay_summary",
        container=st
    )


def _render_reports_page(df):

    cached = _load_cached_state("report_state.json", profile="reports")

    if cached:

        _render_cached_report_state(cached)
        return



def _load_runtime_json_state():

    return _load_cached_state("runtime_state.json", profile="developer")


def _load_runtime_health_state():

    return _load_cached_state("runtime_health.json", profile="developer")


def _load_runtime_performance_df():

    return _read_csv_safe(ROOT_DIR / "data" / "runtime_performance.csv")


def _load_runtime_metrics_df():

    return _read_csv_safe(ROOT_DIR / "data" / "runtime_metrics.csv")


def _load_runtime_performance_summary():

    return _load_cached_state("runtime_performance_summary.json", profile="developer")


def _render_runtime_performance_panel():

    st.subheader("Runtime Performance")
    runtime_state = _load_runtime_json_state()
    runtime_health = _load_runtime_health_state()

    if runtime_health:

        st.markdown("**Runtime Health**")
        _render_compact_card_grid([
            ("Healthy", runtime_health.get("healthy")),
            ("Score", runtime_health.get("score")),
            ("Warnings", len(runtime_health.get("warnings") or [])),
            ("Errors", len(runtime_health.get("errors") or [])),
        ])

        for warning in runtime_health.get("warnings") or []:

            st.warning(warning)

        for error in runtime_health.get("errors") or []:

            st.error(error)

    if runtime_state:

        cards = [
            ("Critical", runtime_state.get("critical_jobs", 0)),
            ("High", runtime_state.get("high_jobs", 0)),
            ("Normal", runtime_state.get("normal_jobs", 0)),
            ("Low", runtime_state.get("low_jobs", 0)),
            ("Running", runtime_state.get("running_jobs", 0)),
            ("Telegram Queue", runtime_state.get("telegram_queue", 0)),
        ]
        _render_compact_card_grid(cards)
    else:

        st.info("runtime_state.json has not been written yet.")

    performance = _load_runtime_performance_df()
    performance_summary = _load_runtime_performance_summary()

    if performance_summary.get("average_seconds_by_stage"):

        st.markdown("**Slowest Runtime Stages**")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(performance_summary["average_seconds_by_stage"])),
            width="stretch",
            hide_index=True
        )

    if not performance.empty:

        st.markdown("**Recent Runtime Timings**")
        columns = [
            column for column in [
                "observed_at_utc",
                "category",
                "stage",
                "page",
                "seconds",
                "scan_id"
            ]
            if column in performance.columns
        ]
        st.dataframe(
            _display_safe_dataframe(performance[columns].tail(25).iloc[::-1]),
            width="stretch",
            hide_index=True
        )
    else:

        st.caption("No runtime_performance.csv rows yet.")

    metrics = _load_runtime_metrics_df()

    if performance_summary.get("average_runtime_by_job"):

        st.markdown("**Slowest Runtime Jobs**")
        st.dataframe(
            _display_safe_dataframe(pd.DataFrame(performance_summary["average_runtime_by_job"])),
            width="stretch",
            hide_index=True
        )

    if not metrics.empty:

        st.markdown("**Recent Runtime Jobs**")
        columns = [
            column for column in [
                "observed_at_utc",
                "job_name",
                "priority",
                "queue_wait",
                "queue_runtime",
                "total_runtime",
                "status",
                "scan_id"
            ]
            if column in metrics.columns
        ]
        st.dataframe(
            _display_safe_dataframe(metrics[columns].tail(25).iloc[::-1]),
            width="stretch",
            hide_index=True
        )
    else:

        st.caption("No runtime_metrics.csv rows yet.")


def _render_lazy_developer_section(title, key, render_fn, expanded=False):

    with st.expander(title, expanded=expanded):

        if not st.toggle(
            f"Load {title}",
            key=f"load_developer_{key}",
            value=False
        ):

            st.caption("Enable this section to load its diagnostics.")
            return

        render_fn()


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
            event_trade = {
                **trade,
                **{
                    key: value
                    for key, value in row.to_dict().items()
                    if _has_value(value)
                }
            }
            r_multiple = _safe_float(row.get("r_multiple"), None)
            trading_day = str(row.get("trading_day") or "").strip()

            if not trading_day:

                event_time = str(row.get("event_time") or "")
                trading_day = event_time[:10] if len(event_time) >= 10 else None

            if not trade_key:

                trade_key = "|".join(
                    str(part or "")
                    for part in [
                        row.get("symbol"),
                        row.get("option_ticker"),
                        row.get("event_time_et") or row.get("event_time")
                    ]
                    if str(part or "").strip()
                )

            closed_rows.append({
                "trade_key": trade_key,
                "trading_day": trading_day,
                "closed_at": row.get("event_time_et") or row.get("event_time"),
                "symbol": row.get("symbol") or event_trade.get("symbol"),
                "direction": row.get("direction") or event_trade.get("direction"),
                "option_ticker": row.get("option_ticker") or event_trade.get("option_ticker"),
                "r_multiple": r_multiple,
                "estimated_pnl_dollars": _estimated_trade_pnl_dollars(event_trade, r_multiple),
                "exit_reason": row.get("exit_reason") or event_trade.get("exit_reason"),
                "paper_affordability_override": _trade_context_value(
                    event_trade,
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
    values = [
        ("Closed", summary["closed_trades"]),
        ("Win %", _format_rate(summary["win_rate"])),
        ("Loss %", _format_rate(summary["loss_rate"])),
        ("Total R", _format_r(summary["total_r"])),
        ("Est. $ P/L", _format_dollars(summary["estimated_pnl_dollars"])),
        ("Avg R", _format_r(summary["avg_r"])),
    ]

    for col, (metric_label, metric_value) in zip(cols, values):

        with col:

            kpi_card(metric_label, str(metric_value))


def _format_efficiency_pct(value):

    try:

        if value is None or pd.isna(value):

            return "-"

        return f"{float(value):.1f}%"

    except Exception:

        return "-"


def _format_efficiency_number(value):

    try:

        if value is None or pd.isna(value):

            return "-"

        return f"{float(value):.2f}"

    except Exception:

        return "-"


def _trend_capture_numeric(df):

    if df is None or df.empty:

        return pd.DataFrame()

    output = df.copy()

    for column in [
        "Trend Capture %",
        "Left On Table",
        "Available Move",
        "Captured Move",
        "Maximum Favorable Excursion",
        "Maximum Adverse Excursion",
        "Trend Health Score",
        "Risk Reward",
        "Profit +1 Bar",
        "Profit +2 Bars",
        "Profit +3 Bars",
        "Profit +5 Bars",
        "Best Profit",
        "Trade Efficiency Score"
    ]:

        if column in output.columns:

            output[column] = pd.to_numeric(
                output[column],
                errors="coerce"
            )

    return output


def _trend_capture_summary_table(df, group_column, include_left=True):

    if df.empty or group_column not in df.columns:

        return pd.DataFrame()

    aggregations = {
        "Trades": ("Symbol", "count"),
        "AvgCapture": ("Trend Capture %", "mean")
    }

    if include_left and "Left On Table" in df.columns:

        aggregations["AvgLeft"] = ("Left On Table", "mean")

    summary = (
        df.groupby(group_column, dropna=True)
        .agg(**aggregations)
        .reset_index()
    )

    for column in ["AvgCapture", "AvgLeft"]:

        if column in summary.columns:

            summary[column] = summary[column].round(2)

    return summary.sort_values(
        by="AvgCapture",
        ascending=False,
        na_position="last"
    )


def _exit_trigger_frequency_table(trend_capture):

    rows = []

    for label, column in [
        ("EMA", "Triggered EMA"),
        ("VWAP", "Triggered VWAP"),
        ("MACD", "Triggered MACD"),
        ("STOP", "Triggered Stop"),
        ("TARGET", "Triggered Target"),
        ("TIME", "Triggered Time Exit"),
        ("NEAR_CLOSE", "Triggered Near Close"),
    ]:

        if column not in trend_capture.columns:

            continue

        mask = trend_capture[column].astype(str).str.lower().isin(["true", "1", "yes"])
        subset = trend_capture[mask]

        if subset.empty:

            continue

        avg_capture = None

        if "Trend Capture %" in subset.columns:

            avg_capture = round(float(subset["Trend Capture %"].mean()), 2)

        rows.append({
            "Trigger": label,
            "Count": int(len(subset)),
            "Avg Capture": avg_capture
        })

    return pd.DataFrame(rows)


def _format_trend_capture_table(df):

    if df is None or df.empty:

        return pd.DataFrame()

    output = df.copy()

    for column in ["Trend Capture %", "AvgCapture", "Avg Capture"]:

        if column in output.columns:

            output[column] = output[column].map(
                lambda value: "-"
                if pd.isna(value)
                else f"{float(value):.1f}%"
            )

    for column in ["Left On Table", "AvgLeft"]:

        if column in output.columns:

            output[column] = output[column].map(
                lambda value: "-"
                if pd.isna(value)
                else f"{float(value):.2f}"
            )

    return output


def _average_percent_left_on_table(trend_capture):

    if trend_capture.empty:

        return None

    if "Available Move" not in trend_capture.columns or "Left On Table" not in trend_capture.columns:

        return None

    available = trend_capture["Available Move"]
    left = trend_capture["Left On Table"]
    valid = available > 0

    if not valid.any():

        return None

    return round(float((left[valid] / available[valid] * 100).mean()), 2)


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


def main():

    # layout="wide" was already here, so the header was never boxed into ~730px.
    # initial_sidebar_state is deliberately left alone: the sidebar carries
    # navigation, the scan engine controls and the system block, so collapsing it
    # by default would hide the controls an operator reaches for first.
    favicon = ROOT_DIR / "assets" / "favicon-32.png"

    st.set_page_config(
        page_title="Dravya Edge",
        page_icon=str(favicon) if favicon.exists() else None,
        layout="wide"
    )

    _inject_compact_dashboard_css()

    render_app_header()
    st.caption("Trading workstation. Developer diagnostics stay collapsed unless needed.")

    # Navigation is claimed first so it renders at the top of the sidebar, but it
    # is filled in last: the controls below it start the scan engine and return
    # state the page routing needs. Previously navigation sat fourth, under three
    # blocks of controls, which is the wrong order for the thing an operator
    # reaches for most.
    navigation = st.sidebar.container()
    system = st.sidebar.container()

    refresh_state = _render_auto_refresh_controls()
    auto_paper_controls = _auto_paper_controls()
    _render_daily_validation_report_controls()
    _render_download_exports()

    st.session_state["dashboard_page"] = _migrate_dashboard_page(
        st.session_state.get("dashboard_page")
    )
    page = navigation.radio(
        "Navigation",
        options=DASHBOARD_PAGES,
        key="dashboard_page",
    )
    _render_system_status(system)

    # No scan trigger here. Scanning is owned by app.runtime.scan_supervisor,
    # started from the sidebar's Scan Engine panel, so cadence does not depend on
    # a rerun, a page, or an operator remembering to click anything.

    if TRADING_DASHBOARD_STATE_ONLY and page == "Trading":

        cached_state = _load_cached_state("dashboard_state.json", profile="trading")

        if cached_state:

            from app.ui.pages.trading import render_from_state

            render_from_state(
                cached_state,
                refresh_state
            )
            st.caption("Trading page rendered from dashboard_state.json. Auto-refresh controls are in the sidebar.")
            return

    if page == "Validation" and _load_cached_state("validation_state.json", profile="validation"):

        from app.ui.pages.validation import render

        render(pd.DataFrame())
        st.caption("Validation page rendered from validation_state.json. Auto-refresh controls are in the sidebar.")
        return

    if page == "Research":

        from app.ui.pages.research import render

        render(refresh_state=refresh_state, load_frame=_research_frame)
        return

    df = _load_scanner_output()

    if df.empty:

        st.warning("scanner_output.xlsx not found or empty. Run python -m app.main first.")
        return

    # Suggestion lifecycle is advanced by the scanner, not by rendering a page.
    df = _add_paper_trade_opened(df)
    df = _add_real_trade_readiness(df)
    df = _enrich_with_suggestion_lifecycle(df)

    latest_time = df.get("Current ET")
    latest_scanner_run = "N/A"

    if latest_time is not None and len(latest_time) > 0:

        latest_scanner_run = latest_time.iloc[0]
        st.caption(f"Last scanner run: {latest_scanner_run}")

    # Paper trade entries, management, and exits are owned exclusively by the
    # scanner (app/main.py). The dashboard never opens, updates, or closes a
    # paper trade automatically: it read the stale scanner_output.xlsx, applied a
    # second exit rule set that bypassed the exit engine, and only ran on the
    # Trading/Developer pages. Trigger a scan instead; the scanner decides.

    dashboard_state = _load_dashboard_state(df)

    with measure_runtime(
        "dashboard",
        "page_render",
        trading_day=_current_trading_day(),
        page=page
    ):

        if page == "Trading":

            from app.ui.pages.trading import render

            render(
                dashboard_state,
                df,
                refresh_state
            )

        elif page == "Validation":

            from app.ui.pages.validation import render

            render(df)

        else:

            from app.ui.pages.developer import render

            render(
                df,
                auto_paper_controls,
                refresh_state=refresh_state
            )

    st.caption("Auto-refresh controls are in the sidebar. Market-hours default is ON at 5 minutes; after-hours default is OFF.")


if __name__ == "__main__":

    main()
