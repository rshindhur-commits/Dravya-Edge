"""A failed daily fetch must not be cached for the rest of the session.

The cache key is (symbol, trading day). Storing a failure under it pinned
`UNKNOWN` for that symbol until the next day and no later scan retried, which is
why `Daily Trend` reads UNKNOWN on 1,882 of 1,883 archived candidates while the
same code called directly returns BULL with a 3.08% ATR.
"""

from unittest import mock

import pandas as pd

from app.indicators import daily_context as module


FRAME = pd.DataFrame({
    "Open": [100.0] * 60,
    "High": [101.0] * 60,
    "Low": [99.0] * 60,
    "Close": [100.5] * 60,
    "Volume": [1_000.0] * 60,
}, index=pd.date_range("2026-05-01", periods=60, freq="D"))


def _patch(fetch_results):
    """Patch the two imports `daily_context` pulls in lazily."""

    calls = {"n": 0}

    def fake_fetch(*_args, **_kwargs):
        index = min(calls["n"], len(fetch_results) - 1)
        calls["n"] += 1
        return fetch_results[index]

    fake_module = mock.MagicMock()
    fake_module.get_polygon_data = fake_fetch
    fake_module.compute_indicators = lambda frame, **_kw: frame
    return fake_module, calls


def _run(symbol, fetch_results, context_from_frame):
    fake_module, calls = _patch(fetch_results)
    with mock.patch.dict(
        "sys.modules", {"app.indicators.technical_indicators": fake_module}
    ), mock.patch.object(module, "build_daily_context", context_from_frame):
        first = module.daily_context(symbol)
        second = module.daily_context(symbol)
    return first, second, calls["n"]


def setup_function(_):
    module.clear_cache()


def test_a_failed_fetch_is_retried_on_the_next_scan():
    """The defect: one empty premarket fetch silenced the symbol all day."""

    good = {"daily_trend": "BULL", "daily_trend_reason": "CLOSE_ABOVE_RISING_EMAS"}
    first, second, fetches = _run(
        "NVDA", [None, FRAME], lambda _frame: good
    )

    assert first["daily_trend"] == "UNKNOWN"
    assert second["daily_trend"] == "BULL"
    assert fetches == 2, "the second call must actually refetch"


def test_a_successful_context_is_cached():
    """The cache still has to work, or every scan refetches every symbol."""

    good = {"daily_trend": "BEAR", "daily_trend_reason": "CLOSE_BELOW_FALLING_EMAS"}
    first, second, fetches = _run("PLTR", [FRAME, FRAME], lambda _frame: good)

    assert first["daily_trend"] == "BEAR"
    assert second["daily_trend"] == "BEAR"
    assert fetches == 1, "a good context must not be refetched"


def test_an_exception_is_also_retried():
    def explode(_frame):
        raise ValueError("indicator failure")

    calls = {"n": 0}

    def build(frame):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("indicator failure")
        return {"daily_trend": "BULL", "daily_trend_reason": "OK"}

    fake_module, _ = _patch([FRAME, FRAME])
    with mock.patch.dict(
        "sys.modules", {"app.indicators.technical_indicators": fake_module}
    ), mock.patch.object(module, "build_daily_context", build):
        first = module.daily_context("AMD")
        second = module.daily_context("AMD")

    assert first["daily_trend_reason"] == "DAILY_CONTEXT_ERROR"
    assert second["daily_trend"] == "BULL"


def test_disabled_returns_immediately_and_is_not_cached_as_a_failure():
    with mock.patch.object(module, "daily_context_enabled", lambda: False):
        result = module.daily_context("NVDA")
    assert result["daily_trend_reason"] == "DAILY_CONTEXT_DISABLED"
    assert not module._cache, "a disabled read must not populate the cache"
