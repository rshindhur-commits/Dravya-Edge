from __future__ import annotations

from dataclasses import asdict, dataclass

from app.gates.entry_gate import EntryGateConfig, build_entry_gate_rule_evaluations


OPERATIONAL_RULE_GROUPS = {"TELEGRAM", "PAPER", "REVIEW", "TRADE LIFECYCLE", "REPLAY"}


def rule_domain(rule_group):
    return "OPERATIONAL" if str(rule_group or "").upper() in OPERATIONAL_RULE_GROUPS else "TRADING"


@dataclass(frozen=True)
class RuleEvaluation:
    scan_id: str
    symbol: str
    setup: str | None
    rule_name: str
    rule_group: str
    actual_value: object
    required_value: object
    passed: bool
    blocked_trade: bool
    priority: int = 100
    evaluation_phase: str = "ENTRY"

    def to_record(self):
        return asdict(self)


def resolve_blocked_trade(rule_group, passed, blocked_trade=None):
    """Only a TRADING rule can block a trade.

    Telegram, Paper, Review, Trade Lifecycle, and Replay are operational
    outcomes, not entry gates: Telegram is a notification transport that
    "does not block an alertable action", and Paper/Review describe what
    happened *after* the decision. Recording blocked_trade=True on them
    misattributes the cause of a no-trade -- on 2026-07-29 the Telegram rule
    claimed to have blocked all 884 rows, including the one trade that opened.
    """

    if rule_domain(rule_group) == "OPERATIONAL":
        return False

    return not bool(passed) if blocked_trade is None else bool(blocked_trade)


def _evaluation(scan_id, row, name, group, actual, required, passed, priority=100, evaluation_phase="ENTRY", blocked_trade=None):
    return RuleEvaluation(
        scan_id=scan_id,
        symbol=str(row.get("Symbol") or row.get("symbol") or ""),
        setup=row.get("Entry") or row.get("setup") or row.get("setup_type"),
        rule_name=name,
        rule_group=group,
        actual_value=actual,
        required_value=required,
        passed=bool(passed),
        blocked_trade=resolve_blocked_trade(group, passed, blocked_trade),
        priority=priority,
        evaluation_phase=evaluation_phase,
    )


def build_rule_evaluations(row, scan_id, config=None):
    """Create a uniform, queryable gate audit from an already-scored scanner row."""
    row = row or {}
    evaluations = build_entry_gate_rule_evaluations(row, config or EntryGateConfig(), scan_id)
    telegram = row.get("Telegram Eligibility")
    if telegram is not None:
        evaluations.append(_evaluation(scan_id, row, "Telegram", "Telegram", telegram, "ELIGIBLE", str(telegram).upper() in {"ELIGIBLE", "SENT"}, 60))
    paper = row.get("Paper Trade Opened")
    if paper is not None:
        evaluations.append(_evaluation(scan_id, row, "Paper", "Paper", paper, True, str(paper).lower() in {"true", "1", "yes"}, 50))
    review = row.get("Real Trade Readiness")
    if review is not None:
        evaluations.append(_evaluation(scan_id, row, "Review", "Review", review, "READY", str(review).upper() in {"READY", "PASS", "TRUE"}, 40))
    trade_action = str(row.get("Trade Action") or "").strip().upper()
    bars_in_trade = _as_int(row.get("Bars In Trade"))
    if trade_action in {"HOLD", "PARTIAL_PROFIT"} and bars_in_trade > 0:
        evaluations.append(_evaluation(scan_id, row, "Active Trade Management", "Trade Lifecycle", trade_action, "HOLD_OR_PARTIAL_PROFIT", True, 50, "ACTIVE", False))
    if _as_bool(row.get("Live Exit Signal")):
        evaluations.append(_evaluation(scan_id, row, "Exit Decision", "Trade Lifecycle", row.get("Live Exit Reason"), "EXIT", True, 70, "EXIT", False))
    if _as_bool(row.get("Replay Ran")):
        replay_outcome = row.get("Replay Outcome")
        evaluations.append(_evaluation(scan_id, row, "Replay Outcome", "Replay", replay_outcome, "OUTCOME_AVAILABLE", replay_outcome not in {None, ""}, 30, "REPLAY", False))
    return evaluations


def _as_bool(value):
    return str(value).strip().lower() in {"true", "1", "yes"}


def _as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def aggregate_rule_evaluations(*evaluation_groups):
    """Merge native validator outputs without duplicating a rule per scanner row."""
    merged = {}

    for group in evaluation_groups:

        for evaluation in group or []:

            key = (
                evaluation.scan_id,
                evaluation.symbol,
                evaluation.setup,
                evaluation.evaluation_phase,
                evaluation.rule_group,
                evaluation.rule_name,
            )
            merged[key] = evaluation

    return list(merged.values())
