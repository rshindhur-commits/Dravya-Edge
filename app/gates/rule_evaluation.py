from __future__ import annotations

from dataclasses import asdict, dataclass

from app.gates.entry_gate import EntryGateConfig, build_entry_gate_rule_evaluations


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

    def to_record(self):
        return asdict(self)


def _evaluation(scan_id, row, name, group, actual, required, passed, priority=100):
    return RuleEvaluation(
        scan_id=scan_id,
        symbol=str(row.get("Symbol") or row.get("symbol") or ""),
        setup=row.get("Entry") or row.get("setup") or row.get("setup_type"),
        rule_name=name,
        rule_group=group,
        actual_value=actual,
        required_value=required,
        passed=bool(passed),
        blocked_trade=not bool(passed),
        priority=priority,
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
    return evaluations


def aggregate_rule_evaluations(*evaluation_groups):
    """Merge native validator outputs without duplicating a rule per scanner row."""
    merged = {}

    for group in evaluation_groups:

        for evaluation in group or []:

            key = (
                evaluation.scan_id,
                evaluation.symbol,
                evaluation.setup,
                evaluation.rule_group,
                evaluation.rule_name,
            )
            merged[key] = evaluation

    return list(merged.values())
