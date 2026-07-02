from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT_DIR / "data"
LIVE_DIR = DATA_DIR / "live"
DAILY_DIR = DATA_DIR / "daily"


def get_live_dir():

    LIVE_DIR.mkdir(parents=True, exist_ok=True)

    return LIVE_DIR


def get_daily_dir(trading_day):

    daily_dir = DAILY_DIR / trading_day
    daily_dir.mkdir(parents=True, exist_ok=True)

    return daily_dir


def live_path(filename):

    return get_live_dir() / filename


def daily_path(trading_day, filename):

    return get_daily_dir(trading_day) / filename


def manifest_path(trading_day):

    return daily_path(trading_day, "manifest.json")