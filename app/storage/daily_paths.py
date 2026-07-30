import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]


def _root_override(name, default):
    """Resolve a storage root, allowing an environment override.

    Resolved once at import so the module-level constants below stay stable for
    the many modules that import them directly. Tests set DRAVYA_DATA_DIR and
    DRAVYA_STATE_DIR before importing any app module so a test run cannot write
    into the real trading artifacts.
    """

    value = str(os.getenv(name) or "").strip()

    return Path(value) if value else default


DATA_DIR = _root_override("DRAVYA_DATA_DIR", ROOT_DIR / "data")
STATE_DIR = _root_override("DRAVYA_STATE_DIR", ROOT_DIR / "app" / "state")
LIVE_DIR = DATA_DIR / "live"
DAILY_DIR = DATA_DIR / "daily"


def get_live_dir():

    LIVE_DIR.mkdir(parents=True, exist_ok=True)

    return LIVE_DIR


def get_daily_dir(trading_day):

    daily_dir = DAILY_DIR / trading_day
    daily_dir.mkdir(parents=True, exist_ok=True)

    return daily_dir


def get_state_dir():

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    return STATE_DIR


def live_path(filename):

    return get_live_dir() / filename


def daily_path(trading_day, filename):

    return get_daily_dir(trading_day) / filename


def state_path(filename):

    return get_state_dir() / filename


def manifest_path(trading_day):

    return daily_path(trading_day, "manifest.json")
