from __future__ import annotations

from datetime import datetime, time, timezone
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
from app.gates.setup_quality import MIN_SETUP_BASE
from app.risk.stop_viability import evaluate_stop_viability
from app.strategies.setup_registry import KNOWN_SETUPS
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
AUTO_PAPER_TOP_CANDIDATES = [
    "BULLISH_TOP_1",
    "BEARISH_TOP_1",
    "BULLISH_TOP_2",
    "BEARISH_TOP_2",
    "BULLISH_TOP_3",
    "BEARISH_TOP_3",
]
INDEX_REVIEW_VALIDATION_SYMBOLS = {"SPY", "QQQ"}
# Derived from the setup registry. This listed two setups that cannot be emitted,
# and dashboard.py carried a *different* copy of the same constant allowing only
# longs -- so the dashboard called a SPY BREAKDOWN_SHORT ineligible while this
# path would open it. One definition, both sides.
REVIEW_VALIDATION_ENTRY_TYPES = KNOWN_SETUPS
AUTO_PAPER_ENTRY_START = time(9, 45)
AUTO_PAPER_ENTRY_END = time(15, 30)
AUTO_PAPER_EOD_CLOSE = time(15, 55)
# Raised 3 -> 5 on 2026-07-31 so it stops being the binding constraint. At 3 it
# was the single largest blocker of the first live session -- 79 events across 11
# symbols, ahead of RR -- and it was rejecting candidates that were not obviously
# worse than the ones taken: AMZN at RR 2.88 and PLTR at setup 81 never got a
# look, while the three trades that ran went off at RR 2.4, 2.75 and 2.26 for
# -0.47R.
#
# 5 matches MAX_DAILY_ENTRIES, which hands the throttling job to the limits that
# exist to manage risk -- daily entries, concurrent positions, per-direction
# exposure, symbol cooldown -- rather than to a ranking cutoff that was never
# chosen for that purpose. Note a candidate already passes when it is a named
# TOP_1/2/3 in its direction, so this only admits ranks 4 and 5.
AUTO_PAPER_MAX_CANDIDATE_RANK = 5
DEFAULT_AUTO_PAPER_MIN_RR = 1.8
DEFAULT_AUTO_PAPER_MIN_OPTION_QUALITY = 65.0
DEFAULT_AUTO_PAPER_MAX_SPREAD_PCT = 6.0
AUTO_PAPER_REQUIRED_COLUMNS = [
    "Symbol", "Setup Valid", "Candidate Direction", "Candidate Entry Price",
    "Candidate Stop Price", "Candidate Target Price", "Candidate RR", "Entry",
    "Action Status", "Next Condition", "Live Chart Checklist",
]


def max_active_paper_trades():
    """Concurrent open paper positions allowed."""

    return env_int("MAX_ACTIVE_PAPER_TRADES", 3)


def max_active_per_direction():
    """Concurrent open positions allowed in one direction.

    Kept separate from the total so directional concentration can be limited
    without capping the book: four positions split 2 CALL / 2 PUT is a different
    risk to four CALLs, and only the total was ever intended to be a hard cap.
    """

    return env_int("MAX_ACTIVE_PER_DIRECTION", 2)


# The two profiles compete for the same book, and only one of them gives its slot
# back at the close.
#
# An INTRADAY position is flattened at 15:55 whatever happens, so its slot is
# borrowed for hours. A MULTIDAY position sets force_eod_exit=False and holds
# until the exit engine closes it, which on 2026-07-30 meant positions still open
# days later. Under one shared MAX_ACTIVE_PAPER_TRADES the slow profile therefore
# crowds out the fast one: two multiday carries against a cap of 4 leave tomorrow
# morning with two slots for a full session of intraday setups, and the operator
# sees "MAX_ACTIVE_PAPER_TRADES_REACHED" without seeing that the trades holding
# the book were opened last week.
#
# Both default to the shared cap, so an unset environment behaves exactly as it
# does now and this becomes a limit only once someone sets it.
def max_active_for_profile(profile):
    """Concurrent open positions allowed within one holding profile."""

    if str(profile or "").upper() == "MULTIDAY":
        return env_int("MAX_ACTIVE_MULTIDAY_TRADES", max_active_paper_trades())

    return env_int("MAX_ACTIVE_INTRADAY_TRADES", max_active_paper_trades())


def max_daily_for_profile(profile, controls=None):
    """New entries allowed per day within one holding profile.

    Separate from the concurrency cap because they answer different questions: how
    much of the book one profile may hold at once, and how much churn it may
    generate in a session.
    """

    fallback = (controls or {}).get("max_daily")

    if fallback is None:
        fallback = load_auto_paper_controls()["max_daily"]

    if str(profile or "").upper() == "MULTIDAY":
        return env_int("MAX_DAILY_MULTIDAY_ENTRIES", fallback)

    return env_int("MAX_DAILY_INTRADAY_ENTRIES", fallback)


def candidate_holding_profile(row):
    """INTRADAY or MULTIDAY for a scanner row, without raising.

    `_add_holding_profiles` already stamps every row, so this is normally just
    reading the column back. It re-derives when the column is missing -- the
    dashboard's manual path and the older archived frames both reach here without
    one -- and falls back to INTRADAY, which is the profile that gives its slot
    back and so is the safe assumption when the answer is unknown.
    """

    from app.state.holding_policy import derive_holding_profile

    try:
        return derive_holding_profile(
            row if isinstance(row, dict) else dict(row)
        ).value

    except Exception:
        return "INTRADAY"


def _active_profile_count(open_trades, profile):
    profile = str(profile or "").upper()

    return len([
        trade for trade in open_trades
        if str(trade.get("holding_profile") or "INTRADAY").upper() == profile
    ])


def load_auto_paper_controls():
    """Auto-paper settings, read from the environment and nowhere else.

    These used to come from `app/state/auto_paper_settings.json`, written by the
    dashboard sidebar. That worked only while the same process rendered the
    sidebar and ran the scans. Once `SCAN_ENGINE_OWNER=worker` moved scanning to
    Render, the sidebar wrote to one host's disk and the scanner read another's --
    which on a Background Worker does not exist at all. Every control was
    therefore inert, while still displaying whatever the operator had last set:
    a UI that reported a limit the scanner was not applying.

    Env vars are the single source of truth because they are the only thing both
    hosts actually share. The file is gone rather than synced through Postgres:
    this codebase has repeatedly been bitten by one limit living in two places
    that drift (the hardcoded 3 vs MAX_DAILY_ENTRIES, the two copies of
    REVIEW_VALIDATION_ENTRY_TYPES, the eleven inert Telegram throttles), and a
    setting that changes trading behaviour is worth a deploy.
    """

    return {
        "auto_paper_enabled": _env_bool("AUTO_PAPER_ENABLED", True),
        # Named the same limit as the affordability config and disagreed with it
        # for as long as both existed; MAX_DAILY_ENTRIES is now the only spelling.
        #
        # Default raised 3 -> 5 on 2026-08-03. This is the only position cap with
        # a track record of costing trades: every one of the five
        # DAILY_AUTO_PAPER_LIMIT_REACHED blocks in the ledger is 07-31, all AMZN,
        # on a day that opened **three** trades -- because production runs the code
        # default and `.env`'s 5 never reaches Render or Streamlit Cloud. Exactly
        # the failure mode `enforce_stop_viability` documents, on the one limit
        # where it was actually binding.
        #
        # 5 is also what the codebase already assumed: AUTO_PAPER_MAX_CANDIDATE_RANK
        # was raised to 5 specifically to "match MAX_DAILY_ENTRIES". The default was
        # the odd one out.
        "max_daily": env_int("MAX_DAILY_ENTRIES", 5),
        "min_setup": _env_float("AUTO_PAPER_MIN_SETUP", MIN_SETUP_BASE),
        "min_rr": _env_float("AUTO_PAPER_MIN_RR", DEFAULT_AUTO_PAPER_MIN_RR),
        "direction": _env_str("AUTO_PAPER_DIRECTION", "Both"),
        # Default ON. This is the standing policy of flattening intraday
        # positions at 15:55 ET; defaulting it off would carry day trades
        # overnight whenever the variable was simply not set.
        "eod_close_enabled": _env_bool("AUTO_PAPER_EOD_CLOSE_ENABLED", True),
        "restore_multiday_positions": _env_bool("RESTORE_MULTIDAY_POSITIONS", True),
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


def _env_str(name, default):

    import os

    value = os.getenv(name)

    return str(value).strip() if value is not None and str(value).strip() else default


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


# The scanner stamps one of these instead of running detect_entry when the
# symbol already has an OPEN paper trade (app/main.py, the `active_trade` branch).
# They are not malformed setups -- they are the absence of a setup search.
HELD_POSITION_ENTRY_TYPES = {
    "ACTIVE_TRADE",
    "PAPER_TRADE",
    "OPEN_TRADE",
}

NO_SETUP_ENTRY_TYPES = {
    "",
    "NAN",
    "NONE",
    "NO_ENTRY",
    "NO_SETUP",
}


def _is_valid_new_entry_type(entry_type):

    return (
        str(entry_type or "").upper()
        not in NO_SETUP_ENTRY_TYPES | HELD_POSITION_ENTRY_TYPES
    )


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
    entry_type = str(row.get("Entry") or "").upper()

    # Reported as INVALID_ENTRY_TYPE until 2026-08-18, which is the same string
    # a malformed row gets. 55 of the 128 actionable decisions in the ledger --
    # 43%, the largest single category -- were this, and every one of them was a
    # re-scan of a symbol the book already held. Reading the funnel with that
    # label in it produced a whole session's worth of wrong conclusions about
    # entries being silently dropped. The decision is unchanged; only the reason
    # is, because the reason is the entire point of the ledger.
    if entry_type in HELD_POSITION_ENTRY_TYPES:
        return "ALREADY_HOLDING_NO_ADDITIONAL_ENTRY"

    if not _is_valid_new_entry_type(entry_type):
        return "NO_ENTRY_SETUP_DETECTED"
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


def _decision_rr(row):
    """Planned reward:risk for the decision ledger, or None when unknown.

    None must stay None rather than becoming 0.0: "no RR was computed" and "the RR
    was zero" mean opposite things when you are asking why a candidate was rejected.
    """

    if row is None:
        return None

    for column in ("Candidate RR", "RR", "Risk Reward"):
        value = row.get(column)

        if value is None or value == "":
            continue

        try:
            number = float(value)
        except (TypeError, ValueError):
            continue

        if number == number:  # not NaN
            return number

    return None


def _persist_auto_paper_decision(entry):
    """Mirror one decision into Postgres. Never allowed to disturb the trade path.

    BestEffortRepository already swallows database errors, but an import failure or
    a mapping bug would propagate into the entry loop and could block a trade over a
    bookkeeping problem. Diagnostics are worth less than the trade they describe.
    """

    try:
        from app.db.auto_paper_decision_repository import AutoPaperDecisionRepository

        AutoPaperDecisionRepository().insert(entry)

    except Exception as exc:
        print(f"[AUTO PAPER DECISION DB WARNING] {entry.get('symbol')}: {exc}")


def _gate_counterfactuals(row, controls):
    """Would this candidate have passed under a different threshold?

    These columns exist in the decision ledger and have never been populated on
    the scan path: they are produced by _add_shadow_diagnostics(), which runs
    inside the dashboard's _load_scanner_output() and therefore only ever sees
    dashboard renders. Every automated decision recorded them blank, which is why
    "what did the RR gate cost us today" has not been answerable.

    Computed against evaluate_entry_gate() with a substituted threshold rather
    than by re-deriving the rule, so the counterfactual cannot drift from the gate
    it is a counterfactual about. Only the threshold is varied -- position caps,
    cooldowns and the daily limit are deliberately left out, because "would a
    lower RR floor have allowed this setup" and "was there room in the book" are
    different questions and answering them in one column makes both useless.
    """

    if row is None:
        return {}

    try:
        gate_row = _paper_gate_row(row)
    except Exception:
        return {}

    def passes(min_rr, min_setup):
        try:
            allowed, _ = evaluate_entry_gate(
                gate_row,
                EntryGateConfig(
                    min_rr=min_rr,
                    min_setup_percent=min_setup,
                    min_option_quality=DEFAULT_AUTO_PAPER_MIN_OPTION_QUALITY,
                    max_spread_pct=DEFAULT_AUTO_PAPER_MAX_SPREAD_PCT,
                ),
                mode="paper",
            )
            return bool(allowed)

        except Exception:
            return None

    current_rr = controls.get("min_rr", DEFAULT_AUTO_PAPER_MIN_RR)
    current_setup = controls.get("min_setup", MIN_SETUP_BASE)
    action_status = str(row.get("Action Status") or "").strip().upper()

    return {
        "would_pass_gate_if_rr_1_7": passes(1.7, current_setup),
        "would_pass_gate_if_setup_65": passes(current_rr, 65.0),
        "would_pass_gate_if_review_allowed": (
            action_status == "REVIEW_TV_CHART" and passes(current_rr, current_setup)
        ),
    }


def write_auto_paper_decision(entry, trading_day):
    """Persist one decision to all three sinks.

    Shared because there are two decision recorders -- this module's, used by the
    scan path, and app.dashboard's, used by manual entries and Telegram entry
    alerts -- and they had drifted to 53 fields against 29. Only this one gained
    the Postgres mirror, so every manually entered trade and every
    TELEGRAM_ENTRY_ALERT would have been missing from auto_paper_decision while
    the table looked complete.

    Callers keep building their own entry dict: the dashboard genuinely has more
    context available (affordability, real-trade readiness, the gate
    counterfactuals) because it reads the enriched frame from
    _load_scanner_output(), which the scan path never runs. Sharing the write is
    what matters; forcing a single field set would only manufacture empty columns.
    """

    try:
        append_daily_auto_paper_decision(entry, get_daily_dir(trading_day))

    except Exception as exc:
        print(f"[AUTO PAPER LOG WARNING] daily CSV write failed: {exc}")

    try:
        update_recent_auto_paper_log(entry, AUTO_PAPER_DECISION_LOG_FILE)

    except Exception as exc:
        print(f"[AUTO PAPER LOG WARNING] recent JSON write failed: {exc}")

    # Files first, DB second: the CSV/JSON stay the live artifacts, so a DB outage
    # degrades to exactly the behaviour that existed before this line.
    #
    # Guarded here as well as inside _persist_auto_paper_decision. This function is
    # called from the entry loop, so "never raises" has to be a property of the
    # writer itself rather than something inherited from what it happens to call.
    try:
        _persist_auto_paper_decision(entry)

    except Exception as exc:
        print(f"[AUTO PAPER LOG WARNING] decision DB mirror failed: {exc}")


def _effective_gate_floors(row, controls):
    """The thresholds the candidate was actually judged against.

    `controls` holds the auto-paper floor (`AUTO_PAPER_MIN_SETUP`, 62). That is not
    the number that rejects anything. The scanner's gate runs first at
    `SCANNER_GATE_MIN_SETUP` (70) and `apply_regime_entry_thresholds` escalates it
    further -- to 83 on weak breadth, 85 in RANGE_BOUND -- and a row that fails
    there is downgraded before auto-paper sees it.

    So the ledger recorded `min_setup_used = 62` beside `SETUP_BELOW_THRESHOLD`
    blocks at setup 62, 70 and 79 on 2026-08-03: three rows that read as
    contradictions, and a column that cannot answer "how far short was it" for any
    of them. `_add_entry_gate_diagnostics` already put the real floor on the row as
    ENTRY_GATE_MIN_SETUP; nothing read it.

    Falls back to the controls when the row carries no gate diagnostics -- SYSTEM
    rows and the dashboard's manual path never run the scanner gate, and for those
    the auto-paper floor genuinely is the one that applied.
    """

    floors = {
        "min_rr_used": (controls or {}).get("min_rr"),
        "min_setup_used": (controls or {}).get("min_setup"),
    }

    if row is None:
        return floors

    for key, column in (
        ("min_rr_used", "ENTRY_GATE_MIN_RR"),
        ("min_setup_used", "ENTRY_GATE_MIN_SETUP"),
    ):
        value = _safe_float(row.get(column), None)

        if value is not None:
            floors[key] = value

    return floors


# What the contract cost to trade, at the moment the decision was taken.
#
# Every one of the 869 decisions on 2026-08-03 recorded `stop_spread_multiple`
# and not one recorded the spread, delta or premium that produced it -- so the 11
# STOP_INSIDE_OPTION_SPREAD blocks that day say a stop covered 0.56x of a spread
# without saying what the spread was. The multiple alone cannot be recalibrated:
# moving the threshold needs the distribution of its inputs.
#
# The dashboard's recorder has always written some of these. This is the scan
# path -- the one that takes almost every decision -- catching up.
_DECISION_OPTION_COLUMNS = {
    "option_quality_score": "Option Quality Score",
    "option_spread_pct": "Option Spread %",
    "option_delta": "Option Delta",
    "option_mid_price": "Option Mid Price",
    "option_bid": "Option Bid",
    "option_ask": "Option Ask",
    "option_ticker": "Option Ticker",
    "option_contract_cost": "Option Contract Cost",
    "option_quote_freshness": "Option Quote Freshness",
    "option_rejection_reason": "Option Rejection Reason",
    # Which contract was refused and against what threshold. The reason alone
    # ("Low open interest") cannot say whether the floor is too high or the
    # selector picked a bad strike.
    "option_rejection_evidence": "Option Rejection Evidence",
    "expiration_bucket": "Expiration Bucket",
    "candidate_entry_price": "Candidate Entry Price",
    "candidate_stop_price": "Candidate Stop Price",
    "candidate_target_price": "Candidate Target Price",
    "candidate_direction": "Candidate Direction",
    "candidate_rank": "Candidate Rank",
    "holding_profile": "Holding Profile",
    # The stop-viability inputs, so the block reason is reproducible from the row.
    "stop_move_pct_of_premium": "STOP_MOVE_PCT_OF_PREMIUM",
    "stop_round_trip_spread_pct": "STOP_ROUND_TRIP_SPREAD_PCT",
    "stop_required_spread_multiple": "STOP_REQUIRED_SPREAD_MULTIPLE",
}


def _decision_option_details(row):
    """Contract economics for the decision ledger, or {} when there is no row."""

    if row is None:
        return {}

    details = {}

    for key, column in _DECISION_OPTION_COLUMNS.items():
        value = row.get(column)

        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue

        text = str(value).strip()

        if text in {"", "nan", "None"}:
            continue

        details[key] = value

    return details


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
        # `scan_timestamp` is ET wall-clock formatted without an offset. Written
        # straight into a `timestamptz` it is read as UTC, so every row in the
        # ledger sat exactly 4.00h early -- all 1,275 of them, uniformly. The
        # naive string stays as it is because the CSV and the recent-decisions
        # JSON display it as ET on purpose; these two carry the unambiguous
        # values, and the repository writes the UTC one to the column.
        "scan_timestamp_et": decision_time.isoformat(),
        "scan_timestamp_utc": decision_time.astimezone(timezone.utc).isoformat(),
        **classify_decision_time(decision_time),
        "gate_mode": "auto_paper",
        # The floor that actually applied, not the auto-paper control that is
        # usually below it. See _effective_gate_floors.
        **_effective_gate_floors(row, controls),
        # What the auto-paper control was set to, kept alongside so raising
        # AUTO_PAPER_MIN_SETUP can still be seen to have had no effect while the
        # scanner floor sits above it.
        "auto_paper_min_rr": controls.get("min_rr"),
        "auto_paper_min_setup": controls.get("min_setup"),
        "symbol": symbol,
        "decision": decision,
        "reason": reason,
        "trade_key": trade.get("trade_key") if trade else None,
        "top_candidate": row.get("Top Candidate") if row is not None else None,
        "setup_percent": row.get("Setup %") if row is not None else None,
        # The scanner column is "Candidate RR"; a bare "RR" key does not exist on
        # scanner rows, so this recorded None for all 96 decisions on 2026-07-30 and
        # the ledger's rr column has never been populated. Same fallback order as
        # persistence._gate_decision_params, which always had it right.
        "rr": _decision_rr(row),
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
        # Why the stop was or was not compatible with the contract's spread.
        # stop_viability_would_block is the observe-only signal: it counts what the
        # gate would have cost before it is allowed to cost anything.
        "stop_viability": row.get("STOP_VIABILITY") if row is not None else None,
        "stop_spread_multiple": row.get("STOP_SPREAD_MULTIPLE") if row is not None else None,
        "stop_viability_would_block": row.get("STOP_VIABILITY_WOULD_BLOCK") if row is not None else None,
        # Implied vs realised volatility, and why the event blocker fired.
        "iv_rv_ratio": row.get("IV_RV_RATIO") if row is not None else None,
        "iv_richness": row.get("IV_RICHNESS") if row is not None else None,
        "iv_richness_would_block": row.get("IV_RICHNESS_WOULD_BLOCK") if row is not None else None,
        "event_blocked": row.get("Event Blocked") if row is not None else None,
        "event_label": row.get("Event Label") if row is not None else None,
        # The regime inputs that raise the entry bar, recorded beside the outcome
        # they are supposed to improve.
        #
        # `apply_regime_entry_thresholds` reads all four of these and escalates
        # min_setup, min_rr and max_spread on them -- a RANGE_BOUND reading or
        # weak breadth pushes min_rr to 2.0, a VIX spike to 2.2. None of them was
        # written here, and `paper_trades.payload` carries no regime at all, so
        # asking "do trades taken under this regime do better" returned UNKNOWN
        # for every row in the archive. The thresholds have been costing entries
        # since they were written and there has never been a way to price them.
        #
        # Recording only. Nothing reads these to make a decision, and the gate is
        # unchanged -- this makes the existing behaviour measurable, which is the
        # precondition for ever tuning it.
        "market_regime": row.get("Market Regime") if row is not None else None,
        "reference_regime": row.get("Reference Regime") if row is not None else None,
        "watchlist_breadth_score": (
            row.get("Watchlist Breadth Score") if row is not None else None
        ),
        "above_ema20_pct": row.get("Above EMA20 %") if row is not None else None,
        "vix_move_pct": row.get("VIX Move %") if row is not None else None,
        # Higher-timeframe context: was this taken with or against the daily trend.
        "daily_trend": row.get("Daily Trend") if row is not None else None,
        "daily_realised_vol": row.get("Daily Realised Vol %") if row is not None else None,
        "realised_vol_source": row.get("IV_RV_SOURCE") if row is not None else None,
        "stop_viability_enforced": row.get("STOP_VIABILITY_ENFORCED") if row is not None else None,
        **_decision_option_details(row),
        **_gate_counterfactuals(row, controls),
    }
    write_auto_paper_decision(entry, trading_day)


def _closed_paper_trades(paper_trades):
    return [trade for trade in (paper_trades or {}).values() if trade.get("status") == "CLOSED"]


def _auto_paper_trade_count_today(paper_trades, profile=None):
    """Auto-paper entries opened today, optionally within one holding profile.

    `profile=None` keeps the original whole-book count, which is what the shared
    MAX_DAILY_ENTRIES cap still uses.
    """

    today = _current_et().date()
    profile = str(profile).upper() if profile else None
    count = 0
    for trade in paper_trades.values():
        opened_at = trade.get("opened_at")
        if not opened_at:
            continue
        try:
            opened_date = datetime.strptime(opened_at, "%Y-%m-%d %H:%M:%S").date()
        except Exception:
            continue
        if opened_date != today or not str(trade.get("notes", "")).startswith("Auto paper"):
            continue
        if profile and str(trade.get("holding_profile") or "INTRADAY").upper() != profile:
            continue
        count += 1
    return count


def _legacy_spread_to_risk_multiple():
    """`AUTO_PAPER_MAX_SPREAD_TO_RISK` expressed as a stop-spread multiple.

    The two spellings of this rule were inverses of each other. This path asked
    "how large may the spread be as a fraction of the risk move" (0.5 = strict);
    `MIN_STOP_SPREAD_MULTIPLE` asks "how many times the spread must the risk move
    cover" (2.0 = the same strictness). Returns None when the legacy variable is
    unset, which is the normal case -- it is honoured only so that a deployment
    which had set it does not silently loosen on this deploy.
    """

    import os

    raw = os.getenv("AUTO_PAPER_MAX_SPREAD_TO_RISK")

    if raw is None or str(raw).strip() == "":
        return None

    try:
        tolerance = float(raw)
    except (TypeError, ValueError):
        return None

    return None if tolerance <= 0 else 1.0 / tolerance


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

    **Delegates to `evaluate_stop_viability` rather than repeating the arithmetic.**
    This function and that module implemented the same rule twice, with two
    separate environment variables holding reciprocal values and two different
    premium fallbacks (`Option Midpoint` here, `Option Ask` there). Raising
    `MIN_STOP_SPREAD_MULTIPLE` therefore tightened the scanner's copy and left this
    one at 1.0 -- survivable only because this check sits downstream of a row the
    scanner has already downgraded to AVOID, so the looser copy could never be
    reached while both were enabled. Set `STOP_VIABILITY_ENFORCE=false` and it
    becomes the only copy that runs, at a threshold nobody chose.

    Returns False when delta, premium or spread are missing rather than blocking on
    absent data: a gate that fires on a missing field silently stops trading
    altogether, which is a worse failure than the one it prevents. That is
    `evaluate_stop_viability`'s `viable is None`, which this maps back to False.
    """

    verdict = evaluate_stop_viability(
        row.get("Candidate Entry Price"),
        row.get("Candidate Stop Price"),
        row.get("Option Mid Price")
        or row.get("Option Midpoint")
        or row.get("Option Ask"),
        row.get("Option Delta"),
        row.get("Option Spread %"),
        min_multiple=_legacy_spread_to_risk_multiple(),
    )

    if verdict.get("viable") is not False:
        return False, None

    # STOP_AT_ENTRY is a zero-risk stop, which this rule has never spoken about --
    # it is the risk manager's to reject, and reporting it as a spread problem
    # would misattribute the block in the decision ledger.
    if verdict.get("reason") == "STOP_AT_ENTRY":
        return False, None

    return True, (
        f"SPREAD_EXCEEDS_RISK: spread {verdict['round_trip_spread_pct']:.1f}% vs "
        f"{verdict['move_pct_of_premium']:.1f}% option move to stop "
        f"({verdict['spread_multiple']:.2f}x, need "
        f"{verdict['required_multiple']:.2f}x)"
    )


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
    # `direction` so a reversal is not blocked by the trade its own reversal
    # invalidated. See cooldown_is_directional().
    if is_symbol_in_cooldown(
        symbol, _closed_paper_trades(paper_trades), now_et, cooldown_minutes,
        direction=direction,
    ):
        return False, "SYMBOL_COOLDOWN_ACTIVE"
    if symbol_trade_count_today(paper_trades, symbol, now_et) >= env_int("MAX_TRADES_PER_SYMBOL_PER_DAY", 1):
        return False, "MAX_TRADES_PER_SYMBOL_PER_DAY_REACHED"
    open_trades = [trade for trade in paper_trades.values() if trade.get("status") == "OPEN"]

    # Both limits were hardcoded here, which meant MAX_ACTIVE_PAPER_TRADES was read
    # by the affordability config and honoured by the dashboard entry path while
    # this path -- the one that opens almost every trade -- ignored it and used 3.
    # Setting it to 1 therefore had no effect on automated entries at all.
    if len(open_trades) >= max_active_paper_trades():
        return False, "MAX_ACTIVE_PAPER_TRADES_REACHED"

    # Previously a hardcoded 1: a single open CALL blocked every other bullish
    # setup. Under a MULTIDAY profile (force_eod_exit=False) that position is held
    # overnight, so one trade could block the book for days. This is the constraint
    # that decided how many trades a day actually happened.
    if len([
        trade for trade in open_trades if trade.get("direction") == direction
    ]) >= max_active_per_direction():
        return False, "DIRECTION_ALREADY_ACTIVE"

    # Per-profile budgets, applied under the shared caps rather than instead of
    # them. Both default to the shared cap, so this is inert until set: an
    # overnight carry cannot quietly consume tomorrow's intraday capacity once
    # MAX_ACTIVE_MULTIDAY_TRADES names its own ceiling.
    profile = candidate_holding_profile(row)

    if _active_profile_count(open_trades, profile) >= max_active_for_profile(profile):
        return False, f"MAX_ACTIVE_{profile}_TRADES_REACHED"

    if _auto_paper_trade_count_today(paper_trades) >= controls["max_daily"]:
        return False, "DAILY_AUTO_PAPER_LIMIT_REACHED"

    if (
        _auto_paper_trade_count_today(paper_trades, profile)
        >= max_daily_for_profile(profile, controls)
    ):
        return False, f"DAILY_{profile}_LIMIT_REACHED"
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