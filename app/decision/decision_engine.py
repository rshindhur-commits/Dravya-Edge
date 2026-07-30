from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from app.gates.entry_gate import price_geometry_error


ACTION_ENTER_PAPER = "ENTER_PAPER"


@dataclass
class TradeDecision:

    action: str
    setup_score: float
    rr: float
    option_quality: float
    confidence: float
    reasons: list[str] = field(default_factory=list)
    block_reasons: list[str] = field(default_factory=list)
    holding_profile: str = "INTRADAY"

    @property
    def score(self) -> float:

        return self.confidence

    @property
    def blocked(self) -> bool:

        return bool(self.block_reasons)


def _row_get(row: dict[str, Any], *names: str, default=None):

    for name in names:

        try:

            value = row.get(name)

        except Exception:

            value = None

        if value is None:

            continue

        if str(value).strip().lower() in {"", "nan", "none"}:

            continue

        return value

    return default


def _safe_float(value, default=0.0) -> float:

    try:

        if value is None:

            return default

        numeric_value = float(value)

        if math.isnan(numeric_value) or math.isinf(numeric_value):

            return default

        return numeric_value

    except Exception:

        return default


def _bool_value(value) -> bool:

    if isinstance(value, bool):

        return value

    return str(value).strip().lower() in {"true", "1", "yes", "y", "on"}


def _append_reason(reasons: list[str], reason) -> None:

    reason = str(reason or "").strip()

    if reason.upper() in {
        "NO_BLOCK",
        "NOT_BLOCKED",
        "NONE",
        "NAN",
        "FALSE",
    }:

        return

    if reason and reason not in reasons:

        reasons.append(reason)


def evaluate_candidate(candidate: dict[str, Any] | None) -> TradeDecision:

    candidate = candidate or {}
    from app.state.holding_policy import derive_holding_profile

    holding_profile = derive_holding_profile(candidate).value
    action = str(
        _row_get(
            candidate,
            "Action Status",
            "action",
            "action_status",
            default="UNKNOWN",
        )
    ).strip().upper()
    setup_score = _safe_float(
        _row_get(
            candidate,
            "Setup %",
            "setup_score",
            "setup_percent",
            "15m Score",
        )
    )
    rr = _safe_float(
        _row_get(
            candidate,
            "Candidate RR",
            "Risk Reward",
            "RR",
            "rr",
        )
    )
    option_quality = _safe_float(
        _row_get(
            candidate,
            "Option Quality Score",
            "option_quality",
            "option_quality_score",
        )
    )
    confidence = _safe_float(
        _row_get(
            candidate,
            "Decision Confidence",
            "Entry Alert Score",
            "confidence",
            "score",
            default=setup_score,
        ),
        setup_score,
    )
    reasons: list[str] = []
    block_reasons: list[str] = []

    _append_reason(reasons, _row_get(candidate, "Action Reason", "reason"))
    _append_reason(reasons, _row_get(candidate, "Next Condition", "next_condition"))

    if action != ACTION_ENTER_PAPER:

        _append_reason(block_reasons, f"ACTION_{action or 'UNKNOWN'}")

    if _bool_value(_row_get(candidate, "Event Blocked", "event_blocked", default=False)):

        _append_reason(block_reasons, "EVENT_BLOCKED")

    if _bool_value(_row_get(candidate, "Regime Blocked", "regime_blocked", default=False)):

        _append_reason(block_reasons, "REGIME_BLOCKED")

    realtime_ready = _row_get(candidate, "Realtime Ready", "realtime_ready")

    if realtime_ready is not None and not _bool_value(realtime_ready):

        _append_reason(block_reasons, "REALTIME_NOT_READY")

    _append_reason(block_reasons, _row_get(candidate, "Blocked By", "blocked_by"))
    _append_reason(block_reasons, _row_get(candidate, "Realtime Block Reason", "realtime_block_reason"))
    _append_reason(block_reasons, _row_get(candidate, "Option Rejection Reason", "option_rejection_reason"))

    geometry_error = price_geometry_error(candidate)

    if geometry_error:

        _append_reason(block_reasons, geometry_error)

    return TradeDecision(
        action=action,
        setup_score=setup_score,
        rr=rr,
        option_quality=option_quality,
        confidence=confidence,
        reasons=reasons,
        block_reasons=block_reasons,
        holding_profile=holding_profile,
    )


def build_review_rule_evaluations(candidate, decision, scan_id):
    from app.gates.rule_evaluation import RuleEvaluation

    decision = decision or TradeDecision("UNKNOWN", 0.0, 0.0, 0.0, 0.0)
    review_ready = decision.action == ACTION_ENTER_PAPER and not decision.block_reasons
    return [
        RuleEvaluation(
            scan_id,
            str(_row_get(candidate, "Symbol", "symbol", default="")),
            _row_get(candidate, "Entry", "setup", "setup_type"),
            "Review Eligibility",
            "Review",
            decision.action,
            ACTION_ENTER_PAPER,
            review_ready,
            # Operational outcome, not an entry gate.
            False,
            40,
        )
    ]