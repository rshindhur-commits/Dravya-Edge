from app.config.settings import get_bool_env, get_float_env
from app.utils.runtime_logging import debug_print

import pandas as pd


ENTRY_BASE_SCORES = {
    "BREAKDOWN_SHORT": 90,
    "EMA_PULLBACK": 85,
    "BREAKOUT": 80,
    "VWAP_REJECTION": 88,
    "EMA_REJECTION_SHORT": 86
}


def _base_score(setup_type):
    """The pattern's own contribution, and the only thing that separates them.

    `_entry_score` adds the analysis score, a regime bonus, a volume bonus and
    an extension penalty -- all of which read the same `analysis` and the same
    bar for every candidate pattern. So the base score is the *entire*
    difference between two patterns competing on one bar, and `detect_entry`
    keeps the maximum.

    BREAKOUT sits at 80 against EMA_PULLBACK's 85, which means it cannot win
    when both qualify: it loses by a fixed five points on every symbol, in every
    regime, always. Over a 21-day replay that produced 167 EMA_PULLBACK entries
    and 2 BREAKOUT ones, and it is why a stock running 13% in a session never
    triggers -- the one pattern built for that move is outranked by construction.

    Making these tunable does not change the ranking; it makes the ranking
    testable. `ENTRY_BASE_SCORE_BREAKOUT=85` levels it against the pullback so
    a replay can price the difference in dollars, which is the only way to know
    whether the current ordering is deliberate or merely old.
    """

    return get_float_env(
        f"ENTRY_BASE_SCORE_{setup_type}",
        ENTRY_BASE_SCORES.get(setup_type, 70),
    )


# How far price may sit from its references before the setup counts as chased.
#
# These are the boundary of what the app is allowed to trade, and until
# 2026-08-14 they were two bare constants with no way to vary them. That matters
# more than an ordinary threshold, because `avoid_chasing` is a hard refusal in
# `risk_manager.calculate_risk` rather than a score penalty -- so these two
# numbers decide which candidates come into existence, not merely which ones
# rank well.
#
# Defaults are the previous constants exactly, so nothing moves until a variable
# is set.
DEFAULT_MAX_VWAP_DISTANCE_PCT = 1.5
DEFAULT_MAX_EMA_DISTANCE_PCT = 1.2


def max_vwap_distance_pct():

    return get_float_env(
        "AVOID_CHASING_MAX_VWAP_DISTANCE_PCT", DEFAULT_MAX_VWAP_DISTANCE_PCT
    )


def max_ema_distance_pct():

    return get_float_env(
        "AVOID_CHASING_MAX_EMA_DISTANCE_PCT", DEFAULT_MAX_EMA_DISTANCE_PCT
    )


# How close the bar's Low must come to EMA9 to count as a pullback, as a
# multiple of ATR. Named because `entry_diagnostics` reports this rule to the
# dashboard and had its own hardcoded 0.25 against the engine's 0.40 -- so the
# waterfall told an operator a candidate was "not close enough to EMA9" using a
# threshold 37% tighter than the one that actually decided. Two numbers for one
# rule is how a diagnostic starts lying about the engine it describes.
EMA_PULLBACK_ATR_MULTIPLE = 0.40


def require_ema_alignment():
    """Whether EMA9 must have crossed EMA20 before a pullback or rejection counts.

    Both setups already require price to have done the thing: EMA_PULLBACK needs
    Close above EMA9 with the bar's Low touching it, EMA_REJECTION_SHORT needs
    Close below EMA9 after a recent touch from above, and both sit behind a
    directional signal and a VWAP side check. The EMA9/EMA20 cross on top of that
    is a moving average confirming what price has already printed, and averages
    confirm late by construction.

    Measured on NVDA 2026-08-21, which is one instance of a pattern that holds on
    all 32 trades opened between 08-10 and 08-21: at 09:37 the short was complete
    -- bearish signal, close below EMA9, rejection off 218.74, below VWAP -- and
    this condition alone held it out. EMA9 was 217.85 against EMA20 217.73. The
    app looked again at 09:39, 09:41, 09:43 and 09:45 and refused each time. It
    entered at 09:55 once the cross completed, by which point NVDA had fallen from
    217.42 to 216.52 and the put had gone from 7.85 to 8.53. Same exit either way:
    +14.6% against the +5.6% actually booked.

    Default True, so nothing moves until a replay says it should.
    """

    return get_bool_env("ENTRY_REQUIRE_EMA_ALIGNMENT", True)


def _normalized_market_regime(value):

    regime = str(value or "").strip().upper()

    if regime == "TRENDING_BULL":

        return "TRENDING_BULLISH"

    if regime == "TRENDING_BEAR":

        return "TRENDING_BEARISH"

    return regime


def _recent_ema_touch(df, price_column="High", ema_column="EMA9", window=3):

    if df is None or df.empty:

        return False, None

    if price_column not in df.columns or ema_column not in df.columns:

        return False, None

    recent = df.tail(window)

    try:

        touches = recent[price_column] >= recent[ema_column]

        return bool(touches.any()), recent.loc[touches, price_column].max() if touches.any() else recent[price_column].max()

    except Exception:

        return False, None


def _entry_score(setup_type, analysis, latest, avoid_chasing, direction):

    base_score = _base_score(setup_type)

    try:

        analysis_score = abs(float(analysis.get("score", 0)))

    except Exception:

        analysis_score = 0

    market_regime = _normalized_market_regime(
        analysis.get("market_regime")
    )
    market_regime_bonus = 0

    if direction == "CALL" and market_regime == "TRENDING_BULLISH":

        market_regime_bonus = 5

    elif direction == "PUT" and market_regime == "TRENDING_BEARISH":

        market_regime_bonus = 5

    rel_volume = latest.get("REL_VOLUME", 0) or 0
    volume_bonus = 5 if rel_volume > 1.5 else 3 if rel_volume > 1.2 else 0
    extension_penalty = 5 if avoid_chasing else 0

    return base_score + analysis_score + market_regime_bonus + volume_bonus - extension_penalty


def detect_entry(df, analysis, symbol=None):

    debug_print("[ENTRY ENGINE COLUMNS]")
    debug_print(list(df.columns))    
    
    best_score = 0

    latest = df.iloc[-1]
    recent_ema9_touch, recent_ema9_touch_high = _recent_ema_touch(
        df,
        "High",
        "EMA9",
        window=3
    )

    if analysis["signal"] in [
        "NEUTRAL",
        "INVALID"
    ]:

        return {

            "entry_type": "NO_ENTRY",

            "entry_trigger": None,

            "entry_quality": "LOW",

            "avoid_chasing": False,

            "reasons": [
                "Neutral trend environment"
            ]
        }    

    entry_type = "NO_ENTRY"
    entry_trigger = None
    entry_quality = "NONE"
    avoid_chasing = False

    reasons = []

    # =========================
    # Distance Measurements
    # =========================

    vwap_distance = (
        (
            latest["Close"] - latest["VWAP"]
        ) / latest["VWAP"]
    ) * 100

    ema_distance = (
        (
            latest["Close"] - latest["EMA9"]
        ) / latest["EMA9"]
    ) * 100

    # =========================
    # Overextension Filter
    # =========================
    #
    # Measured as distance, not as signed offset. `vwap_distance` and
    # `ema_distance` are positive above the reference and negative below it, so
    # testing `> 1.5` only ever caught extended longs. A PUT setup entered 2%
    # below VWAP -- a chased short -- read as -2.0 and cleared both filters.
    #
    # That mattered more than it looks: `avoid_chasing` is not a score penalty,
    # it is a hard block in risk_manager.calculate_risk(), so shorts were the
    # only direction with no chase protection at all. It compounded with the
    # scoring asymmetry that already favours them (a below-VWAP close feeds both
    # the individual rules and the bearish_strength aggregate in
    # momentum_strategy), and with the timing filter below, which named a setup
    # the entry engine never emits and so only ever filtered BREAKDOWN_SHORT.
    #
    # Thresholds are unchanged; only the direction they can see is.
    #
    # The distances became configurable on 2026-08-14, defaults identical to the
    # constants they replace, because this is the rule that decides *which
    # candidates can exist* and it had never been measured.
    #
    # 1.2% from EMA9 is a narrow band for a liquid megacap in a real trend: on
    # 2026-08-13 MU travelled 5.67% and SMCI 7.33% and neither produced a
    # tradeable candidate, because both left the band within minutes of starting
    # to move. `docs/TRADE_QUALITY_PLAN.md` §2.2a attributes the strategy's
    # sub-percent ceiling to the stop anchor; this sits upstream of that and is
    # the harder constraint, since the anchor shapes a trade that this rule has
    # already refused to allow.
    #
    # Widening these does not merely change which candidates pass -- it changes
    # which candidates are generated at all, so every archived candidate set is
    # incomparable across a change here. See docs/CHANGE_IMPACT_MAP.md §1a.

    if abs(vwap_distance) > max_vwap_distance_pct():

        avoid_chasing = True

        reasons.append(
            "Price extended far above VWAP"
            if vwap_distance > 0
            else "Price extended far below VWAP"
        )

    if abs(ema_distance) > max_ema_distance_pct():

        avoid_chasing = True

        reasons.append(
            "Price extended far above EMA9"
            if ema_distance > 0
            else "Price extended far below EMA9"
        )

    # =========================
    # Breakout Entry
    # =========================

    recent_high = (
        df["High"]
        .rolling(10)
        .max()
        .iloc[-2]
        if len(df) >= 2
        else df["High"].max()
    )

    if pd.isna(recent_high):

        recent_high = df["High"].shift(1).tail(10).max()

    if (
        latest["Close"] > recent_high and latest["REL_VOLUME"] > 1.2
        and analysis["signal"] in [
            "BULLISH",
            "HIGH CONVICTION BULLISH"
        ]
    ):

        setup_score = _entry_score(
            "BREAKOUT",
            analysis,
            latest,
            avoid_chasing,
            "CALL"
        )

        if setup_score > best_score:
            best_score = setup_score

            entry_type = "BREAKOUT"         

            entry_trigger = round(
                recent_high,
                2
            )

            entry_quality = ("MEDIUM" if avoid_chasing else "HIGH")

            reasons.append(
                "Near breakout level"
            )


    # =========================
    # EMA Pullback Continuation
    # =========================

    ema_pullback_threshold = latest.get("ATR", 0) * EMA_PULLBACK_ATR_MULTIPLE
    ema_pullback_low_distance = abs(
        latest["Low"] - latest["EMA9"]
    )

    debug_print(
        f"[EMA_PULLBACK CHECK] "
        f"{symbol or 'UNKNOWN'} "
        f"Signal={analysis['signal']} "
        f"Close>EMA9={latest['Close'] > latest['EMA9']} "
        f"EMA9>EMA20={latest['EMA9'] > latest['EMA20']} "
        f"LowDist={ema_pullback_low_distance:.2f} "
        f"Threshold={ema_pullback_threshold:.2f}"
    )

    if (
        analysis["signal"] in [
            "BULLISH",
            "HIGH CONVICTION BULLISH"
        ]
         and

        latest["Close"] > latest["EMA9"]
        and ema_pullback_low_distance <= ema_pullback_threshold
        and (
            latest["EMA9"] > latest["EMA20"]
            or not require_ema_alignment()
        )

    ):


        setup_score = _entry_score(
            "EMA_PULLBACK",
            analysis,
            latest,
            avoid_chasing,
            "CALL"
        )

        if setup_score > best_score:
            best_score = setup_score  

            entry_type = "EMA_PULLBACK"   

            debug_print(
                "[ENTRY TRIGGERED] EMA_PULLBACK"
            )      

            entry_trigger = round(
                latest["EMA9"],
                2
            )

            entry_quality = ("MEDIUM" if avoid_chasing else "HIGH")

            reasons.append(
                "Bullish EMA continuation"
            )


    # =========================
    # Bearish EMA Rejection
    # =========================

    if (
        analysis["signal"] in [
            "BEARISH",
            "HIGH CONVICTION BEARISH"
        ]
         and

        latest["Close"] < latest["EMA9"]
        and recent_ema9_touch
        and (
            latest["EMA9"] < latest["EMA20"]
            or not require_ema_alignment()
        )

    ):
        
        debug_print(
            f"[EMA REJECTION DEBUG] "
            f"close={latest['Close']:.2f} "
            f"high={latest['High']:.2f} "
            f"recent_touch_high={recent_ema9_touch_high:.2f} "
            f"ema9={latest['EMA9']:.2f} "
            f"ema20={latest['EMA20']:.2f}"
        )        

        setup_score = _entry_score(
            "EMA_REJECTION_SHORT",
            analysis,
            latest,
            avoid_chasing,
            "PUT"
        )

        if setup_score > best_score:

            best_score = setup_score

            entry_type = "EMA_REJECTION_SHORT"

            debug_print(
                "[ENTRY TRIGGERED] EMA_REJECTION_SHORT"
            )

            entry_trigger = round(
                latest["EMA9"],
                2
            )

            entry_quality = ("MEDIUM" if avoid_chasing else "HIGH")

            reasons.append(
                "Bearish EMA rejection"
            )


    debug_print(
        f"[BREAKDOWN CHECK] "
        f"BREAKDOWN={latest['BREAKDOWN']} "
        f"LOWER_HIGH={latest['LOWER_HIGH']} "
        f"CLOSE={latest['Close']} "
        f"VWAP={latest['VWAP']} "
        f"EMA9={latest['EMA9']} "
        f"EMA20={latest['EMA20']}"
    )    

    debug_print(
        f"[BREAKDOWN LEVELS] "
        f"close={latest['Close']} "
        f"support={latest.get('ROLLING_SUPPORT')} "
        f"prev_low={latest.get('PREV_LOW')} "
        f"breakdown={latest['BREAKDOWN']}"
    )    

    if (
        (
            latest.get("BREAKDOWN", False)
            and latest.get("LOWER_HIGH", False)
        )
        and
        latest["Close"] < latest["VWAP"]
        and
        latest["EMA9"] < latest["EMA20"]
        and latest.get("BODY_STRENGTH", 0) > 0.5
        and latest.get("REL_VOLUME", 0) > 1.1
    ):

        recent_low = (
            df["Low"]
            .shift(1)
            .tail(3)
            .min()
        )

        if (latest["Close"] <= recent_low
            or latest["LOWER_HIGH"]
        ):
            
            debug_print(
                f"[BREAKDOWN DEBUG] "
                f"REL_VOL={latest['REL_VOLUME']} "
                f"RECENT_LOW={recent_low} "
                f"CLOSE={latest['Close']} "
                f"LOWER_HIGH={latest['LOWER_HIGH']}"
            )            

            setup_score = _entry_score(
                "BREAKDOWN_SHORT",
                analysis,
                latest,
                avoid_chasing,
                "PUT"
            )
            if setup_score > best_score:
                best_score = setup_score
            
                entry_type = "BREAKDOWN_SHORT"

                debug_print(
                    "[ENTRY TRIGGERED] BREAKDOWN_SHORT"
                )

                debug_print(
                    f"[ENTRY CHECK] "
                    f"REL_VOL={latest['REL_VOLUME']} "
                    f"RECENT_LOW={recent_low} "
                    f"CLOSE={latest['Close']}"
                )

                entry_trigger = round(
                    recent_low,
                    2
                )

                entry_quality = ("MEDIUM" if avoid_chasing else "HIGH")

                reasons.append(
                    "Fresh bearish breakdown"
                )

    # =========================
    # Failed VWAP Reclaim
    # =========================

    if (
        analysis["signal"] in [
            "BEARISH",
            "HIGH CONVICTION BEARISH"
        ] and

        latest["High"] > latest["VWAP"]
        and latest["Close"] < latest["VWAP"]
        and latest["EMA9"] < latest["EMA20"]

    ):        

        setup_score = _entry_score(
            "VWAP_REJECTION",
            analysis,
            latest,
            avoid_chasing,
            "PUT"
        )
        if setup_score > best_score:
            best_score = setup_score  

            entry_type = "VWAP_REJECTION"      

            debug_print(
                "[ENTRY TRIGGERED] VWAP_REJECTION"
            )

            entry_trigger = round(
                latest["VWAP"],
                2
            )

            entry_quality = ("MEDIUM" if avoid_chasing else "HIGH")

            reasons.append(
                "Failed VWAP reclaim rejection"
            )

    debug_print(
        f"[ENTRY SELECTED] "
        f"type={entry_type} "
        f"score={best_score} "
        f"quality={entry_quality}"
    )


    return {

        "entry_type": entry_type,

        "entry_trigger": entry_trigger,

        "entry_quality": entry_quality,

        "avoid_chasing": avoid_chasing,

        "reasons": reasons
    }            