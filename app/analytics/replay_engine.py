import pandas as pd

from app.analytics.trade_outcome_tracker import (
    evaluate_trade_outcome
)
from app.utils.runtime_logging import debug_print


def replay_trade_projection(

    symbol,

    df,

    projection,

    final_signal

):
    
    debug_print("REPLAY ENGINE VERSION 2 LOADED")

    """
    Replay future candles
    after trade generation
    """

    try:

        if (
            projection is None
            or final_signal == "NEUTRAL"
        ):

            return None

        # minimum_required_bars = max(
        #     lookahead_bars + confirmation_delay + 5,
        #     25
        # )

        # if len(df) < minimum_required_bars:

        #     print(
        #         f"[REPLAY SKIP] "
        #         f"{symbol} "
        #         f"df_len={len(df)} "
        #         f"required={minimum_required_bars}"
        #     )            

        #     return None

        # =====================================
        # Normalize polygon columns
        # =====================================

        df = df.rename(

            columns={

                "o": "open",

                "h": "high",

                "l": "low",

                "c": "close"

            }

        )

        # =====================================
        # Standardize column casing
        # =====================================

        df.columns = [

            col.lower()

            for col in df.columns

        ]        

        debug_print(
            f"[REPLAY COLUMNS] "
            f"{list(df.columns)}"
        )

        # =====================================
        # Dynamic replay horizon
        # =====================================

        market_regime = projection.get(
            "market_regime",
            "CHOPPY"
        )

        lookahead_bars = min(
            20,
            len(df) - 3
        )

        confirmation_delay = 2

        entry_index = max(
            0,
            len(df) - confirmation_delay - 1
        )

        confirmation_delay = 2

        minimum_required_bars = 10

        if len(df) < minimum_required_bars:

            print(
                f"[REPLAY SKIP] "
                f"{symbol} "
                f"df_len={len(df)}"
            )

            return None


        entry_price = (
            df.iloc[entry_index]["close"]
        )

        start_index = (
            entry_index
            + confirmation_delay
        )

        future_df = df.iloc[start_index:]

        future_candles = len(future_df)

        if future_candles < 10:

            print(
                f"[REPLAY SKIPPED] "
                f"{symbol} "
                f"insufficient future candles="
                f"{future_candles}"
            )

            return None

        debug_print(
            f"[REPLAY DEBUG] "
            f"{symbol} "
            f"regime={market_regime} "
            f"lookahead={lookahead_bars} "
            f"entry_index={entry_index} "
            f"start_index={start_index} "
            f"delay={confirmation_delay} "
            f"future candles="
            f"{len(future_df)}"
        )

        result = evaluate_trade_outcome(

            symbol=symbol,

            trade_direction=
                final_signal,

            entry_price=
                entry_price,

            target_price=
                projection[
                    "target_price"
                ],

            stop_price=
                projection[
                    "stop_price"
                ],

            future_df=future_df

        )

        debug_print(
            f"[REPLAY RESULT] "
            f"{symbol} → "
            f"{result}"
        )

        if result is not None:

            result["market_regime"] = (
                market_regime
            )

            result["lookahead_bars"] = (
                lookahead_bars
            )

            result["confirmation_delay"] = (
                confirmation_delay
            )

        return result

    except Exception as e:

        print(
            f"[REPLAY ERROR] {e}"
        )

        return None