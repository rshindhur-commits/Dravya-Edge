import pandas as pd


def normalize_trade_direction(trade_direction):

    direction = str(
        trade_direction or ""
    ).upper()

    if "BULLISH" in direction:

        return "BULLISH"

    if "BEARISH" in direction:

        return "BEARISH"

    return "UNKNOWN"


def evaluate_trade_outcome(

    symbol,

    trade_direction,

    entry_price,

    target_price,

    stop_price,

    future_df

):

    """
    Evaluate whether
    target or stop
    was hit first
    """

    try:

        if future_df.empty:

            return {

                "outcome": "UNKNOWN",

                "bars_processed": 0

            }

        bars_processed = 0

        initial_risk = abs(
            entry_price - stop_price
        )

        max_favorable = 0
        max_adverse = 0

        for _, row in future_df.iterrows():

            high = row["high"]
            low = row["low"]

            bars_processed += 1

            # ==========================
            # Favorable / adverse tracking
            # ==========================

            normalized_direction = normalize_trade_direction(
                trade_direction
            )

            is_bullish = (
                normalized_direction == "BULLISH"
            )

            is_bearish = (
                normalized_direction == "BEARISH"
            )

            if is_bullish:

                favorable_move = (
                    high - entry_price
                )

                adverse_move = (
                    entry_price - low
                )

            elif is_bearish:

                favorable_move = (
                    entry_price - low
                )

                adverse_move = (
                    high - entry_price
                )

            else:

                favorable_move = 0
                adverse_move = 0

            max_favorable = max(
                max_favorable,
                favorable_move
            )

            max_adverse = max(
                max_adverse,
                adverse_move
            )

            # ==========================
            # BULLISH
            # ==========================

            # is_bullish = "BULLISH" in str(trade_direction).upper()
            # is_bearish = "BEARISH" in str(trade_direction).upper()

            if is_bullish:

                if high >= target_price:

                    r_multiple = (
                        abs(target_price - entry_price)
                        / initial_risk
                    )

                    return {

                        "outcome": "TARGET_HIT",

                        "bars_processed":
                            bars_processed,

                        "r_multiple":
                            round(r_multiple, 2),

                        "mae":
                            round(max_adverse, 2),

                        "mfe":
                            round(max_favorable, 2)

                    }

                if bars_processed > 2 and low <= stop_price:

                    return {

                        "outcome": "STOP_HIT",

                        "bars_processed":
                            bars_processed,

                        "r_multiple": -1.0,

                        "mae":
                            round(max_adverse, 2),

                        "mfe":
                            round(max_favorable, 2)

                    }

            # ==========================
            # BEARISH
            # ==========================

            elif is_bearish:

                if low <= target_price:

                    r_multiple = (
                        abs(target_price - entry_price)
                        / initial_risk
                    )

                    return {

                        "outcome": "TARGET_HIT",

                        "bars_processed":
                            bars_processed,

                        "r_multiple":
                            round(r_multiple, 2),

                        "mae":
                            round(max_adverse, 2),

                        "mfe":
                            round(max_favorable, 2)

                    }

                if bars_processed > 2 and high >= stop_price:

                    return {

                        "outcome": "STOP_HIT",

                        "bars_processed":
                            bars_processed,

                        "r_multiple": -1.0,

                        "mae":
                            round(max_adverse, 2),

                        "mfe":
                            round(max_favorable, 2)

                    }

        return {

            "outcome": "NO_HIT",

            "bars_processed":
                bars_processed,

            "r_multiple": 0,

            "mae":
                round(max_adverse, 2),

            "mfe":
                round(max_favorable, 2)

        }

    except Exception as e:

        print(
            f"[OUTCOME ERROR] {e}"
        )

        return {

            "outcome": "ERROR",

            "bars_processed": 0

        }