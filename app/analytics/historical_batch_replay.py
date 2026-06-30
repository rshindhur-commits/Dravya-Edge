import pandas as pd

from app.strategies.entry_engine import (
    detect_entry
)

from app.mock.load_mock_aggs import (
    load_mock_aggs
)

from app.strategies.momentum_strategy import (
    analyze_setup
)

from app.projections.trade_projection import (
    project_trade
)

from app.analytics.replay_engine import (
    replay_trade_projection
)

from app.analytics.trade_telemetry import (
    save_trade_telemetry
)

from app.risk.risk_manager import (
    calculate_risk
)

from app.indicators.enrich_indicators import (
    enrich_indicators
)

def run_historical_batch_replay(
    symbol,
    df,
    max_replays=25,
    setup_type=None
):
    print(
        f"\n========== "
        f"BATCH REPLAY: {symbol} "
        f"==========\n"
    )

    replay_count = 0

    neutral_count = 0

    risk_skip_count = 0

    projection_skip_count = 0

    replay_skip_count = 0    

    for i in range(60, len(df) - 20):
        if replay_count >= max_replays:
            break
        historical_df = (
            df.iloc[:i]
            .copy()
        )

        historical_df = enrich_indicators(
            historical_df
        )           

        if len(historical_df) < 45:

            continue
        
        analysis = analyze_setup(
            historical_df
        )

        print(
            f"[ANALYSIS DEBUG] "
            f"{symbol} "
            f"index={i} "
            f"analysis={analysis.get('signal')}"
        )

        final_signal = analysis.get(
            "signal",
            "NEUTRAL"
        )

        if final_signal == "NEUTRAL":

            neutral_count += 1

            continue

        # entry_setup = {

        #     "entry_type":
        #         analysis.get(
        #             "entry_type",
        #             "BREAKOUT"
        #         ),

        #     "entry_quality":
        #         analysis.get(
        #             "entry_quality",
        #             "MEDIUM"
        #         ),

        #     "avoid_chasing":
        #         analysis.get(
        #             "avoid_chasing",
        #             False
        #         )
        # }

        entry_setup = detect_entry(
            historical_df,
            analysis
        )

        if entry_setup["entry_type"] == "NO_ENTRY":
            continue

        if (
            setup_type
            and entry_setup["entry_type"] != setup_type
        ):

            continue

        risk_setup = calculate_risk(

            df=historical_df,

            analysis=analysis,

            entry_setup=entry_setup
        )

        if (
            risk_setup["stop_loss"]
            is None
        ):

            risk_skip_count += 1
            print(
                f"[RISK SKIP] "
                f"{symbol} "
                f"index={i} "
                f"risk_setup={risk_setup}"
            )
            continue

        projection = project_trade(

            symbol=symbol,

            latest_price=
                historical_df.iloc[-1]["Close"],

            analysis={

                "signal":
                    final_signal,

                "score":
                    analysis.get(
                        "score",
                        0
                    ),

                "ATR":
                    abs(
                        historical_df["High"].tail(14).mean()
                        -
                        historical_df["Low"].tail(14).mean()
                    ),

                "market_regime":
                    analysis.get(
                        "market_regime",
                        "CHOPPY"
                    )
            },

            entry_setup=entry_setup,

            risk_setup=risk_setup
        )

        if (
            projection is None
            or projection.get("target_price") is None
            or projection.get("stop_price") is None
        ):
            projection_skip_count += 1
            continue        


        # =========================================
        # Adaptive Replay Window
        # =========================================

        remaining_bars = len(df) - i

        future_window = min(
            20,
            remaining_bars
        )

        # Safety protection
        if future_window < 5:

            replay_skip_count += 1

            print(
                f"[REPLAY SKIP] "
                f"{symbol} "
                f"index={i} "
                f"future_window={future_window}"
            )

            continue

        replay_result = (

            replay_trade_projection(

                symbol=symbol,

                df=df.iloc[
                    : i + future_window
                ].copy(),

                projection=projection,

                final_signal=final_signal
            )

        )


        if replay_result is None:
            replay_skip_count += 1
            continue

        save_trade_telemetry({

            "symbol":
                symbol,

            "final_signal":
                final_signal,

            "setup_category":
                entry_setup[
                    "entry_type"
                ],

            "market_regime":
                analysis.get(
                    "market_regime",
                    "UNKNOWN"
                ),

            "trade_grade":
                projection.get(
                    "trade_grade"
                ),

            "probability":
                projection.get(
                    "probability"
                ),

            "risk_reward":
                risk_setup.get(
                    "risk_reward"
                ),

            "replay_outcome":
                replay_result.get(
                    "outcome"
                ),

            "bars_to_outcome":
                replay_result.get(
                    "bars_processed"
                ),

            "r_multiple":
                replay_result.get(
                    "r_multiple"
                ),

            "mae":
                replay_result.get(
                    "mae"
                ),

            "mfe":
                replay_result.get(
                    "mfe"
                ),

            "run_type":
                "historical_batch"
        })

        replay_count += 1

        print(
            f"[BATCH REPLAY] "
            f"{symbol} "
            f"index={i} "
            f"signal={final_signal} "
            f"outcome="
            f"{replay_result.get('outcome')}"
        )        

    print(
        f"\n[BATCH COMPLETE] "
        f"{symbol} "
        f"replays={replay_count}"
    )

    print(
        f"[FILTER STATS] "
        f"neutral={neutral_count} "
        f"risk_skip={risk_skip_count} "
        f"projection_skip={projection_skip_count} "
        f"replay_skip={replay_skip_count}"
    )    

if __name__ == "__main__":

    # mock_df = pd.read_csv(
    #     "mock_aggs.csv"
    # )



    symbols = [

        "TSLA",
        "QQQ",
        "SPY",
        "NVDA"

    ]

    for symbol in symbols:

        mock_data = load_mock_aggs(
            symbol
        )

        mock_df = pd.DataFrame(
            mock_data
        )    

        mock_df = mock_df.rename(columns={

            "o": "Open",
            "h": "High",
            "l": "Low",
            "c": "Close",
            "v": "Volume"

        })            

        run_historical_batch_replay(
            symbol,
            mock_df
        )                                                                                                                  