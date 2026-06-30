import pandas as pd
import os


def _print_value_counts(df, column, title):

    print(f"\n{title}:")

    if column not in df.columns:

        print("N/A")
        return

    print(
        df[column]
        .value_counts()
    )


def _print_average(df, column, title):

    print(f"\n{title}:")

    if column not in df.columns:

        print("N/A")
        return

    values = pd.to_numeric(
        df[column],
        errors="coerce"
    ).dropna()

    if values.empty:

        print("N/A")
        return

    print(round(values.mean(), 2))


def summarize_telemetry():

    try:

        telemetry_path = (
            "telemetry/"
            "trade_telemetry.csv"
        )

        if not os.path.exists(
            telemetry_path
        ):

            print(
                "[SUMMARY] "
                "No telemetry data yet"
            )

            return

        if os.path.getsize(telemetry_path) == 0:

            print(
                "[SUMMARY] "
                "Telemetry file is empty"
            )

            return

        df = pd.read_csv(
            telemetry_path
        )

        print(
            "\nRun Types:"
        )

        if "run_type" in df.columns:

            print("\nRun Types:")

            print(
                df["run_type"]
                .value_counts()
            )

        else:

            print(
                "\nRun Types:"
            )

            print(
                "Legacy telemetry file"
            )     

        print(
            "\n========== "
            "TELEMETRY SUMMARY "
            "=========="
        )

        print(
            f"Total Records: "
            f"{len(df)}"
        )

        _print_value_counts(
            df,
            "final_signal",
            "Signals"
        )

        _print_value_counts(
            df,
            "trade_grade",
            "Trade Grades"
        )

        print(
            "\nGrade Distribution %:"
        )

        if "trade_grade" in df.columns:

            print(
                round(
                    (
                        df["trade_grade"]
                        .value_counts(
                            normalize=True
                        )
                        * 100
                    ),
                    2
                )
            )

        else:

            print("N/A")

        _print_average(
            df,
            "probability",
            "Average Probability"
        )

        _print_average(
            df,
            "risk_reward",
            "Average RR"
        )

        print(
            "\nSignal Statistics:"
        )
        stat_columns = [
            column for column in [
                "probability",
                "risk_reward",
                "r_multiple",
                "pnl_pct"
            ]
            if column in df.columns
        ]

        if "final_signal" in df.columns and stat_columns:

            signal_stats = (
                df.groupby(
                    "final_signal"
                )[stat_columns]
                .mean(numeric_only=True)
            )

            print(
                round(
                    signal_stats,
                    2
                )
            )

        else:

            print("N/A")

        print(
            "\nReplay Outcome %:"
        )

        if "replay_outcome" in df.columns:

            print(
                (
                    df["replay_outcome"]
                    .value_counts(normalize=True)
                    * 100
                ).round(2)
            )

        else:

            print("N/A")

        print(
            "\nAverage Bars To Outcome:"
        )

        if (
            "replay_outcome" in df.columns
            and "bars_to_outcome" in df.columns
        ):

            print(
                round(
                    df.groupby(
                        "replay_outcome"
                    )[
                        "bars_to_outcome"
                    ]
                    .mean(),
                    2
                )
            )

        else:

            print("N/A")


        print(
            "\nOutcome By Signal:"
        )

        if (
            "final_signal" in df.columns
            and "replay_outcome" in df.columns
        ):

            print(
                pd.crosstab(
                    df["final_signal"],
                    df["replay_outcome"],
                    normalize="index"
                ).round(2)
            )

        else:

            print("N/A")

    except Exception as e:

        print(
            f"[SUMMARY ERROR] "
            f"{e}"
        )