from datetime import datetime, timezone


def safe_float(value, default=None):

    try:

        if value is None:

            return default

        return float(value)

    except Exception:

        return default


def calculate_option_mid_price(bid, ask, fallback=None):

    bid = safe_float(bid)
    ask = safe_float(ask)

    if bid is not None and ask is not None and bid > 0 and ask > 0:

        return round(
            (bid + ask) / 2,
            4
        )

    fallback = safe_float(fallback)

    if fallback is not None and fallback > 0:

        return round(fallback, 4)

    return None


def calculate_spread_pct(bid, ask):

    bid = safe_float(bid)
    ask = safe_float(ask)

    if bid is None or ask is None or bid <= 0 or ask <= 0:

        return None

    if ask < bid:

        return None

    return round(
        ((ask - bid) / ask) * 100,
        2
    )


def calculate_dte(expiration):

    if not expiration:

        return None

    try:

        expiry_date = datetime.strptime(
            str(expiration),
            "%Y-%m-%d"
        ).date()

        return max(
            (expiry_date - datetime.now().date()).days,
            0
        )

    except Exception:

        return None


def classify_expiration_risk(dte, theta=None):

    if dte is None:

        return "UNKNOWN"

    theta = abs(
        safe_float(theta, 0) or 0
    )

    if dte <= 1:

        return "EXTREME_0DTE_1DTE"

    if dte <= 6:

        return "HIGH_THETA_DECAY"

    if dte <= 13:

        return "SHORT_SWING_THETA_RISK"

    if 14 <= dte <= 30:

        return "PREFERRED_SWING_WINDOW"

    if dte <= 45:

        return "ACCEPTABLE_FALLBACK"

    return "LONG_DATED"


def classify_expiration_bucket(dte):

    if dte is None:

        return "UNKNOWN"

    if dte == 0:

        return "0DTE"

    if dte == 1:

        return "1DTE"

    if dte <= 6:

        return "SHORT_DTE_2_6"

    if dte <= 13:

        return "SHORT_SWING_7_13"

    if dte <= 30:

        return "PREFERRED_14_30"

    if dte <= 45:

        return "FALLBACK_31_45"

    return "LONG_DATED_46_PLUS"


def parse_quote_timestamp(value):

    if value in [None, ""]:

        return None

    try:

        if isinstance(value, str) and not value.isdigit():

            parsed = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )

            if parsed.tzinfo is None:

                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.astimezone(timezone.utc)

        timestamp = int(value)

        if timestamp > 10**17:

            seconds = timestamp / 1_000_000_000

        elif timestamp > 10**14:

            seconds = timestamp / 1_000_000

        elif timestamp > 10**11:

            seconds = timestamp / 1_000

        else:

            seconds = timestamp

        return datetime.fromtimestamp(
            seconds,
            tz=timezone.utc
        )

    except Exception:

        return None


def classify_quote_freshness(
    quote_time,
    delayed_minutes=10,
    max_quote_age_minutes=30,
    now_utc=None,
):

    parsed = parse_quote_timestamp(quote_time)
    checked_at = now_utc or datetime.now(timezone.utc)
    allowed_age_seconds = round(float(max_quote_age_minutes) * 60, 2)

    if parsed is None:

        return {
            "quote_time_utc": None,
            "quote_checked_at_utc": checked_at.isoformat(),
            "quote_age_minutes": None,
            "quote_age_seconds": None,
            "quote_allowed_age_seconds": allowed_age_seconds,
            "quote_freshness_reason": "QUOTE_TIMESTAMP_UNPARSEABLE",
            "quote_freshness": "UNKNOWN_QUOTE_TIME"
        }

    age_seconds = round((checked_at - parsed).total_seconds(), 2)
    age_minutes = round(age_seconds / 60, 2)

    if age_minutes < 0:

        freshness = "LIVE_QUOTE"
        reason = "QUOTE_TIMESTAMP_IN_FUTURE"

    elif age_minutes > max_quote_age_minutes:

        freshness = "STALE_QUOTE"
        reason = "AGE_EXCEEDS_ALLOWED_AGE"

    elif age_minutes >= delayed_minutes:

        freshness = "DELAYED_QUOTE"
        reason = "AGE_AT_OR_ABOVE_DELAY_THRESHOLD"

    else:

        freshness = "LIVE_QUOTE"
        reason = "AGE_WITHIN_ALLOWED_AGE"

    return {
        "quote_time_utc": parsed.isoformat(),
        "quote_checked_at_utc": checked_at.isoformat(),
        "quote_age_minutes": age_minutes,
        "quote_age_seconds": age_seconds,
        "quote_allowed_age_seconds": allowed_age_seconds,
        "quote_freshness_reason": reason,
        "quote_freshness": freshness
    }


def score_option_quality(
    option_data,
    min_volume=100,
    min_open_interest=500,
    max_spread_pct=10,
    allow_0dte=False,
    allow_1dte=False,
    min_dte=10,
    preferred_min_dte=14,
    preferred_max_dte=30,
    max_dte=45
):

    score = 100
    reasons = []

    spread_pct = safe_float(
        option_data.get("spread_pct")
    )
    volume = safe_float(
        option_data.get("volume"),
        0
    ) or 0
    open_interest = safe_float(
        option_data.get("open_interest"),
        0
    ) or 0
    dte = option_data.get("dte")
    theta = abs(
        safe_float(option_data.get("theta"), 0) or 0
    )
    quote_freshness = option_data.get(
        "quote_freshness",
        "UNKNOWN_QUOTE_TIME"
    )
    expiration_bucket = option_data.get(
        "expiration_bucket",
        classify_expiration_bucket(dte)
    )

    if spread_pct is None:

        score -= 35
        reasons.append("missing spread")

    elif spread_pct > max_spread_pct:

        score -= 35
        reasons.append("wide spread")

    elif spread_pct > max_spread_pct * 0.7:

        score -= 15
        reasons.append("borderline spread")

    if volume < min_volume:

        score -= 25
        reasons.append("low volume")

    if open_interest < min_open_interest:

        score -= 20
        reasons.append("low open interest")

    if quote_freshness == "STALE_QUOTE":

        score -= 35
        reasons.append("stale quote")

    elif quote_freshness == "DELAYED_QUOTE":

        score -= 20
        reasons.append("delayed quote")

    elif quote_freshness == "UNKNOWN_QUOTE_TIME":

        score -= 10
        reasons.append("unknown quote time")

    if expiration_bucket == "0DTE" and not allow_0dte:

        score -= 40
        reasons.append("0DTE blocked")

    elif expiration_bucket == "1DTE" and not allow_1dte:

        score -= 30
        reasons.append("1DTE blocked")

    elif dte is not None and dte < min_dte:

        score -= 25
        reasons.append("below preferred minimum DTE")

    elif (
        dte is not None
        and preferred_min_dte <= dte <= preferred_max_dte
    ):

        score += 10
        reasons.append("preferred DTE window")

    elif dte is not None and dte > max_dte:

        score -= 15
        reasons.append("beyond configured max DTE")

    elif expiration_bucket == "SHORT_DTE_2_6" and theta >= 0.2:

        score -= 15
        reasons.append("short-DTE theta risk")

    if theta >= 0.3:

        score -= 15
        reasons.append("high theta")

    score = max(0, min(100, round(score)))

    if score >= 85:

        grade = "A"

    elif score >= 75:

        grade = "B"

    elif score >= 65:

        grade = "C"

    elif score >= 50:

        grade = "D"

    else:

        grade = "F"

    return {
        "option_quality_score": score,
        "option_liquidity_grade": grade,
        "option_quality_reasons": "; ".join(reasons) or "passes configured gates"
    }


def enrich_option_metrics(
    option_data,
    delayed_minutes=10,
    max_quote_age_minutes=30,
    min_volume=100,
    min_open_interest=500,
    max_spread_pct=10,
    allow_0dte=False,
    allow_1dte=False,
    min_dte=10,
    preferred_min_dte=14,
    preferred_max_dte=30,
    max_dte=45
):

    if not option_data:

        return option_data

    option_data = dict(option_data)

    option_data["mid_price"] = calculate_option_mid_price(
        option_data.get("bid"),
        option_data.get("ask"),
        option_data.get("quote_midpoint")
        or option_data.get("close")
    )

    option_data["spread_pct"] = calculate_spread_pct(
        option_data.get("bid"),
        option_data.get("ask")
    )

    dte = option_data.get("dte")

    if dte is None:

        dte = calculate_dte(
            option_data.get("expiration")
        )

    option_data["dte"] = dte
    option_data["expiration_bucket"] = classify_expiration_bucket(
        dte
    )
    option_data["expiration_risk"] = classify_expiration_risk(
        dte,
        option_data.get("theta")
    )
    option_data.update(
        classify_quote_freshness(
            option_data.get("quote_time"),
            delayed_minutes=delayed_minutes,
            max_quote_age_minutes=max_quote_age_minutes
        )
    )
    option_data.update(
        score_option_quality(
            option_data,
            min_volume=min_volume,
            min_open_interest=min_open_interest,
            max_spread_pct=max_spread_pct,
            allow_0dte=allow_0dte,
            allow_1dte=allow_1dte,
            min_dte=min_dte,
            preferred_min_dte=preferred_min_dte,
            preferred_max_dte=preferred_max_dte,
            max_dte=max_dte
        )
    )

    return option_data


def calculate_option_pl(entry_mid, current_mid, contracts=1):

    entry_mid = safe_float(entry_mid)
    current_mid = safe_float(current_mid)

    contracts = safe_float(contracts, 1) or 1

    if entry_mid is None or current_mid is None or entry_mid <= 0:

        return {
            "option_pl_pct": None,
            "option_pl_dollars": None
        }

    pl_pct = round(
        ((current_mid - entry_mid) / entry_mid) * 100,
        2
    )

    pl_dollars = round(
        (current_mid - entry_mid) * contracts * 100,
        2
    )

    return {
        "option_pl_pct": pl_pct,
        "option_pl_dollars": pl_dollars
    }