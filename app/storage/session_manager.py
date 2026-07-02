import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app.storage.daily_paths import (
    daily_path,
    get_daily_dir,
    manifest_path
)
from app.utils.json_store import (
    load_json_file,
    save_json_file
)


MARKET_TZ = ZoneInfo("America/New_York")


def now_et():

    return datetime.now(MARKET_TZ)


def get_trading_day(current_time=None):

    env_value = os.getenv("TRADING_DAY")

    if env_value:

        return env_value

    current_time = current_time or now_et()

    if current_time.tzinfo is None:

        current_time = current_time.replace(tzinfo=MARKET_TZ)

    current_time = current_time.astimezone(MARKET_TZ)

    return current_time.strftime("%Y-%m-%d")


def get_session_id(trading_day=None):

    trading_day = trading_day or get_trading_day()

    return f"paper_validation_{trading_day}"


def get_scan_id(trading_day=None, scan_timestamp=None):

    trading_day = trading_day or get_trading_day(scan_timestamp)
    scan_timestamp = scan_timestamp or now_et()

    if scan_timestamp.tzinfo is None:

        scan_timestamp = scan_timestamp.replace(tzinfo=MARKET_TZ)

    scan_timestamp = scan_timestamp.astimezone(MARKET_TZ)

    return f"{trading_day}_{scan_timestamp:%H%M%S}"


def get_or_create_session_manifest(trading_day=None):

    trading_day = trading_day or get_trading_day()
    path = manifest_path(trading_day)
    manifest = load_json_file(
        str(path),
        {}
    )

    if not manifest:

        manifest = {
            "trading_day": trading_day,
            "session_id": get_session_id(trading_day),
            "status": "OPEN",
            "first_scan_at": None,
            "last_scan_at": None,
            "finalized": False
        }
        save_json_file(str(path), manifest)

    return manifest


def save_session_manifest(manifest):

    trading_day = manifest["trading_day"]
    save_json_file(
        str(manifest_path(trading_day)),
        manifest
    )

    return manifest


def register_scan(trading_day=None, scan_id=None, scan_timestamp=None):

    scan_timestamp = scan_timestamp or now_et()
    trading_day = trading_day or get_trading_day(scan_timestamp)
    scan_id = scan_id or get_scan_id(trading_day, scan_timestamp)
    timestamp_text = scan_timestamp.strftime("%Y-%m-%d %H:%M:%S")
    manifest = get_or_create_session_manifest(trading_day)

    if not manifest.get("first_scan_at"):

        manifest["first_scan_at"] = timestamp_text

    manifest["last_scan_at"] = timestamp_text
    manifest["last_scan_id"] = scan_id

    if not manifest.get("finalized"):

        manifest["status"] = "OPEN"

    save_session_manifest(manifest)

    return manifest


def finalize_daily_report(trading_day=None):

    trading_day = trading_day or get_trading_day()
    manifest = get_or_create_session_manifest(trading_day)
    manifest["status"] = "FINAL"
    manifest["finalized"] = True
    manifest["finalized_at"] = now_et().strftime("%Y-%m-%d %H:%M:%S")

    return save_session_manifest(manifest)


def candidate_key(row):

    return "|".join([
        str(row.get("trading_day", "")),
        str(row.get("scan_timestamp", "")),
        str(row.get("symbol", "")),
        str(row.get("direction", "")),
        str(row.get("setup_type", "")),
        str(row.get("option_ticker", "")),
    ])


def trade_key(row):

    opened_at = (
        row.get("opened_at")
        or row.get("timestamp")
        or row.get("trade_id")
        or ""
    )

    return "|".join([
        str(row.get("symbol", "")),
        str(row.get("option_ticker", "")),
        str(opened_at),
    ])


def dedupe_daily_file(path, key_columns):

    path = daily_path(get_trading_day(), path) if isinstance(path, str) else path

    if not path.exists() or path.stat().st_size == 0:

        return 0

    suffix = path.suffix.lower()

    try:

        if suffix == ".parquet":

            df = pd.read_parquet(path)

        elif suffix == ".csv":

            df = pd.read_csv(path)

        else:

            return 0

        before_count = len(df)
        available_keys = [
            column for column in key_columns
            if column in df.columns
        ]

        if not available_keys:

            return 0

        df = df.drop_duplicates(
            subset=available_keys,
            keep="last"
        )

        if suffix == ".parquet":

            df.to_parquet(path, index=False)

        else:

            df.to_csv(path, index=False)

        return before_count - len(df)

    except Exception as exc:

        print(f"[DAILY DEDUPE WARNING] {path}: {exc}")
        return 0


def ensure_daily_session(trading_day=None):

    trading_day = trading_day or get_trading_day()
    get_daily_dir(trading_day)

    return get_or_create_session_manifest(trading_day)