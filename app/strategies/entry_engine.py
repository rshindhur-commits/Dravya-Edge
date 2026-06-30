from app.utils.runtime_logging import debug_print


def detect_entry(df, analysis):

    debug_print("[ENTRY ENGINE COLUMNS]")
    debug_print(list(df.columns))    
    
    best_score = 0

    latest = df.iloc[-1]

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

    if vwap_distance > 1.5:

        avoid_chasing = True

        reasons.append(
            "Price extended far above VWAP"
        )

    if ema_distance > 1.2:

        avoid_chasing = True

        reasons.append(
            "Price extended far above EMA9"
        )

    # =========================
    # Breakout Entry
    # =========================

    recent_high = (
        df["High"]
        .shift(1)
        .tail(5)
        .max()
    )

    if (
        latest["Close"] > recent_high and latest["REL_VOLUME"] > 1.2
        and analysis["signal"] in [
            "BULLISH",
            "HIGH CONVICTION BULLISH"
        ]
    ):

        setup_score = 4  

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

    if (
        analysis["signal"] in [
            "BULLISH",
            "HIGH CONVICTION BULLISH"
        ]
         and

        latest["Close"] > latest["EMA9"]
        and latest["Low"] <= latest["EMA9"]
        and latest["EMA9"] > latest["EMA20"]

    ):


        setup_score = 4

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
        and latest["High"] >= latest["EMA9"]
        and latest["EMA9"] < latest["EMA20"]

    ):
        
        debug_print(
            f"[EMA REJECTION DEBUG] "
            f"close={latest['Close']:.2f} "
            f"high={latest['High']:.2f} "
            f"ema9={latest['EMA9']:.2f} "
            f"ema20={latest['EMA20']:.2f}"
        )        

        setup_score = 4

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
        f"support={latest['ROLLING_SUPPORT']} "
        f"prev_low={latest['PREV_LOW']} "
        f"breakdown={latest['BREAKDOWN']}"
    )    

    if (
        (
            latest.get("BREAKDOWN", False)
            or
            (
                latest.get("LOWER_HIGH", False)
                and latest["TREND_PHASE"] == "DOWNTREND"
            )    
        )
        and
        latest["Close"] < latest["VWAP"]
        and
        latest["EMA9"] < latest["EMA20"]
        # and latest["REL_VOLUME"] > 1.0        #Enable after market opens
        and latest.get(
            "LOWER_HIGH",
            False
        )
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

            setup_score = 5
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

        setup_score = 5
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