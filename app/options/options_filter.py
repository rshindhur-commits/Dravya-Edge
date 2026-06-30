from app.options.option_metrics import calculate_spread_pct
from app.config.settings import settings


def evaluate_option_liquidity(option_data):

    """
    Basic liquidity filter for options contracts
    """

    try:

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