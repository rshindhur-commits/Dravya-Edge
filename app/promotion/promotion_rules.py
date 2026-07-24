from __future__ import annotations


def evaluate_promotion(sample_size, improvement_pct, confidence, status="SHADOW"):
    if status in {"PROMOTED", "RETIRED"}:
        return status, "TERMINAL_STATUS"
    if sample_size < 100:
        return "SHADOW", "INSUFFICIENT_SAMPLE"
    if confidence < 95:
        return "SHADOW", "INSUFFICIENT_CONFIDENCE"
    if improvement_pct is None:
        return "SHADOW", "LIFT_NOT_YET_MEASURABLE"
    if improvement_pct <= 0:
        return "RETIRED", "NO_POSITIVE_LIFT"
    return "PROMOTION_CANDIDATE", "REVIEW_CONTROLLED_VALIDATION"