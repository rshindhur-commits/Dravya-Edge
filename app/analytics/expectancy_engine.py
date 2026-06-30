import pandas as pd
import os

def generate_expectancy_report(
    telemetry_path="telemetry/trade_telemetry.csv"
):
    try:

        telemetry_path = (
            "telemetry/"
            "trade_telemetry.csv"
        )

        if os.path.exists(
            telemetry_path
        ):

            df = pd.read_csv(
                telemetry_path
            )

            # existing summary logic

        else:

            print(
                "[SUMMARY] "
                "No telemetry data yet"
            )

    except Exception as e:

        print(
            f"[EXPECTANCY ERROR] "
            f"{e}"
        )

        return
    
    if len(df) == 0:

        print(
            "[EXPECTANCY] "
            "No telemetry records"
        )

        return

    replay_df = df[

        df["replay_outcome"]
        .notna()

    ].copy()

    if len(replay_df) == 0:

        print(
            "[EXPECTANCY] "
            "No replay results found"
        )

        return    

    replay_df["is_win"] = (

        replay_df[
            "replay_outcome"
        ]

        == "TARGET_HIT"

    )

    replay_df = replay_df[

        replay_df["r_multiple"]
        .notna()

    ]    

    def calculate_expectancy(group):

        total_trades = len(group)

        wins = group[
            group["is_win"]
        ]

        losses = group[
            ~group["is_win"]
        ]

        win_rate = (
            len(wins)
            / total_trades
        ) * 100

        avg_win = (

            wins["r_multiple"]
            .mean()

            if len(wins) > 0
            else 0
        )

        avg_loss = (

            losses["r_multiple"]
            .mean()

            if len(losses) > 0
            else 0
        )

        expectancy = (

            (
                win_rate / 100
            ) * avg_win

        ) - (

            (
                1 - (
                    win_rate / 100
                )
            ) * abs(avg_loss)

        )

        profit_factor = (

            wins["r_multiple"]
            .sum()

            /

            abs(
                losses["r_multiple"]
                .sum()
            )

            if len(losses) > 0
            else float("inf")
        )

        return pd.Series({

            "trades":
                total_trades,

            "win_rate":
                round(win_rate, 2),

            "avg_win":
                round(avg_win, 2),

            "avg_loss":
                round(avg_loss, 2),

            "expectancy":
                round(expectancy, 2),

            "profit_factor":
                (
                    round(profit_factor, 2)
                    if profit_factor != float("inf")
                    else "INF"
                ),

            "avg_bars":
                round(
                    group[
                        "bars_to_outcome"
                    ].mean(),
                    2
                ),

            "avg_mae":
                round(
                    group["mae"].mean(),
                    2
                ),

            "avg_mfe":
                round(
                    group["mfe"].mean(),
                    2
                )

        })
    
    print("\n========== EXPECTANCY REPORT ==========\n")

    signal_stats = (

        replay_df

        .groupby("final_signal")

        .apply(
            calculate_expectancy
        )

    )

    print(
        "\n=== BY SIGNAL ===\n"
    )

    print(
        signal_stats.sort_values(
            by="expectancy",
            ascending=False
        )
    )

    if "setup_category" in replay_df.columns:

        setup_stats = (

            replay_df

            .groupby(
                "setup_category"
            )

            .apply(
                calculate_expectancy
            )

        )

        print(
            "\n=== BY SETUP ===\n"
        )

        print(setup_stats)            

    if "market_regime" in replay_df.columns:

        regime_stats = (

            replay_df

            .groupby(
                "market_regime"
            )

            .apply(
                calculate_expectancy
            )

        )

        print(
            "\n=== BY REGIME ===\n"
        )

        print(regime_stats)

if __name__ == "__main__":

    generate_expectancy_report()                