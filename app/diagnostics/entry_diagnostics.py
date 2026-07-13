from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from typing import Any

import pandas as pd


@dataclass
class EntryConditionDiagnostic:

    name: str
    passed: bool
    actual: Any = None
    required: Any = None
    description: str | None = None


@dataclass
class EntrySetupDiagnostic:

    setup: str
    direction: str
    matched_conditions: int
    total_conditions: int
    readiness: float
    passed_conditions: list[str] = field(default_factory=list)
    failed_conditions: list[str] = field(default_factory=list)
    conditions: list[EntryConditionDiagnostic] = field(default_factory=list)
    trigger: float | None = None


@dataclass
class EntryDiagnostics:

    ticker: str
    market_regime: str | None
    analysis: dict[str, Any]
    candidate_setup: str | None
    matched_conditions: int
    total_conditions: int
    failed_conditions: list[str]
    passed_conditions: list[str]
    indicator_values: dict[str, Any]
    readiness: float
    timeline: list[str]
    setups: list[EntrySetupDiagnostic] = field(default_factory=list)


def _safe_float(value, default=None):

    try:

        if value is None:

            return default

        numeric_value = float(value)

        if math.isnan(numeric_value) or math.isinf(numeric_value):

            return default

        return round(numeric_value, 4)

    except Exception:

        return default


def _safe_bool(value) -> bool:

    if isinstance(value, bool):

        return value

    return str(value).strip().lower() in {"true", "1", "yes", "y", "on"}


def _condition(name, passed, actual=None, required=None, description=None):

    return EntryConditionDiagnostic(
        name=name,
        passed=bool(passed),
        actual=_json_safe(actual),
        required=_json_safe(required),
        description=description,
    )


def _json_safe(value):

    if isinstance(value, (str, int, bool)) or value is None:

        return value

    if isinstance(value, float):

        if math.isnan(value) or math.isinf(value):

            return None

        return round(value, 4)

    try:

        if pd.isna(value):

            return None

    except Exception:

        pass

    try:

        return round(float(value), 4)

    except Exception:

        return str(value)


def _setup_diagnostic(setup, direction, conditions, trigger=None):

    passed = [condition.name for condition in conditions if condition.passed]
    failed = [condition.name for condition in conditions if not condition.passed]
    total = len(conditions)
    matched = len(passed)
    readiness = round((matched / total) * 100, 1) if total else 0.0

    return EntrySetupDiagnostic(
        setup=setup,
        direction=direction,
        matched_conditions=matched,
        total_conditions=total,
        readiness=readiness,
        passed_conditions=passed,
        failed_conditions=failed,
        conditions=conditions,
        trigger=trigger,
    )


def _signal_is(analysis, *signals):

    signal = str((analysis or {}).get("signal") or "").upper()

    return signal in {str(item).upper() for item in signals}


def _indicator_values(latest, recent_high=None, recent_low=None):

    names = [
        "Open",
        "High",
        "Low",
        "Close",
        "VWAP",
        "EMA9",
        "EMA20",
        "MACD",
        "MACD_SIGNAL",
        "RSI",
        "ADX",
        "ATR",
        "ATR_PCT",
        "OBV",
        "REL_VOLUME",
        "BODY_STRENGTH",
        "BREAKOUT",
        "BREAKDOWN",
        "LOWER_HIGH",
        "ROLLING_SUPPORT",
        "PREV_LOW",
    ]
    values = {
        name: _json_safe(latest.get(name))
        for name in names
        if name in latest
    }
    values["RECENT_HIGH"] = _json_safe(recent_high)
    values["RECENT_LOW"] = _json_safe(recent_low)
    return values


def _latest_context(df):

    latest = df.iloc[-1]
    recent_high = latest.get("RECENT_HIGH")

    if recent_high is None or pd.isna(recent_high):

        recent_high = (
            df["High"].rolling(10).max().iloc[-2]
            if len(df) >= 2 and "High" in df
            else latest.get("High")
        )

    if pd.isna(recent_high):

        recent_high = df["High"].shift(1).tail(10).max()

    recent_low = latest.get("RECENT_LOW")

    if recent_low is None or pd.isna(recent_low):

        recent_low = (
            df["Low"].shift(1).tail(3).min()
            if "Low" in df
            else latest.get("Low")
        )
    return latest, recent_high, recent_low


def _evaluate_setups(df, analysis):

    latest, recent_high, recent_low = _latest_context(df)
    close = _safe_float(latest.get("Close"))
    high = _safe_float(latest.get("High"))
    low = _safe_float(latest.get("Low"))
    vwap = _safe_float(latest.get("VWAP"))
    ema9 = _safe_float(latest.get("EMA9"))
    ema20 = _safe_float(latest.get("EMA20"))
    atr = _safe_float(latest.get("ATR"), 0) or 0
    rel_volume = _safe_float(latest.get("REL_VOLUME"), 0) or 0
    body_strength = _safe_float(latest.get("BODY_STRENGTH"), 0) or 0
    recent_high = _safe_float(recent_high)
    recent_low = _safe_float(recent_low)
    breakdown = _safe_bool(latest.get("BREAKDOWN"))
    lower_high = _safe_bool(latest.get("LOWER_HIGH"))

    bullish_signal = _signal_is(analysis, "BULLISH", "HIGH CONVICTION BULLISH")
    bearish_signal = _signal_is(analysis, "BEARISH", "HIGH CONVICTION BEARISH")

    setups = [
        _setup_diagnostic(
            "BREAKOUT",
            "CALL",
            [
                _condition("BULLISH_SIGNAL", bullish_signal, analysis.get("signal"), "BULLISH or HIGH CONVICTION BULLISH"),
                _condition("BREAKOUT_LEVEL", close is not None and recent_high is not None and close > recent_high, close, f"> {recent_high}"),
                _condition("REL_VOLUME", rel_volume > 1.2, rel_volume, "> 1.2"),
                _condition("EMA_ALIGNMENT", ema9 is not None and ema20 is not None and ema9 > ema20, ema9, f"> EMA20 {ema20}"),
                _condition("VWAP", close is not None and vwap is not None and close > vwap, close, f"> VWAP {vwap}"),
            ],
            trigger=recent_high,
        ),
        _setup_diagnostic(
            "EMA_PULLBACK",
            "CALL",
            [
                _condition("BULLISH_SIGNAL", bullish_signal, analysis.get("signal"), "BULLISH or HIGH CONVICTION BULLISH"),
                _condition("CLOSE_ABOVE_EMA9", close is not None and ema9 is not None and close > ema9, close, f"> EMA9 {ema9}"),
                _condition("PULLBACK_TO_EMA9", low is not None and ema9 is not None and abs(low - ema9) <= atr * 0.25, low, f"within ATR*0.25 of EMA9 {ema9}"),
                _condition("EMA_ALIGNMENT", ema9 is not None and ema20 is not None and ema9 > ema20, ema9, f"> EMA20 {ema20}"),
                _condition("VWAP", close is not None and vwap is not None and close > vwap, close, f"> VWAP {vwap}"),
            ],
            trigger=ema9,
        ),
        _setup_diagnostic(
            "EMA_REJECTION_SHORT",
            "PUT",
            [
                _condition("BEARISH_SIGNAL", bearish_signal, analysis.get("signal"), "BEARISH or HIGH CONVICTION BEARISH"),
                _condition("CLOSE_BELOW_EMA9", close is not None and ema9 is not None and close < ema9, close, f"< EMA9 {ema9}"),
                _condition("REJECTED_EMA9", high is not None and ema9 is not None and high >= ema9, high, f">= EMA9 {ema9}"),
                _condition("EMA_ALIGNMENT", ema9 is not None and ema20 is not None and ema9 < ema20, ema9, f"< EMA20 {ema20}"),
                _condition("VWAP", close is not None and vwap is not None and close < vwap, close, f"< VWAP {vwap}"),
            ],
            trigger=ema9,
        ),
        _setup_diagnostic(
            "BREAKDOWN_SHORT",
            "PUT",
            [
                _condition("BEARISH_STRUCTURE", breakdown and lower_high, {"BREAKDOWN": breakdown, "LOWER_HIGH": lower_high}, "BREAKDOWN and LOWER_HIGH"),
                _condition("VWAP", close is not None and vwap is not None and close < vwap, close, f"< VWAP {vwap}"),
                _condition("EMA_ALIGNMENT", ema9 is not None and ema20 is not None and ema9 < ema20, ema9, f"< EMA20 {ema20}"),
                _condition("BODY_STRENGTH", body_strength > 0.5, body_strength, "> 0.5"),
                _condition("REL_VOLUME", rel_volume > 1.1, rel_volume, "> 1.1"),
                _condition("RECENT_LOW_OR_LOWER_HIGH", (close is not None and recent_low is not None and close <= recent_low) or lower_high, {"close": close, "recent_low": recent_low, "lower_high": lower_high}, "close <= recent low or lower high"),
            ],
            trigger=recent_low,
        ),
        _setup_diagnostic(
            "VWAP_REJECTION",
            "PUT",
            [
                _condition("BEARISH_SIGNAL", bearish_signal, analysis.get("signal"), "BEARISH or HIGH CONVICTION BEARISH"),
                _condition("TESTED_VWAP", high is not None and vwap is not None and high > vwap, high, f"> VWAP {vwap}"),
                _condition("CLOSE_BELOW_VWAP", close is not None and vwap is not None and close < vwap, close, f"< VWAP {vwap}"),
                _condition("EMA_ALIGNMENT", ema9 is not None and ema20 is not None and ema9 < ema20, ema9, f"< EMA20 {ema20}"),
                _condition("REL_VOLUME", rel_volume > 1.0, rel_volume, "> 1.0"),
            ],
            trigger=vwap,
        ),
    ]
    return setups, _indicator_values(latest, recent_high=recent_high, recent_low=recent_low)


def empty_entry_diagnostics(symbol, reason="No diagnostic context", market_regime="UNKNOWN"):

    diagnostic = EntryDiagnostics(
        ticker=symbol,
        market_regime=market_regime,
        analysis={},
        candidate_setup=None,
        matched_conditions=0,
        total_conditions=0,
        failed_conditions=[reason],
        passed_conditions=[],
        indicator_values={},
        readiness=0.0,
        timeline=[reason],
        setups=[],
    )
    return asdict(diagnostic)


def build_entry_diagnostics(symbol, df, analysis, market_regime=None, selected_entry=None):

    if df is None or df.empty:

        return empty_entry_diagnostics(symbol, reason="No indicator dataframe", market_regime=market_regime or "UNKNOWN")

    analysis = analysis or {}
    setups, indicator_values = _evaluate_setups(df, analysis)
    selected_type = str((selected_entry or {}).get("entry_type") or "").upper()
    exact_match = next((setup for setup in setups if setup.setup == selected_type), None)
    closest = exact_match or max(setups, key=lambda setup: setup.readiness)
    action_entry = selected_type or "NO_ENTRY"
    timeline = [
        f"Momentum {analysis.get('score')}",
        str(analysis.get("signal") or "UNKNOWN"),
        f"Candidate {closest.setup}",
        f"Readiness {closest.readiness}%",
        "Failed " + ", ".join(closest.failed_conditions) if closest.failed_conditions else "Failed none",
        f"Selected {action_entry}",
    ]
    diagnostic = EntryDiagnostics(
        ticker=symbol,
        market_regime=market_regime or analysis.get("market_regime"),
        analysis={
            "signal": analysis.get("signal"),
            "score": _json_safe(analysis.get("score")),
            "category_score": _json_safe(analysis.get("category_score")),
            "entry_timing_ok": analysis.get("entry_timing_ok"),
        },
        candidate_setup=closest.setup,
        matched_conditions=closest.matched_conditions,
        total_conditions=closest.total_conditions,
        failed_conditions=closest.failed_conditions,
        passed_conditions=closest.passed_conditions,
        indicator_values=indicator_values,
        readiness=closest.readiness,
        timeline=timeline,
        setups=setups,
    )
    return asdict(diagnostic)


def build_entry_diagnostics_from_snapshot(row: dict[str, Any]):

    mapping = {
        "ENTRY_OPEN": "Open",
        "ENTRY_HIGH": "High",
        "ENTRY_LOW": "Low",
        "ENTRY_CLOSE": "Close",
        "ENTRY_EMA9": "EMA9",
        "ENTRY_EMA20": "EMA20",
        "ENTRY_VWAP": "VWAP",
        "ENTRY_RSI": "RSI",
        "ENTRY_MACD": "MACD",
        "ENTRY_MACD_SIGNAL": "MACD_SIGNAL",
        "ENTRY_REL_VOLUME": "REL_VOLUME",
        "ENTRY_BODY_STRENGTH": "BODY_STRENGTH",
        "ENTRY_ATR": "ATR",
        "ENTRY_ADX": "ADX",
        "ENTRY_OBV": "OBV",
        "ENTRY_BREAKOUT": "BREAKOUT",
        "ENTRY_BREAKDOWN": "BREAKDOWN",
        "ENTRY_LOWER_HIGH": "LOWER_HIGH",
        "ENTRY_ROLLING_SUPPORT": "ROLLING_SUPPORT",
        "ENTRY_PREV_LOW": "PREV_LOW",
        "ENTRY_RECENT_HIGH": "RECENT_HIGH",
        "ENTRY_RECENT_LOW": "RECENT_LOW",
    }
    snapshot = {
        target: row.get(source)
        for source, target in mapping.items()
    }

    required = [
        "Close",
        "High",
        "Low",
        "EMA9",
        "EMA20",
        "VWAP",
        "REL_VOLUME",
        "BODY_STRENGTH",
        "ATR",
    ]
    missing = [
        name for name in required
        if snapshot.get(name) is None or str(snapshot.get(name)).strip().lower() in {"", "nan", "none"}
    ]

    symbol = row.get("Symbol") or row.get("symbol") or "UNKNOWN"
    market_regime = row.get("Market Regime") or row.get("market_regime") or "UNKNOWN"

    if missing:

        return empty_entry_diagnostics(
            symbol,
            reason="Missing replay indicators: " + ", ".join(missing),
            market_regime=market_regime,
        )

    df = pd.DataFrame([snapshot])
    analysis = {
        "signal": row.get("Final Signal") or row.get("Signal"),
        "score": row.get("15m Score") or row.get("score"),
        "category_score": row.get("Category Score"),
        "entry_timing_ok": row.get("Entry Timing OK", True),
    }
    return build_entry_diagnostics(
        symbol,
        df,
        analysis,
        market_regime=market_regime,
        selected_entry={
            "entry_type": row.get("Entry") or row.get("entry") or "NO_ENTRY"
        },
    )


def diagnostics_to_json(diagnostics) -> str:

    return json.dumps(diagnostics or {}, default=str, sort_keys=True)


def classify_entry_gate_failure_stage(row: dict[str, Any]) -> str:

    action = str(row.get("Action Status") or row.get("action_status") or "").upper()
    final_signal = str(row.get("Final Signal") or row.get("Signal") or "").upper()
    entry = str(row.get("Entry") or row.get("entry") or "").upper()
    blocked_by = str(row.get("Blocked By") or row.get("blocked_by") or "").upper()
    option_reason = str(row.get("Option Rejection Reason") or row.get("option_rejection_reason") or "").upper()
    realtime_reason = str(row.get("Realtime Block Reason") or row.get("realtime_block_reason") or "").upper()

    if action in {"ENTER", "ENTER_PAPER", "OPENED"}:

        return "Generated"

    if final_signal in {"NEUTRAL", "INVALID", "STALE DATA", "NO DATA", "ERROR"}:

        return "Momentum"

    if entry in {"", "NAN", "NONE", "NO_ENTRY", "NO_SETUP"}:

        return "Entry"

    if "RISK" in blocked_by or "RR" in blocked_by or "RISK" in action:

        return "Risk"

    if option_reason and option_reason not in {"NAN", "NONE"}:

        if "AFFORD" in option_reason or "EXPENSIVE" in option_reason or "CHEAP" in option_reason:

            return "Affordability"

        return "Option Quality"

    if "AFFORD" in blocked_by or "EXPENSIVE" in blocked_by:

        return "Affordability"

    if realtime_reason and realtime_reason not in {"NAN", "NONE"}:

        return "Realtime"

    if action in {"REVIEW_TV_CHART", "WAIT"}:

        return "Paper Gate"

    if "TELEGRAM" in blocked_by:

        return "Telegram"

    return "Unknown"


def summarize_entry_diagnostics(rows):

    failure_counts: dict[str, int] = {}
    regime_summary: dict[str, dict[str, Any]] = {}

    for row in rows or []:

        diagnostics = row.get("ENTRY_DIAGNOSTICS") or row.get("ENTRY_DIAGNOSTICS_JSON") or {}

        if isinstance(diagnostics, str):

            try:

                diagnostics = json.loads(diagnostics)

            except Exception:

                diagnostics = {}

        regime = str(row.get("Market Regime") or diagnostics.get("market_regime") or "UNKNOWN")
        action = str(row.get("Action Status") or "UNKNOWN")
        candidate = str(diagnostics.get("candidate_setup") or row.get("ENTRY_SETUP_CANDIDATE") or "UNKNOWN")
        direction = "bearish" if candidate in {"BREAKDOWN_SHORT", "VWAP_REJECTION", "EMA_REJECTION_SHORT"} else "bullish" if candidate not in {"UNKNOWN", "None"} else "unknown"
        regime_bucket = regime_summary.setdefault(
            regime,
            {
                "candidates": 0,
                "bullish_candidates": 0,
                "bearish_candidates": 0,
                "generated": 0,
                "failures": {},
            },
        )
        regime_bucket["candidates"] += 1

        if direction == "bullish":

            regime_bucket["bullish_candidates"] += 1

        elif direction == "bearish":

            regime_bucket["bearish_candidates"] += 1

        if action in {"ENTER", "ENTER_PAPER"}:

            regime_bucket["generated"] += 1

        for failure in diagnostics.get("failed_conditions") or []:

            failure_counts[failure] = failure_counts.get(failure, 0) + 1
            regime_bucket["failures"][failure] = regime_bucket["failures"].get(failure, 0) + 1

    for bucket in regime_summary.values():

        failures = bucket.get("failures") or {}
        bucket["top_failure"] = max(failures, key=failures.get) if failures else None

    return {
        "failure_counts": failure_counts,
        "regime_summary": regime_summary,
    }