from app.utils.runtime_logging import debug_print

import pandas as pd


ENTRY_BASE_SCORES = {
    "BREAKDOWN_SHORT": 90,
    "EMA_PULLBACK": 85,
    "BREAKOUT": 80,
    "VWAP_REJECTION": 88,
    "EMA_REJECTION_SHORT": 86
}


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

    base_score = ENTRY_BASE_SCORES.get(setup_type, 70)

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

    if abs(vwap_distance) > 1.5:

        avoid_chasing = True

        reasons.append(
            "Price extended far above VWAP"
            if vwap_distance > 0
            else "Price extended far below VWAP"
        )

    if abs(ema_distance) > 1.2:

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
    # VWAP Reclaim Entry
    # =========================

    # if (

    #     latest["Close"] > latest["VWAP"]
    #     and latest["Open"] < latest["VWAP"]

    # ):

    #     setup_score = 3

    #     if setup_score > best_score:

    #         best_score = setup_score

    #         entry_type = "VWAP_RECLAIM"

    #         entry_trigger = round(
    #             latest["VWAP"],
    #             2
    #         )

    #         entry_quality = "MEDIUM"

    #         reasons.append(
    #             "VWAP reclaim detected"
    #         )

    # =========================
    # EMA Pullback Continuation
    # =========================

    ema_pullback_threshold = latest.get("ATR", 0) * 0.40
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
        and latest["EMA9"] > latest["EMA20"]

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
        and latest["EMA9"] < latest["EMA20"]

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


    # =========================
    # Higher Low Pullback
    # =========================

    # if (

    #     latest.get("HIGHER_LOW", False)
    #     and latest["Close"] > latest["VWAP"]
    #     and latest["EMA9"] > latest["EMA20"]

    # ):        

    #     setup_score = 4
    #     if setup_score > best_score:
    #         best_score = setup_score   

    #         entry_type = "HIGHER_LOW_CONTINUATION"

    #         entry_trigger = round(
    #             latest["EMA9"],
    #             2
    #         )

    #         entry_quality = "HIGH"

    #         reasons.append(
    #             "Higher low continuation setup"
    #         )


    # Breakout entry

    # if (

    #     latest["BREAKOUT"]
    #     and not avoid_chasing

    # ):

    #     setup_score = 3

    #     if setup_score > best_score:

    #         best_score = setup_score

    #         entry_type = "BREAKOUT_CONTINUATION"

    #         entry_quality = "HIGH"

    #         reasons.append(
    #             "Momentum breakout continuation"
    #         )

    # Bullish coiled setup

    # if (

    #     latest["CONSOLIDATING"]
    #     and latest["REL_VOLUME"] > 1.5
    #     and latest["Close"] > latest["VWAP"]
    #     and latest["EMA9"] > latest["EMA20"]

    # ):

    #     setup_score = 3

    #     if setup_score > best_score:

    #         best_score = setup_score

    #         entry_type = "COILED_BREAKOUT"

    #         entry_quality = "HIGH"

    #         reasons.append(
    #             "Bullish coiled breakout"
    #         )

    # # Bearish coiled setup

    # if (

    #     latest["CONSOLIDATING"]
    #     and latest["REL_VOLUME"] > 1.5
    #     and latest["Close"] < latest["VWAP"]
    #     and latest["EMA9"] < latest["EMA20"]

    # ):

    #     setup_score = 3

    #     if setup_score > best_score:

    #         best_score = setup_score

    #         entry_type = "COILED_BREAKDOWN"

    #         entry_quality = "HIGH"

    #         reasons.append(
    #             "Bearish coiled breakdown"
    #         )

    # =========================
    # Bearish Breakdown Trigger
    # =========================

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