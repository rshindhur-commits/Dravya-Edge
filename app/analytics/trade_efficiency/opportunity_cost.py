import pandas as pd


def calculate_average_opportunity_cost(df):

    if df is None or df.empty or "Left On Table" not in df.columns:

        return None

    values = pd.to_numeric(
        df["Left On Table"],
        errors="coerce"
    ).dropna()

    if values.empty:

        return None

    return round(float(values.mean()), 4)


def calculate_total_opportunity_cost(df):

    if df is None or df.empty or "Left On Table" not in df.columns:

        return None

    values = pd.to_numeric(
        df["Left On Table"],
        errors="coerce"
    ).dropna()

    if values.empty:

        return None

    return round(float(values.sum()), 4)


__all__ = [
    "calculate_average_opportunity_cost",
    "calculate_total_opportunity_cost",
]