"""Rank candidates on what the trade is worth, not only on how good it looks.

The scoring here was blind to the one thing that decides how much money a correct
call actually makes: how much premium you have to pay per dollar of underlying.
2026-08-03 is the worked example. ORCL and SMCI both went the right way; ORCL
booked +2.41R and +26.4% of premium, SMCI booked +1.0R and +5.1%. Same ranking
inputs, four times the return, because ORCL's contract cost 2.6% of notional and
SMCI's cost 9.5%.

R cannot see this -- it is computed entirely on the underlying -- and neither
could this file, so a $28 name with rich options ranked level with a $140 name
with cheap ones.
"""

from __future__ import annotations

import os

import pandas as pd


# Premium as a percentage of notional, mapped to 0-100.
#
# Measured, not assumed: 77 archived candidates carrying both a premium and an
# entry price, 2026-07-31 through 08-03.
#
#   min 1.21 | p10 1.59 | p25 1.68 | median 3.41 | p75 9.51 | p90 10.64 | max 10.85
#
# The distribution is bimodal by underlying price rather than continuous. Median
# premium as % of notional, with the implied elasticity (delta x spot / premium)
# beside it:
#
#   AAPL 1.43 (23.4x) | TSLA 1.52 (23.7x) | AMZN 1.67 (20.8x) | NVDA 2.18 (24.3x)
#   ORCL 2.63 (13.1x) | XOM 2.68 (15.8x)  | NFLX 3.41 (15.5x) | SMCI 9.51 (5.7x)
#
# So 2% is where the cheap cluster sits and 10% is the expensive tail. Full marks
# at or below the first, nothing at or above the second.
LEVERAGE_BEST_PREMIUM_PCT = 2.0
LEVERAGE_WORST_PREMIUM_PCT = 10.0

# The contract-economics share of the score: option quality 0.15 + liquidity 0.10
# before this change. Leverage is funded out of that budget rather than added on
# top, so the market-read components (setup, timing, trend, relative strength)
# keep exactly the influence they had and the weights still sum to 1.0.
CONTRACT_SCORE_BUDGET = 0.25
DEFAULT_LEVERAGE_WEIGHT = 0.10


def _leverage_weight():
    """How much of the contract budget leverage takes. 0 restores the old weights.

    Env-tunable because this is the first change to the ranking in a while and it
    reorders candidates rather than filtering them -- there is no way to observe
    it without letting it run, and no way to undo it mid-session except a dial.
    At 0 the arithmetic collapses to exactly what shipped before.
    """

    try:
        weight = float(os.getenv("RANK_LEVERAGE_WEIGHT", DEFAULT_LEVERAGE_WEIGHT))

    except (TypeError, ValueError):
        return DEFAULT_LEVERAGE_WEIGHT

    if weight != weight:
        return DEFAULT_LEVERAGE_WEIGHT

    return max(0.0, min(CONTRACT_SCORE_BUDGET, weight))


def premium_pct_of_notional(row):
    """The option's premium as a percentage of the underlying it controls.

    Both legs are quoted per share, so the 100x contract multiplier cancels and
    does not appear. None when either side is missing -- an unpriced contract is a
    data gap, and scoring it as expensive would penalise a candidate for the quote
    feed rather than for its economics.
    """

    premium = _number(
        row.get("Option Mid Price")
        if row.get("Option Mid Price") is not None
        else row.get("Option Midpoint"),
        None,
    )
    underlying = _number(
        row.get("Candidate Entry Price")
        if row.get("Candidate Entry Price") is not None
        else row.get("Current Price"),
        None,
    )

    if not premium or not underlying or premium <= 0 or underlying <= 0:
        return None

    return premium / underlying * 100


def _leverage_score(row):
    """0-100, high meaning more underlying controlled per premium dollar.

    Deliberately scored on premium/notional rather than on elasticity
    (delta x spot / premium), which is the same quantity refined by delta. The
    contract ranker already bands delta to 0.25-0.75 and penalises distance from
    0.55 at 100 points per unit, so delta is near-constant across selected
    contracts and the two measures rank identically. Were that band ever widened,
    premium/notional alone would start rewarding far-OTM lottery tickets and this
    would need to become elasticity.

    50 -- neutral, neither rewarded nor punished -- when the premium is unknown,
    matching how _liquidity_score treats a missing spread.
    """

    premium_pct = premium_pct_of_notional(row)

    if premium_pct is None:
        return 50.0

    span = LEVERAGE_WORST_PREMIUM_PCT - LEVERAGE_BEST_PREMIUM_PCT
    scaled = (LEVERAGE_WORST_PREMIUM_PCT - premium_pct) / span * 100

    return max(0.0, min(100.0, scaled))


def _number(value, default=0.0):

    try:

        numeric = float(value)

        return numeric if pd.notna(numeric) else default

    except (TypeError, ValueError):

        return default


def _scale_relative_strength(value):

    return max(0, min(100, 50 + _number(value) * 10))


def _trend_health_score(row):

    value = str(
        row.get("V2 Trend Health Status")
        or row.get("Trend Health State")
        or ""
    ).upper()
    return {
        "STRONG": 100,
        "HEALTHY": 75,
        "WEAKENING": 45,
        "BROKEN": 0,
    }.get(value, 50)


def _liquidity_score(row):

    passed = str(row.get("Option Liquidity Passed") or "").lower()

    if passed in {"true", "1", "yes"}:

        return 100

    spread = _number(row.get("Option Spread %"), None)

    if spread is None:

        return 50

    return max(0, min(100, 100 - spread * 10))


def _score_row(row):

    setup = max(0, min(100, _number(
        row.get("Setup %", row.get("15m Score"))
    )))
    timing = max(0, min(100, _number(row.get("Entry Timing Score"))))
    trend = _trend_health_score(row)
    option = max(0, min(100, _number(row.get("Option Quality Score"))))
    relative_strength = _scale_relative_strength(row.get("RS Rank Score"))
    liquidity = _liquidity_score(row)
    leverage = _leverage_score(row)
    entry_priority = _number(row.get("Entry Priority Adjustment"))

    # Leverage is funded from the contract budget, not added to the total. The
    # remaining share is split between option quality and liquidity in their
    # original 0.15:0.10 proportion, so RANK_LEVERAGE_WEIGHT=0 reproduces the
    # previous weights exactly and any other value still sums to 1.0.
    leverage_weight = _leverage_weight()
    contract_remainder = 1 - leverage_weight / CONTRACT_SCORE_BUDGET
    option_weight = 0.15 * contract_remainder
    liquidity_weight = 0.10 * contract_remainder

    score = round(
        setup * 0.25
        + timing * 0.20
        + trend * 0.20
        + relative_strength * 0.10
        + option * option_weight
        + liquidity * liquidity_weight
        + leverage * leverage_weight,
        2
    )
    ranking_score = round(score + entry_priority, 2)
    components = {
        "setup": round(setup, 1),
        "timing": round(timing, 1),
        "trend": round(trend, 1),
        "option": round(option, 1),
        "relative_strength": round(relative_strength, 1),
        "liquidity": round(liquidity, 1),
        "leverage": round(leverage, 1),
    }
    leaders = sorted(
        components.items(),
        key=lambda item: item[1],
        reverse=True
    )[:3]
    return score, ranking_score, "; ".join(
        f"{name}={value:g}" for name, value in leaders
    )


def rank_candidates(frame):
    """Add observational TQS, rank, and explainable component leaders."""

    if frame is None or frame.empty:

        return frame

    ranked = frame.copy()
    scored = ranked.apply(_score_row, axis=1)
    ranked["Trade Quality Score"] = scored.map(lambda item: item[0])
    ranked["Ranking Score"] = scored.map(lambda item: item[1])
    ranked["Rank Reason"] = scored.map(lambda item: item[2])
    # Published as its own column, not only folded into the score. It is the
    # number that explains why two trades with the same R returned different
    # money, and nothing downstream could recover it from the score.
    # to_numeric first: an all-unpriced frame yields an object-dtype Series of
    # None, which .round() rejects outright rather than leaving alone.
    ranked["Option Premium % of Notional"] = pd.to_numeric(
        ranked.apply(premium_pct_of_notional, axis=1),
        errors="coerce",
    ).round(2)
    ranked["Candidate Rank"] = (
        ranked["Ranking Score"]
        .rank(method="first", ascending=False)
        .astype(int)
    )
    return ranked