from pathlib import Path

import pandas as pd

from app.analytics.expectancy_engine import build_expectancy_table


DEFAULT_GROUP_COLUMNS = [
    "setup_type",
    "direction",
    "market_regime",
    "reference_regime",
    "sector_group",
    "top_candidate",
    "time_bucket",
    "option_quality_bucket",
    "spread_bucket",
    "expiration_bucket",
]


def add_expectancy_buckets(df):

    bucketed_df = df.copy()

    if "timestamp" in bucketed_df.columns:

        timestamps = pd.to_datetime(bucketed_df["timestamp"], errors="coerce")
        bucketed_df["time_bucket"] = timestamps.dt.hour.fillna(-1).astype(int).map(
            lambda hour: "UNKNOWN" if hour < 0 else f"{hour:02d}:00"
        )

    if "option_quality_score" in bucketed_df.columns:

        bucketed_df["option_quality_bucket"] = pd.cut(
            pd.to_numeric(bucketed_df["option_quality_score"], errors="coerce"),
            bins=[-1, 50, 65, 80, 1000],
            labels=["LOW", "FAIR", "GOOD", "GREAT"]
        ).astype(str)

    if "option_spread_pct" in bucketed_df.columns:

        bucketed_df["spread_bucket"] = pd.cut(
            pd.to_numeric(bucketed_df["option_spread_pct"], errors="coerce"),
            bins=[-1, 5, 10, 20, 1000],
            labels=["TIGHT", "ACCEPTABLE", "WIDE", "VERY_WIDE"]
        ).astype(str)

    return bucketed_df


def verdict_for_row(row, min_trades=5):

    trade_count = row.get("trade_count", 0)
    expectancy = row.get("expectancy_r", 0)
    total_r = row.get("total_r", 0)

    if trade_count < min_trades:

        return "WATCH"
    if expectancy > 0.15 and total_r > 0:

        return "KEEP"
    if expectancy < -0.15 or total_r < -1:

        return "BLOCK/TIGHTEN"
    return "REVIEW"


def build_grouped_expectancy_reports(df, group_columns=None):

    group_columns = group_columns or DEFAULT_GROUP_COLUMNS
    bucketed_df = add_expectancy_buckets(df)
    reports = {}

    for group_column in group_columns:

        if group_column not in bucketed_df.columns:

            continue

        report_df = build_expectancy_table(bucketed_df, [group_column])

        if report_df.empty:

            continue

        report_df["verdict"] = report_df.apply(verdict_for_row, axis=1)
        reports[group_column] = report_df

    return reports


def write_expectancy_report(
    df,
    output_path="reports/expectancy_report.html",
    group_columns=None
):

    reports = build_grouped_expectancy_reports(df, group_columns=group_columns)
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    html_sections = [
        "<html><head><title>Expectancy Report</title></head><body>",
        "<h1>Expectancy Report</h1>",
    ]

    for group_name, report_df in reports.items():

        html_sections.append(f"<h2>By {group_name}</h2>")
        html_sections.append(report_df.to_html(index=False))

    html_sections.append("</body></html>")
    output_file.write_text("\n".join(html_sections), encoding="utf-8")

    return {
        "path": str(output_file),
        "sections": list(reports.keys()),
    }