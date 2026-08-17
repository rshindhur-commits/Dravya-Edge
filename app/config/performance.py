from __future__ import annotations

from app.config.settings import get_bool_env, get_int_env


PERFORMANCE_MODE = {
    "TRADING": {
        "lazy_imports": get_bool_env("PERFORMANCE_TRADING_LAZY_IMPORTS", True),
        "cache_ttl": get_int_env("PERFORMANCE_TRADING_CACHE_TTL", 5),
        "background_reports": get_bool_env("PERFORMANCE_BACKGROUND_REPORTS", True),
        "background_validation": get_bool_env("PERFORMANCE_BACKGROUND_VALIDATION", True),
        "background_replay": get_bool_env("PERFORMANCE_BACKGROUND_REPLAY", True),
        "dashboard_state_only": get_bool_env("PERFORMANCE_DASHBOARD_STATE_ONLY", True),
    },
    "VALIDATION": {
        "cache_ttl": get_int_env("PERFORMANCE_VALIDATION_CACHE_TTL", 60),
        # The Validation page reads a 30-day window of *closed* trades straight
        # from Neon, uncached, once per rerun. At the old 1-minute refresh an
        # open tab sent ~390 identical queries a session to redraw numbers that
        # move a handful of times a day. 15 minutes is well inside how fast a
        # closed trade can appear and turns a stray refresh into a dict lookup.
        "trade_cache_ttl": get_int_env("PERFORMANCE_VALIDATION_TRADE_CACHE_TTL", 900),
    },
    "DEVELOPER": {
        "cache_ttl": get_int_env("PERFORMANCE_DEVELOPER_CACHE_TTL", 120),
    },
    "REPLAY": {
        "cache_ttl": None,
    },
    "REPORTS": {
        "cache_ttl": None,
    },
}


TRADING_CACHE_TTL = PERFORMANCE_MODE["TRADING"]["cache_ttl"]
VALIDATION_CACHE_TTL = PERFORMANCE_MODE["VALIDATION"]["cache_ttl"]
VALIDATION_TRADE_CACHE_TTL = PERFORMANCE_MODE["VALIDATION"]["trade_cache_ttl"]
DEVELOPER_CACHE_TTL = PERFORMANCE_MODE["DEVELOPER"]["cache_ttl"]
TRADING_DASHBOARD_STATE_ONLY = PERFORMANCE_MODE["TRADING"]["dashboard_state_only"]