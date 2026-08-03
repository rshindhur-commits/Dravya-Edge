import os
import pandas as pd


def _safe_numeric(series):

    return pd.to_numeric(series, errors="coerce")


def _resolve_r_column(df):

    for column in ("r_multiple", "R Multiple", "r", "R"):

        if column in df.columns:

            return column

    return None


def _max_drawdown(values):

    if len(values) == 0:

        return 0.0

    cumulative = values.fillna(0).cumsum()
    running_high = cumulative.cummax()
    drawdown = cumulative - running_high

    return round(float(drawdown.min()), 2)


def calculate_expectancy_metrics(group, r_column="r_multiple"):

    r_values = _safe_numeric(group[r_column]).dropna()

    if r_values.empty:

        return pd.Series({
            "trade_count": 0,
            "win_rate": 0.0,
            "avg_r": 0.0,
            "median_r": 0.0,
            "total_r": 0.0,
            "profit_factor": 0.0,
            "avg_win_r": 0.0,
            "avg_loss_r": 0.0,
            "max_drawdown_r": 0.0,
            "expectancy_r": 0.0,
        })

    wins = r_values[r_values > 0]
    losses = r_values[r_values < 0]
    win_rate = len(wins) / len(r_values)
    loss_rate = 1 - win_rate
    avg_win = wins.mean() if len(wins) > 0 else 0.0
    avg_loss = losses.mean() if len(losses) > 0 else 0.0
    gross_win = wins.sum()
    gross_loss = abs(losses.sum())
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
    expectancy = (win_rate * avg_win) - (loss_rate * abs(avg_loss))

    return pd.Series({
        "trade_count": int(len(r_values)),
        "win_rate": round(win_rate * 100, 2),
        "avg_r": round(r_values.mean(), 2),
        "median_r": round(r_values.median(), 2),
        "total_r": round(r_values.sum(), 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else "INF",
        "avg_win_r": round(avg_win, 2),
        "avg_loss_r": round(avg_loss, 2),
        "max_drawdown_r": _max_drawdown(r_values),
        "expectancy_r": round(expectancy, 2),
    })


def normalize_expectancy_columns(df):

    normalized_df = df.copy()
    rename_map = {
        "setup_category": "setup_type",
        "entry": "setup_type",
        "final_signal": "direction",
        "Candidate Direction": "direction",
        "Market Regime": "market_regime",
        "Reference Regime": "reference_regime",
        "Sector Group": "sector_group",
        "Top Candidate": "top_candidate",
        "Expiration Bucket": "expiration_bucket",
        "Option Quality Score": "option_quality_score",
        "Option Spread %": "option_spread_pct",
    }

    for old_name, new_name in rename_map.items():

        if old_name in normalized_df.columns and new_name not in normalized_df.columns:

            normalized_df[new_name] = normalized_df[old_name]

    return normalized_df


def build_expectancy_table(df, group_columns):

    if df is None or df.empty:

        return pd.DataFrame()

    normalized_df = normalize_expectancy_columns(df)
    r_column = _resolve_r_column(normalized_df)

    if r_column is None:

        return pd.DataFrame()

    normalized_df[r_column] = _safe_numeric(normalized_df[r_column])
    normalized_df = normalized_df[normalized_df[r_column].notna()].copy()

    if normalized_df.empty:

        return pd.DataFrame()

    available_groups = [
        column for column in group_columns
        if column in normalized_df.columns
    ]

    if not available_groups:

        available_groups = ["run_type"] if "run_type" in normalized_df.columns else []

    if not available_groups:

        result = calculate_expectancy_metrics(normalized_df, r_column).to_frame().T
        result.insert(0, "group", "ALL")
        return result

    result = (
        normalized_df
        .groupby(available_groups, dropna=False)
        .apply(
            calculate_expectancy_metrics,
            r_column=r_column,
            include_groups=False
        )
        .reset_index()
    )
    result["group"] = result[available_groups].astype(str).agg(" | ".join, axis=1)

    ordered_columns = [
        "group",
        "trade_count",
        "win_rate",
        "avg_r",
        "median_r",
        "total_r",
        "profit_factor",
        "avg_win_r",
        "avg_loss_r",
        "max_drawdown_r",
        "expectancy_r",
    ]

    return result[ordered_columns + available_groups]


def _default_telemetry_path():
    """Resolved through the storage root, so tests cannot read the live file."""

    from app.storage.daily_paths import telemetry_path

    return str(telemetry_path("trade_telemetry.csv"))


def load_telemetry(telemetry_path=None):

    telemetry_path = telemetry_path or _default_telemetry_path()

    if not os.path.exists(telemetry_path):

        return pd.DataFrame()

    return pd.read_csv(telemetry_path)

def generate_expectancy_report(
    telemetry_path=None
):
    telemetry_path = telemetry_path or _default_telemetry_path()
    try:

        if os.path.exists(
            telemetry_path
        ):

            df = pd.read_csv(
                telemetry_path
            )

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

    print(
        "\n=== RESEARCH EXPECTANCY TABLES ===\n"
    )

    normalized_df = normalize_expectancy_columns(replay_df)
    report_groups = [
        ["setup_type"],
        ["direction"],
        ["market_regime"],
        ["setup_type", "market_regime"],
        ["setup_type", "direction"],
        ["top_candidate"],
        ["expiration_bucket"],
    ]

    tables = {}

    for group_columns in report_groups:

        table = build_expectancy_table(normalized_df, group_columns)

        if table.empty:

            continue

        group_name = "+".join(group_columns)
        tables[group_name] = table
        print(f"\n--- BY {group_name.upper()} ---\n")
        print(
            table.sort_values(
                by="expectancy_r",
                ascending=False
            )
        )

    return tables

if __name__ == "__main__":

    generate_expectancy_report()                