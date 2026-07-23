from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from math import sqrt


@dataclass(frozen=True)
class V2LearningRecord:
    learning_id: str
    trading_day: str
    session_id: str | None
    scan_id: str | None
    trade_id: str | None
    engine_version: str
    symbol: str
    direction: str | None
    setup: str | None
    candidate_key: str | None
    entry_timestamp: str | None
    entry_price: float | None
    trend_age: int | None
    pullback_number: int | None
    bars_since_breakout: int | None
    entry_efficiency_score: float | None
    distance_from_ema9_pct: float | None
    distance_from_vwap_pct: float | None
    distance_from_ema20_pct: float | None
    atr_extension: float | None
    ema_alignment_score: float | None
    volume_confirmation_score: float | None
    market_breadth: float | None
    sector_strength: float | None
    relative_strength: float | None
    regime: str | None
    option_quality: float | None
    rr: float | None
    max_trend_health: float | None
    min_trend_health: float | None
    avg_trend_health: float | None
    trend_health_std: float | None
    max_mfe_r: float | None
    max_mae_r: float | None
    bars_held: int | None
    time_held_minutes: float | None
    exit_timestamp: str | None
    exit_price: float | None
    exit_phase: str | None
    primary_exit: str | None
    secondary_exit: str | None
    exit_reason: str | None
    trend_health_at_exit: float | None
    mfe_r_at_exit: float | None
    mae_r_at_exit: float | None
    final_r: float | None
    trend_capture_pct: float | None
    tes: float | None
    left_on_table: float | None
    winner: bool | None
    exit_verdict: str | None
    entry_grade: str | None
    exit_grade: str | None
    entry_quality_label: str
    exit_quality_label: str
    execution_quality: str

    def to_record(self):
        return asdict(self)


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _minutes_between(start, end):
    try:
        return round((datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))).total_seconds() / 60, 2)
    except Exception:
        return None


def _entry_label(trade):
    score = _number(trade.get("entry_efficiency_score"))
    pullback = trade.get("pullback_number")
    trend_age = trade.get("trend_age")
    if score is not None and score < 65:
        return "CHASED"
    if trend_age is not None and int(trend_age) > 3:
        return "LATE"
    if pullback is not None and int(pullback) > 1:
        return "LATE"
    return "GOOD" if score is None or score >= 80 else "EARLY"


def _exit_label(trade):
    phase = str(trade.get("exit_phase") or "").upper()
    health = _number(trade.get("trend_health_at_exit"))
    if phase == "HARD_TARGET":
        return "PERFECT"
    if phase == "HARD_STOP":
        return "STOPPED_REAL_REVERSAL" if health is not None and health < 40 else "STOPPED_NOISE"
    if health is not None and health >= 70 and phase not in {"HOLD", ""}:
        return "EARLY"
    return "LATE" if phase == "TIME_EXIT" else "PERFECT"


def _execution_quality(final_r, capture):
    if capture is not None:
        return "A" if capture >= 70 else "B" if capture >= 50 else "C" if capture > 0 else "D"
    return "A" if final_r is not None and final_r >= 1.5 else "B" if final_r is not None and final_r > 0 else "D"


def build_learning_record(trading_day, trade, scanner_context=None):
    trade = trade or {}
    context = scanner_context or trade.get("scanner_context") or {}
    samples = int(trade.get("trend_health_samples") or 0)
    health_sum = _number(trade.get("trend_health_sum")) or 0.0
    health_sum_sq = _number(trade.get("trend_health_sum_sq")) or 0.0
    avg_health = health_sum / samples if samples else _number(trade.get("trend_health_at_exit"))
    std_health = sqrt(max(0.0, health_sum_sq / samples - avg_health * avg_health)) if samples and avg_health is not None else None
    final_r = _number(trade.get("final_r") if trade.get("final_r") is not None else trade.get("rr_progress"))
    mfe_r = _number(trade.get("mfe_r"))
    capture = max(0.0, min(100.0, final_r / mfe_r * 100)) if final_r is not None and mfe_r and mfe_r > 0 else None
    source = "|".join([str(trade.get("engine_version") or "v1"), str(trade.get("symbol")), str(trade.get("opened_at")), str(trade.get("closed_at"))])
    record = V2LearningRecord(
        learning_id=source,
        trading_day=trading_day,
        session_id=trade.get("session_id"),
        scan_id=trade.get("scan_id"),
        trade_id=trade.get("trade_id") or trade.get("trade_key"),
        engine_version=str(trade.get("engine_version") or "v1"),
        symbol=str(trade.get("symbol") or ""),
        direction=trade.get("direction"),
        setup=trade.get("entry_type"),
        candidate_key=trade.get("candidate_key"),
        entry_timestamp=trade.get("opened_at"),
        entry_price=_number(trade.get("entry_price")),
        trend_age=trade.get("trend_age"),
        pullback_number=trade.get("pullback_number"),
        bars_since_breakout=trade.get("bars_since_breakout"),
        entry_efficiency_score=_number(trade.get("entry_efficiency_score")),
        distance_from_ema9_pct=_number(trade.get("distance_from_ema9_pct")),
        distance_from_vwap_pct=_number(trade.get("distance_from_vwap_pct")),
        distance_from_ema20_pct=_number(trade.get("distance_from_ema20_pct")),
        atr_extension=_number(trade.get("atr_extension")),
        ema_alignment_score=_number(trade.get("ema_alignment_score")),
        volume_confirmation_score=_number(trade.get("volume_confirmation_score")),
        market_breadth=_number(context.get("Watchlist Breadth Score")),
        sector_strength=_number(context.get("Sector Strength")),
        relative_strength=_number(context.get("RS vs QQQ")),
        regime=context.get("Market Regime"),
        option_quality=_number(trade.get("option_quality_score") or context.get("Option Quality Score")),
        rr=_number(trade.get("risk_reward") or trade.get("planned_rr")),
        max_trend_health=_number(trade.get("max_trend_health")),
        min_trend_health=_number(trade.get("min_trend_health")),
        avg_trend_health=avg_health,
        trend_health_std=round(std_health, 3) if std_health is not None else None,
        max_mfe_r=mfe_r,
        max_mae_r=_number(trade.get("mae_r")),
        bars_held=trade.get("bars_in_trade"),
        time_held_minutes=_minutes_between(trade.get("opened_at"), trade.get("closed_at")),
        exit_timestamp=trade.get("closed_at"),
        exit_price=_number(trade.get("close_price")),
        exit_phase=trade.get("exit_phase"),
        primary_exit=trade.get("exit_phase"),
        secondary_exit=trade.get("secondary_exit"),
        exit_reason=trade.get("exit_reason") or trade.get("exit_phase"),
        trend_health_at_exit=_number(trade.get("trend_health_at_exit")),
        mfe_r_at_exit=mfe_r,
        mae_r_at_exit=_number(trade.get("mae_r")),
        final_r=final_r,
        trend_capture_pct=round(capture, 2) if capture is not None else None,
        tes=_number(trade.get("tes")),
        left_on_table=_number(trade.get("left_on_table")),
        winner=final_r > 0 if final_r is not None else None,
        exit_verdict=trade.get("exit_verdict"),
        entry_grade=trade.get("entry_grade"),
        exit_grade=trade.get("exit_grade"),
        entry_quality_label="",
        exit_quality_label="",
        execution_quality="",
    )
    enriched = record.to_record()
    enriched["entry_quality_label"] = _entry_label(enriched)
    enriched["exit_quality_label"] = _exit_label(enriched)
    enriched["execution_quality"] = _execution_quality(final_r, capture)
    return enriched