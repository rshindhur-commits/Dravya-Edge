import pandas as pd

from app.ui import trade_chart


def _write_candles(tmp_path, rows):
    path = tmp_path / "candles_5m.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _bars(scan_id, start, count=12, price=100.0):
    start = pd.Timestamp(start, tz="UTC")
    return [{
        "symbol": "NVDA",
        "interval": "5m",
        "timestamp": (start + pd.Timedelta(minutes=5 * step)).isoformat(),
        "open": price, "high": price + 1, "low": price - 1, "close": price + 0.5,
        "volume": 1000, "trading_day": "2026-07-31", "scan_id": scan_id,
    } for step in range(count)]


def _patch_candles(monkeypatch, path):
    monkeypatch.setattr(trade_chart, "daily_path", lambda day, name: path)
    trade_chart._CANDLE_CACHE.clear()


def test_tradingview_url_normalises_symbol_and_sets_interval():
    assert trade_chart.tradingview_url("nvda") == (
        "https://www.tradingview.com/chart/?symbol=NVDA&interval=5"
    )
    assert trade_chart.tradingview_url("  ") is None
    assert trade_chart.tradingview_url(None) is None


def test_interleaved_scan_grids_collapse_to_one_consistent_grid(tmp_path, monkeypatch):
    """Each scan re-fetches the session on its own anchor, so the file holds
    several 5m grids offset by a minute or two. Charting them together would
    overlay candles for overlapping periods."""
    rows = _bars("2026-07-31_100000", "2026-07-31 14:00:00")
    rows += _bars("2026-07-31_100200", "2026-07-31 14:02:00", price=101.0)
    _patch_candles(monkeypatch, _write_candles(tmp_path, rows))

    bars = trade_chart.load_candles("2026-07-31", "NVDA")

    assert bars["scan_id"].nunique() == 1
    spacing = bars["timestamp"].diff().dropna().unique()
    assert list(spacing) == [pd.Timedelta(minutes=5)]


def test_exact_duplicate_bars_are_removed(tmp_path, monkeypatch):
    rows = _bars("2026-07-31_100000", "2026-07-31 14:00:00")
    _patch_candles(monkeypatch, _write_candles(tmp_path, rows + rows))

    bars = trade_chart.load_candles("2026-07-31", "NVDA")

    assert len(bars) == 12
    assert not bars.duplicated(["symbol", "timestamp"]).any()


def test_window_centres_on_the_trade_rather_than_the_end_of_file(tmp_path, monkeypatch):
    _patch_candles(
        monkeypatch,
        _write_candles(tmp_path, _bars("2026-07-31_100000", "2026-07-31 13:00:00", count=200)),
    )
    entry = pd.Timestamp("2026-07-31 13:30:00", tz="UTC").tz_convert(trade_chart.ET_TZ)
    exit_at = pd.Timestamp("2026-07-31 14:00:00", tz="UTC").tz_convert(trade_chart.ET_TZ)

    windowed = trade_chart.load_candles("2026-07-31", "NVDA", around=(entry, exit_at))
    tail = trade_chart.load_candles("2026-07-31", "NVDA")

    assert windowed["timestamp"].min() <= entry <= windowed["timestamp"].max()
    assert windowed["timestamp"].min() <= exit_at <= windowed["timestamp"].max()
    assert len(windowed) < len(tail)


def test_window_with_no_overlapping_bars_falls_back_to_recent_bars(tmp_path, monkeypatch):
    _patch_candles(
        monkeypatch,
        _write_candles(tmp_path, _bars("2026-07-31_100000", "2026-07-31 14:00:00")),
    )
    stale = pd.Timestamp("2020-01-01", tz="UTC").tz_convert(trade_chart.ET_TZ)

    assert len(trade_chart.load_candles("2026-07-31", "NVDA", around=(stale, stale))) == 12


def test_missing_symbol_and_missing_day_return_empty(tmp_path, monkeypatch):
    _patch_candles(
        monkeypatch,
        _write_candles(tmp_path, _bars("2026-07-31_100000", "2026-07-31 14:00:00")),
    )
    assert trade_chart.load_candles("2026-07-31", "ZZZZ").empty

    monkeypatch.setattr(trade_chart, "daily_path", lambda day, name: tmp_path / "absent.csv")
    trade_chart._CANDLE_CACHE.clear()
    assert trade_chart.load_candles("2026-07-31", "NVDA").empty


def test_build_markers_reads_both_spellings_and_rejects_unusable_prices():
    markers = trade_chart.build_markers({
        "entry_price": 198.24,
        "stop_loss": 197.24,
        "take_profit": 200.1,
        "exit_price": 197.5,
        "opened_at_et": "2026-07-31T10:58:46-04:00",
        "exit_time": "2026-07-31T11:11:25-04:00",
    })
    assert markers["entry_price"] == 198.24
    assert markers["stop_price"] == 197.24
    assert markers["target_price"] == 200.1
    assert markers["entry_time"].hour == 10

    empty = trade_chart.build_markers({"entry_price": "n/a", "stop_loss": 0, "exit_time": ""})
    assert empty["entry_price"] is None
    assert empty["stop_price"] is None
    assert empty["exit_time"] is None
