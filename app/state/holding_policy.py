from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.gates.setup_quality import MIN_SETUP_MULTIDAY


class HoldingProfile(StrEnum):
    INTRADAY = "INTRADAY"
    MULTIDAY = "MULTIDAY"


@dataclass(frozen=True)
class TradeHoldingPolicy:
    holding_profile: HoldingProfile
    force_eod_exit: bool
    restore_next_session: bool
    telegram_resume: bool
    archive_candidates_eod: bool


# Expirations with enough life left to be worth carrying overnight.
#
# This set previously read {"PREFERRED_14_30", "LONGER_DTE"}, and "LONGER_DTE" is
# not a value classify_expiration_bucket() can return -- the buckets past 30 days
# are FALLBACK_31_45 and LONG_DATED_46_PLUS. The branch was dead, so a 40-day
# contract, which has strictly less theta risk than the 14-30 one beside it, could
# never qualify as MULTIDAY.
#
# SHORT_SWING_7_13 is deliberately excluded: a contract with under two weeks left
# is carried overnight only to pay a full night of theta on the steepest part of
# the curve.
MULTIDAY_EXPIRATION_BUCKETS = {
    "PREFERRED_14_30",
    "FALLBACK_31_45",
    "LONG_DATED_46_PLUS",
}

INTRADAY_POLICY = TradeHoldingPolicy(
    holding_profile=HoldingProfile.INTRADAY,
    force_eod_exit=True,
    restore_next_session=False,
    telegram_resume=False,
    archive_candidates_eod=True,
)

MULTIDAY_POLICY = TradeHoldingPolicy(
    holding_profile=HoldingProfile.MULTIDAY,
    force_eod_exit=False,
    restore_next_session=True,
    telegram_resume=True,
    archive_candidates_eod=False,
)


def holding_policy(value: Any) -> TradeHoldingPolicy:
    try:
        profile = HoldingProfile(str(value or HoldingProfile.INTRADAY).upper())
    except ValueError:
        profile = HoldingProfile.INTRADAY

    return MULTIDAY_POLICY if profile is HoldingProfile.MULTIDAY else INTRADAY_POLICY


def derive_holding_profile(candidate: dict[str, Any] | None) -> HoldingProfile:
    candidate = {} if candidate is None else candidate
    explicit = (
        candidate.get("holding_profile")
        or candidate.get("Holding Profile")
        or candidate.get("position_type")
        or candidate.get("Position Type")
    )
    if explicit:
        return holding_policy(explicit).holding_profile

    expected_hold = str(candidate.get("Expected Hold") or "").upper()
    expiration_bucket = str(candidate.get("Expiration Bucket") or "").upper()
    setup_score = _number(
        candidate.get("Setup %")
        if candidate.get("Setup %") is not None
        else candidate.get("15m Score")
    )
    rr = _number(candidate.get("Candidate RR") or candidate.get("RR"))
    option_quality = _number(candidate.get("Option Quality Score"))

    if (
        "MULTI" in expected_hold
        or "SWING" in expected_hold
        or "OVERNIGHT" in expected_hold
        or (
            expiration_bucket in MULTIDAY_EXPIRATION_BUCKETS
            # On the setup_quality scale. 80 on the old metric passed 3.55% of
            # archived rows; 76 here is the matched equivalent.
            and setup_score >= MIN_SETUP_MULTIDAY
            and rr >= 1.8
            and option_quality >= 75
        )
    ):
        return HoldingProfile.MULTIDAY

    return HoldingProfile.INTRADAY


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0