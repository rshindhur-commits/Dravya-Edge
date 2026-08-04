from app.options.option_metrics import calculate_spread_pct
from app.config.settings import settings
from app.options.affordability_config import get_affordability_config
from app.options.option_affordability import add_affordability_metrics


# What the rejected contract actually was.
#
# Every verdict below said why a contract was refused and none said *which*
# contract or against what number. On 2026-08-03 MSFT produced 29 rejections
# reading "Low open interest" and "Wide bid/ask spread" with the ticker, the
# open interest and the threshold all absent, so the only question that matters
# -- is 500 too high, or is the selector reaching for an illiquid strike? --
# could not be answered from the record at all.
#
# Merged in one place rather than added to a dozen return sites, so a verdict
# added later cannot forget to carry it.
_REJECTION_EVIDENCE_FIELDS = (
    "ticker",
    "strike",
    "expiration",
    "dte",
    "open_interest",
    "volume",
    "bid",
    "ask",
    "mid_price",
    "delta",
    "iv",
    "contract_cost",
    "quote_status",
    "quote_freshness",
    "expiration_bucket",
)


# What a caller recording an attempt should carry off the verdict. Same fields
# plus the threshold, so the audit trail and the verdict cannot drift apart.
ATTEMPT_EVIDENCE_FIELDS = _REJECTION_EVIDENCE_FIELDS + ("required_value",)


def _rejection_evidence(option_data):

    option_data = option_data or {}
    evidence = {}

    for field in _REJECTION_EVIDENCE_FIELDS:

        value = option_data.get(field)

        if value is not None and str(value).strip() not in {"", "nan", "None"}:

            evidence[field] = value

    return evidence


def _required_value(code):
    """The threshold this code was measured against, where there is one."""

    return {
        "LOW_OPEN_INTEREST": settings.option_min_open_interest,
        "LOW_VOLUME": settings.option_min_volume,
        "WIDE_SPREAD": settings.option_max_spread_pct,
        "LOW_OPTION_QUALITY": settings.option_min_quality_score,
    }.get(str(code or "").upper())


def evaluate_option_liquidity(option_data):
    """Liquidity verdict, with the contract and threshold behind it attached."""

    result = _evaluate_option_liquidity(option_data)

    if result.get("liquid"):

        return result

    # Evidence never overwrites a verdict's own fields: `spread_pct` is rounded
    # deliberately by some branches, and the raw quote value must not replace it.
    for field, value in _rejection_evidence(option_data).items():

        result.setdefault(field, value)

    required = _required_value(result.get("code"))

    if required is not None:

        result.setdefault("required_value", required)

    return result


def _evaluate_option_liquidity(option_data):

    """
    Basic liquidity filter for options contracts
    """

    try:

        affordability_config = get_affordability_config()
        add_affordability_metrics(
            option_data,
            config=affordability_config
        )

        open_interest = option_data.get(
            "open_interest",
            0
        )

        volume = option_data.get(
            "volume",
            0
        )

        bid = option_data.get(
            "bid",
            0
        )

        ask = option_data.get(
            "ask",
            0
        )

        quote_status = option_data.get(
            "quote_status",
            "UNKNOWN"
        )

        quote_status_reason = option_data.get(
            "quote_status_reason",
            "Option quote unavailable"
        )

        quote_freshness = option_data.get(
            "quote_freshness"
        )

        quote_timeframe = option_data.get(
            "quote_timeframe"
        )

        expiration_bucket = option_data.get(
            "expiration_bucket",
            "UNKNOWN"
        )

        option_quality_score = option_data.get(
            "option_quality_score",
            0
        )

        option_quality_reasons = option_data.get(
            "option_quality_reasons",
            "Option quality failed"
        )

        if settings.option_require_bid_ask and (bid <= 0 or ask <= 0):

            return {

                "liquid": False,

                "code": "MISSING_BID_ASK",

                "reason": "Missing live bid/ask quote",

                "spread_pct": option_data.get("spread_pct"),

                "quality_score": option_quality_score,

                "liquidity_grade": option_data.get(
                    "option_liquidity_grade"
                )
            }

        if quote_timeframe == "DELAYED":

            return {

                "liquid": False,

                "code": "DELAYED_QUOTE",

                "reason": "Option quote timeframe is delayed",

                "spread_pct": option_data.get("spread_pct"),

                "quality_score": option_quality_score,

                "liquidity_grade": option_data.get(
                    "option_liquidity_grade"
                )
            }

        if settings.option_require_fresh_quote and quote_freshness != "LIVE_QUOTE":

            return {

                "liquid": False,

                "code": quote_freshness or "UNKNOWN_QUOTE_TIME",

                "reason": "Option quote is not fresh real-time data",

                "spread_pct": option_data.get("spread_pct"),

                "quality_score": option_quality_score,

                "liquidity_grade": option_data.get(
                    "option_liquidity_grade"
                )
            }

        if quote_status in [
            "STALE_QUOTE",
            "DELAYED_QUOTE"
        ] or quote_freshness in [
            "STALE_QUOTE",
            "DELAYED_QUOTE"
        ]:

            code = (
                quote_status
                if quote_status in [
                    "STALE_QUOTE",
                    "DELAYED_QUOTE"
                ]
                else quote_freshness
            )

            reason = (
                "Option quote is delayed; confirm broker premium"
                if code == "DELAYED_QUOTE"
                else "Option quote is too stale for intraday execution"
            )

            return {

                "liquid": False,

                "code": code,

                "reason": reason or quote_status_reason,

                "spread_pct": option_data.get("spread_pct"),

                "quality_score": option_quality_score,

                "liquidity_grade": option_data.get(
                    "option_liquidity_grade"
                )
            }

        # Avoid division errors
        if bid <= 0 or ask <= 0:

            if quote_status in [
                "NO_BID_ASK",
                "OPTION_MARKET_CLOSED",
                "RATE_LIMITED",
                "PROVIDER_ERROR",
                "NO_QUOTE_SNAPSHOT",
                "DELAYED_QUOTE",
                "STALE_QUOTE",
                "QUOTE_UNAVAILABLE"
            ]:

                return {

                    "liquid": False,

                    "code": quote_status,

                    "reason": quote_status_reason
                }

            return {

                "liquid": False,

                "code": "INVALID_BID_ASK",

                "reason": "Invalid bid/ask"
            }

        if ask < bid:

            return {

                "liquid": False,

                "code": "CROSSED_MARKET",

                "reason": "Invalid crossed bid/ask"
            }

        spread_pct = calculate_spread_pct(
            bid,
            ask
        )

        if spread_pct is None:

            return {

                "liquid": False,

                "code": "INVALID_SPREAD",

                "reason": "Invalid bid/ask spread"
            }

        # =====================================
        # Liquidity Rules
        # =====================================

        if open_interest < settings.option_min_open_interest:

            return {

                "liquid": False,

                "code": "LOW_OPEN_INTEREST",

                "reason": "Low open interest",

                "spread_pct": spread_pct,

                "quality_score": option_quality_score
            }

        if volume < settings.option_min_volume:

            return {

                "liquid": False,

                "code": "LOW_VOLUME",

                "reason": "Low option volume",

                "spread_pct": spread_pct,

                "quality_score": option_quality_score
            }

        if spread_pct > settings.option_max_spread_pct:

            return {

                "liquid": False,

                "code": "WIDE_SPREAD",

                "reason": "Wide bid/ask spread",

                "spread_pct": spread_pct,

                "quality_score": option_quality_score
            }

        if expiration_bucket == "0DTE" and not settings.option_allow_0dte:

            return {

                "liquid": False,

                "code": "EXPIRATION_0DTE_BLOCKED",

                "reason": "0DTE contracts disabled by risk settings",

                "spread_pct": spread_pct,

                "quality_score": option_quality_score
            }

        if expiration_bucket == "1DTE" and not settings.option_allow_1dte:

            return {

                "liquid": False,

                "code": "EXPIRATION_1DTE_BLOCKED",

                "reason": "1DTE contracts disabled by risk settings",

                "spread_pct": spread_pct,

                "quality_score": option_quality_score
            }

        if option_quality_score < settings.option_min_quality_score:

            return {

                "liquid": False,

                "code": "LOW_OPTION_QUALITY",

                "reason": option_quality_reasons,

                "spread_pct": spread_pct,

                "quality_score": option_quality_score,

                "liquidity_grade": option_data.get(
                    "option_liquidity_grade"
                )
            }

        if (
            affordability_config.get("mode") == "HARD"
            and not option_data.get("affordable", False)
        ):

            return {

                "liquid": False,

                "code": option_data.get(
                    "affordability_status",
                    "OPTION_TOO_EXPENSIVE"
                ),

                "reason": "Option contract cost exceeds capital profile",

                "spread_pct": spread_pct,

                "quality_score": option_quality_score,

                "liquidity_grade": option_data.get(
                    "option_liquidity_grade"
                ),

                "contract_cost": option_data.get("contract_cost"),

                "risk_at_stop": option_data.get("risk_at_stop"),

                "max_allowed_contract_cost": option_data.get(
                    "max_allowed_contract_cost"
                )
            }

        return {

            "liquid": True,

            "code": "LIQUID",

            "reason": "Healthy liquidity",

            "spread_pct": round(
                spread_pct,
                2
            ),

            "quality_score": option_quality_score,

            "liquidity_grade": option_data.get(
                "option_liquidity_grade"
            )
        }

    except Exception as e:

        return {

            "liquid": False,

            "code": "LIQUIDITY_ERROR",

            "reason": str(e)
        }


def build_option_rule_evaluations(option_data, liquidity_result, scan_id, symbol, setup=None):
    from app.gates.rule_evaluation import RuleEvaluation

    option_data = option_data or {}
    result = liquidity_result or {}
    quality = option_data.get("option_quality_score", 0)
    spread = result.get("spread_pct", option_data.get("spread_pct"))
    fresh = option_data.get("quote_freshness") == "LIVE_QUOTE"
    liquid = bool(result.get("liquid"))
    rules = [
        RuleEvaluation(scan_id, symbol, setup, "Option Quality", "Option", quality, settings.option_min_quality_score, quality >= settings.option_min_quality_score, quality < settings.option_min_quality_score, 80),
        RuleEvaluation(scan_id, symbol, setup, "Quote Freshness", "Realtime", option_data.get("quote_freshness"), "LIVE_QUOTE", fresh, not fresh, 80),
        RuleEvaluation(scan_id, symbol, setup, "Option Liquidity", "Option", result.get("code"), "LIQUID", liquid, not liquid, 75),
    ]
    if spread is not None:
        rules.append(RuleEvaluation(scan_id, symbol, setup, "Option Spread", "Option", spread, settings.option_max_spread_pct, float(spread) <= settings.option_max_spread_pct, float(spread) > settings.option_max_spread_pct, 70))
    return rules