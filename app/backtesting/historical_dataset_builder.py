from pathlib import Path
import json

import pandas as pd


COLUMN_MAP = {
    "o": "Open",
    "h": "High",
    "l": "Low",
    "c": "Close",
    "v": "Volume",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}


def normalize_candle_frame(df, timestamp_column=None):

    normalized_df = df.copy()
    normalized_df = normalized_df.rename(columns=COLUMN_MAP)

    if timestamp_column and timestamp_column in normalized_df.columns:

        normalized_df["timestamp"] = pd.to_datetime(
            normalized_df[timestamp_column],
            errors="coerce"
        )
        normalized_df = normalized_df.set_index("timestamp")
    elif "timestamp" in normalized_df.columns:

        normalized_df["timestamp"] = pd.to_datetime(
            normalized_df["timestamp"],
            errors="coerce"
        )
        normalized_df = normalized_df.set_index("timestamp")
    elif "t" in normalized_df.columns:

        normalized_df["timestamp"] = pd.to_datetime(
            normalized_df["t"],
            unit="ms",
            errors="coerce"
        )
        normalized_df = normalized_df.set_index("timestamp")

    normalized_df = normalized_df.sort_index()

    required_columns = ["Open", "High", "Low", "Close", "Volume"]
    missing_columns = [
        column for column in required_columns
        if column not in normalized_df.columns
    ]

    if missing_columns:

        raise ValueError(f"Missing historical columns: {missing_columns}")

    return normalized_df[required_columns]


def load_symbol_history(path):

    source_path = Path(path)

    if source_path.suffix.lower() == ".json":

        with source_path.open("r", encoding="utf-8") as file_handle:

            raw_data = json.load(file_handle)

        if isinstance(raw_data, dict) and "results" in raw_data:

            raw_df = pd.DataFrame(raw_data["results"])

        else:

            raw_df = pd.DataFrame(raw_data)
    else:

        raw_df = pd.read_csv(source_path)

    return normalize_candle_frame(raw_df)


def build_historical_dataset(input_dir="data/historical", symbols=None):

    input_path = Path(input_dir)
    dataset = {}
    files = list(input_path.glob("*.csv")) + list(input_path.glob("*.json"))

    for file_path in files:

        symbol = file_path.stem.upper()

        if symbols and symbol not in symbols:

            continue

        dataset[symbol] = load_symbol_history(file_path)

    return dataset