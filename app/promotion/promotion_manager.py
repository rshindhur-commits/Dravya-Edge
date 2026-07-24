from __future__ import annotations


REVIEWABLE_STATUSES = {"PROMOTION_CANDIDATE", "APPROVED_FOR_CONTROLLED_VALIDATION", "REJECTED"}


def review_status(feature, status, reviewer, note=None):
    if status not in REVIEWABLE_STATUSES:
        raise ValueError("Unsupported promotion review status")
    return {"feature_name": feature, "status": status, "reviewer": reviewer, "note": note or "", "automatic_v1_change": False}


def persist_review(feature, status, reviewer, note=None):
    review = review_status(feature, status, reviewer, note)
    from app.db.learning_engine_repository import LearningEngineRepository
    LearningEngineRepository().record_promotion_review(review)
    return review