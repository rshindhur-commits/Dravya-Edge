import os
import pandas as pd
from datetime import datetime
import uuid
import csv


def save_trade_telemetry(trade_data):

    """
    Save scan + projection telemetry
    for historical analytics
    """

    try:

        os.makedirs(
            "telemetry",
            exist_ok=True
        )

        file_path = (
            "telemetry/"
            "trade_telemetry.csv"
        )

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

        df = pd.DataFrame(
            [trade_data]
        )

        # =====================================
        # Append or create
        # =====================================

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
        print(
            "[TELEMETRY SAVED]"
        )

    except Exception as e:

        print(
            f"[TELEMETRY ERROR] "
            f"{e}"
        )