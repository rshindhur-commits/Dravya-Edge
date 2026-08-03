import os
import pandas as pd
from datetime import datetime
import uuid
import csv

from app.storage.daily_paths import daily_path, telemetry_path
from app.storage.session_manager import (
    get_or_create_session_manifest,
    get_scan_id,
    get_session_id,
    get_trading_day,
    trade_key
)


def _append_csv(df, file_path):

    file_path = str(file_path)
    os.makedirs(
        os.path.dirname(file_path) or ".",
        exist_ok=True
    )
    write_header = (
        not os.path.exists(file_path)
        or os.path.getsize(file_path) == 0
    )
    df.to_csv(
        file_path,
        mode="a",
        header=write_header,
        index=False,
        quoting=csv.QUOTE_ALL,
        escapechar="\\"
    )


def save_trade_telemetry(trade_data):

    """
    Save scan + projection telemetry
    for historical analytics
    """

    try:

        file_path = telemetry_path("trade_telemetry.csv")

        trading_day = trade_data.get(
            "trading_day",
            get_trading_day()
        )
        session_id = trade_data.get(
            "session_id",
            get_session_id(trading_day)
        )
        scan_id = trade_data.get(
            "scan_id",
            get_scan_id(trading_day)
        )
        get_or_create_session_manifest(trading_day)

        trade_data["trading_day"] = trading_day
        trade_data["session_id"] = session_id
        trade_data["scan_id"] = scan_id

        trade_data["run_type"] = trade_data.get(
            "run_type",
            "live_scan"
        )        

        # =====================================
        # Unique trade ID
        # =====================================

        trade_data["trade_id"] = str(
            uuid.uuid4()
        )        

        # =====================================
        # Add timestamp
        # =====================================

        trade_data["timestamp"] = (
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )

        trade_data["scan_timestamp"] = trade_data.get(
            "scan_timestamp",
            trade_data["timestamp"]
        )
        trade_data["trade_key"] = trade_data.get(
            "trade_key",
            trade_key(trade_data)
        )

        df = pd.DataFrame(
            [trade_data]
        )

        # =====================================
        # Append or create
        # =====================================

        _append_csv(df, file_path)
        _append_csv(
            df,
            daily_path(trading_day, "trade_telemetry.csv")
        )
        print(
            "[TELEMETRY SAVED]"
        )

    except Exception as e:

        print(
            f"[TELEMETRY ERROR] "
            f"{e}"
        )