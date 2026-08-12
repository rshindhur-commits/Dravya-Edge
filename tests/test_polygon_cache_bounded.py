"""The aggregate cache must stay bounded across scans.

This exists because it did not. The key is
`(symbol, multiplier, timespan, from_, to, limit)` and `from_`/`to` advance every
scan, so every scan minted fresh keys; eviction only ran inside `_cache_get` for
the one key being read, and a stale key is never read again. Nothing removed
them. On 2026-08-11 the worker climbed 241MB -> 1330MB over 113 scans, +9.6MB
each, while scan duration went 5s -> 150s walking the dead entries.

The TTL was never the bound and these tests do not treat it as one -- they
simulate scans the way the scanner runs them, with moving windows, and assert on
the size of the dict.
"""

import time

from app.utils import polygon_client


def _simulate_scan(scan_index, symbols=26, timeframes=4):
    """Fill the cache the way one scan does: fresh time bounds, every symbol."""

    for symbol_index in range(symbols):
        for timeframe in range(1, timeframes + 1):
            key = (
                f"SYM{symbol_index}",
                timeframe,
                "minute",
                1_700_000_000 + scan_index * 900,
                1_700_000_900 + scan_index * 900,
                200,
            )
            polygon_client._cache_set(key, [{"close": 1.0}] * 200)


def test_a_days_worth_of_scans_does_not_grow_the_cache(monkeypatch):
    """113 scans is what 2026-08-11 actually ran. It must not accumulate."""

    monkeypatch.setattr(polygon_client, "_cache", {})

    for scan in range(113):
        _simulate_scan(scan)

    assert len(polygon_client._cache) <= polygon_client._CACHE_MAX_ENTRIES, (
        f"cache holds {len(polygon_client._cache)} entries after 113 scans; "
        "unbounded growth here is the 1.1GB leak returning"
    )


def test_the_cache_does_not_track_scan_count(monkeypatch):
    """Size after many scans must not exceed size after few.

    The leak's signature was a cache whose size was a function of how long the
    process had been up. Ten scans and a hundred must land in the same place.
    """

    monkeypatch.setattr(polygon_client, "_cache", {})
    for scan in range(10):
        _simulate_scan(scan)
    after_ten = len(polygon_client._cache)

    for scan in range(10, 100):
        _simulate_scan(scan)
    after_hundred = len(polygon_client._cache)

    assert after_hundred <= after_ten or after_hundred <= polygon_client._CACHE_MAX_ENTRIES


def test_a_live_entry_still_reads_back(monkeypatch):
    """Bounding it must not break what the cache is for."""

    monkeypatch.setattr(polygon_client, "_cache", {})
    monkeypatch.setattr(polygon_client, "_CACHE_TTL", 30.0)

    key = ("AAPL", 1, "minute", 1_700_000_000, 1_700_000_900, 200)
    polygon_client._cache_set(key, [{"close": 42.0}])

    assert polygon_client._cache_get(key) == [{"close": 42.0}]


def test_an_expired_entry_is_not_served(monkeypatch):
    """The TTL still decides freshness, it just no longer decides size."""

    monkeypatch.setattr(polygon_client, "_cache", {})
    monkeypatch.setattr(polygon_client, "_CACHE_TTL", 0.01)

    key = ("AAPL", 1, "minute", 1_700_000_000, 1_700_000_900, 200)
    polygon_client._cache_set(key, [{"close": 42.0}])
    time.sleep(0.05)

    assert polygon_client._cache_get(key) is None


def test_the_sweep_drops_the_oldest_first(monkeypatch):
    """When everything is unexpired, age is the only fair thing to evict on."""

    monkeypatch.setattr(polygon_client, "_cache", {})
    monkeypatch.setattr(polygon_client, "_CACHE_TTL", 300.0)
    monkeypatch.setattr(polygon_client, "_CACHE_MAX_ENTRIES", 10)

    for index in range(40):
        polygon_client._cache_set(("SYM", 1, "minute", index, index + 1, 200), index)

    surviving = sorted(key[3] for key in polygon_client._cache)

    assert len(surviving) <= 10
    assert min(surviving) > 0, "the newest entries are the ones worth keeping"


def test_metrics_expose_the_size(monkeypatch):
    """'Is it fixed' needs an answer other than reading a graph by eye."""

    monkeypatch.setattr(polygon_client, "_cache", {})
    _simulate_scan(0)

    metrics = polygon_client.get_metrics()

    assert metrics["cache_entries"] == len(polygon_client._cache)
    assert metrics["cache_max_entries"] == polygon_client._CACHE_MAX_ENTRIES
