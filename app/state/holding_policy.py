from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


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
            expiration_bucket in {"PREFERRED_14_30", "LONGER_DTE"}
            and setup_score >= 80
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