from __future__ import annotations

import json

from app.gates.rule_evaluation import build_rule_evaluations


STAGE_ORDER = [
    "Momentum",
    "Entry",
    "Risk",
    "Option",
    "Affordability",
    "Realtime",
    "Telegram",
    "Paper",
    "Decision",
]

RULE_GROUP_TO_STAGE = {
    "ENTRY": "Entry",
    "RISK": "Risk",
    "OPTION": "Option",
    "AFFORDABILITY": "Affordability",
    "REALTIME": "Realtime",
    "TELEGRAM": "Telegram",
    "PAPER": "Paper",
    "REVIEW": "Decision",
}


def _diagnostics(row):

    payload = (row or {}).get("ENTRY_DIAGNOSTICS_JSON") or (
        row or {}
    ).get("ENTRY_DIAGNOSTICS") or {}

    if isinstance(payload, str):

        try:

            payload = json.loads(payload)

        except (TypeError, ValueError):

            payload = {}

    return payload if isinstance(payload, dict) else {}


def _selected_setup(diagnostics, row):

    selected = str(
        diagnostics.get("candidate_setup")
        or (row or {}).get("ENTRY_SETUP_CANDIDATE")
        or (row or {}).get("Entry")
        or ""
    ).upper()

    for setup in diagnostics.get("setups") or []:

        if str(setup.get("setup") or "").upper() == selected:

            return setup

    return {}


def _rule(name, actual, required, passed, source, priority):

    return {
        "rule": name,
        "actual": actual,
        "required": required,
        "passed": bool(passed),
        "source": source,
        "priority": priority,
    }


def _empty_stage(name):

    return {
        "stage": name,
        "passed": None,
        "summary": "Not evaluated",
        "failed_rules": [],
        "passed_rules": [],
        "rules": [],
    }


def _summarize_stage(stage):

    rules = stage["rules"]

    if not rules:

        return stage

    failed = [rule for rule in rules if not rule["passed"]]
    stage["passed_rules"] = [rule["rule"] for rule in rules if rule["passed"]]
    stage["failed_rules"] = [
        {
            "rule": rule["rule"],
            "actual": rule["actual"],
            "required": rule["required"],
        }
        for rule in failed
    ]
    stage["passed"] = not failed

    if failed:

        stage["summary"] = "; ".join(
            f"{rule['rule']} failed"
            for rule in failed
        )

    else:

        stage["summary"] = "All evaluated rules passed"

    return stage


def _final_reason(row, blocker):

    return (
        (row or {}).get("Action Reason")
        or (row or {}).get("Blocked By")
        or (row or {}).get("Rejected Trade Reason")
        or (blocker or {}).get("rule")
        or "ELIGIBLE"
    )


def build_decision_waterfall(candidate, scan_id=None):
    """Build a fixed, read-only explanation of a V1 scanner decision."""

    candidate = candidate or {}
    diagnostics = _diagnostics(candidate)
    setup = _selected_setup(diagnostics, candidate)
    stage_map = {stage: _empty_stage(stage) for stage in STAGE_ORDER}
    signal = candidate.get("Final Signal") or diagnostics.get(
        "analysis",
        {},
    ).get("signal")
    directional = (
        "BULL" in str(signal or "").upper()
        or "BEAR" in str(signal or "").upper()
    )
    stage_map["Momentum"]["rules"].append(_rule(
        "Directional Signal",
        signal,
        "BULLISH or BEARISH",
        directional,
        "Entry Diagnostics",
        10,
    ))

    for condition in setup.get("conditions") or []:

        stage_map["Entry"]["rules"].append(_rule(
            condition.get("name"),
            condition.get("actual"),
            condition.get("required"),
            condition.get("passed"),
            "Entry Diagnostics",
            20,
        ))

    evaluations = build_rule_evaluations(
        candidate,
        scan_id or str(
            candidate.get("Scan ID")
            or candidate.get("scan_id")
            or ""
        ),
    )

    for evaluation in evaluations:

        stage = RULE_GROUP_TO_STAGE.get(
            str(evaluation.rule_group).upper(),
            "Decision",
        )
        stage_map[stage]["rules"].append(_rule(
            evaluation.rule_name,
            evaluation.actual_value,
            evaluation.required_value,
            evaluation.passed,
            "RuleEvaluation",
            evaluation.priority,
        ))

    action = (
        candidate.get("Action Status")
        or candidate.get("action_status")
        or "UNKNOWN"
    )
    stage_map["Decision"]["rules"].append(_rule(
        "Action Status",
        action,
        "ENTER or ENTER_PAPER",
        str(action).upper() in {"ENTER", "ENTER_PAPER"},
        "Scanner Decision",
        100,
    ))
    stages = [
        _summarize_stage(stage_map[stage])
        for stage in STAGE_ORDER
    ]
    first_blocker = next(
        (
            {
                "stage": stage["stage"],
                **rule,
            }
            for stage in stages
            for rule in stage["rules"]
            if not rule["passed"]
        ),
        None,
    )

    return {
        "symbol": candidate.get("Symbol") or candidate.get("symbol"),
        "setup": setup.get("setup") or candidate.get("Entry"),
        "final_action": action,
        "final_reason": _final_reason(candidate, first_blocker),
        "blocking_stage": (
            first_blocker.get("stage")
            if first_blocker
            else None
        ),
        "blocking_rule": (
            first_blocker.get("rule")
            if first_blocker
            else None
        ),
        "first_blocker": first_blocker,
        "stages": stages,
    }


def build_v2_decision_waterfall(candidate):
    """Expose the available V2 shadow decision facts without inventing gates."""

    candidate = candidate or {}
    stage_map = {stage: _empty_stage(stage) for stage in STAGE_ORDER}
    signal = candidate.get("Final Signal")
    directional = (
        "BULL" in str(signal or "").upper()
        or "BEAR" in str(signal or "").upper()
    )
    suggested = bool(candidate.get("V2 Entry Suggested"))
    stage_map["Momentum"]["rules"].append(_rule(
        "Directional Signal",
        signal,
        "BULLISH or BEARISH",
        directional,
        "V2 Shadow",
        10,
    ))
    stage_map["Entry"]["rules"].append(_rule(
        "V2 Entry Suggested",
        suggested,
        True,
        suggested,
        "V2 Shadow",
        20,
    ))
    action = "ENTER" if suggested else "WAIT"
    stage_map["Decision"]["rules"].append(_rule(
        "V2 Shadow Decision",
        action,
        "ENTER",
        suggested,
        "V2 Shadow",
        100,
    ))
    stages = [
        _summarize_stage(stage_map[stage])
        for stage in STAGE_ORDER
    ]
    blocker = next(
        (
            {"stage": stage["stage"], **rule}
            for stage in stages
            for rule in stage["rules"]
            if not rule["passed"]
        ),
        None,
    )
    return {
        "symbol": candidate.get("Symbol") or candidate.get("symbol"),
        "final_action": action,
        "final_reason": candidate.get("V2 Entry Reason") or "V2_SHADOW",
        "blocking_stage": blocker.get("stage") if blocker else None,
        "blocking_rule": blocker.get("rule") if blocker else None,
        "stages": stages,
    }


def build_v1_v2_waterfall_comparison(candidate, scan_id=None):

    v1 = build_decision_waterfall(candidate, scan_id=scan_id)
    v2 = build_v2_decision_waterfall(candidate)
    return {
        "symbol": v1.get("symbol"),
        "v1": v1,
        "v2": v2,
        "actions_disagree": v1.get("final_action") != v2.get("final_action"),
        "first_disagreement": next(
            (
                stage
                for stage, v1_stage, v2_stage in zip(
                    STAGE_ORDER,
                    v1["stages"],
                    v2["stages"],
                )
                if v1_stage.get("passed") != v2_stage.get("passed")
            ),
            None,
        ),
    }


def waterfall_rule_records(waterfall, scan_id):
    """Flatten a waterfall into one persistence row per evaluated rule."""

    records = []

    for stage_index, stage in enumerate(waterfall.get("stages") or [], start=1):

        for rule in stage.get("rules") or []:

            records.append({
                "scan_id": scan_id,
                "symbol": waterfall.get("symbol"),
                "stage": stage.get("stage"),
                "stage_order": stage_index,
                "passed": rule.get("passed"),
                "blocking": (
                    waterfall.get("blocking_stage") == stage.get("stage")
                    and waterfall.get("blocking_rule") == rule.get("rule")
                ),
                "rule_name": rule.get("rule"),
                "actual": rule.get("actual"),
                "required": rule.get("required"),
                "priority": rule.get("priority"),
                "summary": stage.get("summary"),
            })

    return records


def summarize_blocking_stages(waterfalls):

    counts = {}

    for waterfall in waterfalls or []:

        stage = waterfall.get("blocking_stage")

        if stage:

            counts[stage] = counts.get(stage, 0) + 1

    total_blocked = sum(counts.values())
    stages = [
        {
            "stage": stage,
            "count": count,
            "percentage": round(count / total_blocked * 100, 1)
            if total_blocked
            else 0,
        }
        for stage, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], STAGE_ORDER.index(item[0])),
        )
    ]
    return {
        "total_candidates": len(waterfalls or []),
        "total_blocked": total_blocked,
        "stages": stages,
    }