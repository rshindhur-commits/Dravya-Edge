import pandas as pd

from app.ui import trade_chart
from app.ui.pages import trading


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


def _write_events(tmp_path, rows):
    day_dir = tmp_path / "data" / "daily" / "2026-07-31"
    day_dir.mkdir(parents=True)
    pd.DataFrame(rows).to_csv(day_dir / "paper_trade_events.csv", index=False)
    return tmp_path


def test_entries_used_counts_the_day_even_when_the_open_row_is_missing(tmp_path, monkeypatch):
    """The 2026-07-30 file holds an AUTO_EXIT whose OPEN row never landed;
    counting OPEN events reported zero entries for a day that took a trade."""
    monkeypatch.setattr(trading, "ROOT_DIR", _write_events(tmp_path, [{
        "event_type": "AUTO_EXIT",
        "trade_key": "NVDA|O:NVDA260807C00197500|2026-07-31 10:58:46",
        "symbol": "NVDA",
    }]))

    assert trading._entries_used("2026-07-31") == 1


def test_entries_used_ignores_a_position_carried_in_from_a_previous_day(tmp_path, monkeypatch):
    monkeypatch.setattr(trading, "ROOT_DIR", _write_events(tmp_path, [
        {"event_type": "AUTO_EXIT",
         "trade_key": "NVDA|O:NVDA260807C00197500|2026-07-30 15:10:00", "symbol": "NVDA"},
        {"event_type": "OPEN",
         "trade_key": "CRWD|O:CRWD260807C00195000|2026-07-31 11:36:33", "symbol": "CRWD"},
    ]))

    assert trading._entries_used("2026-07-31") == 1


def test_closed_trades_stitch_entry_time_back_onto_the_close_row(tmp_path, monkeypatch):
    monkeypatch.setattr(trading, "ROOT_DIR", _write_events(tmp_path, [
        {"event_type": "OPEN", "trade_key": "NVDA|OPT|2026-07-31 10:58:46",
         "symbol": "NVDA", "direction": "CALL", "event_time_et": "2026-07-31T10:58:46-04:00",
         "entry_price": 198.24, "exit_price": None, "r_multiple": None, "exit_reason": None},
        {"event_type": "AUTO_EXIT", "trade_key": "NVDA|OPT|2026-07-31 10:58:46",
         "symbol": "NVDA", "direction": "CALL", "event_time_et": "2026-07-31T11:11:25-04:00",
         "entry_price": 198.24, "exit_price": 197.5, "r_multiple": -0.74,
         "exit_reason": "VWAP invalidation"},
    ]))

    trades = trading._closed_trades("2026-07-31")

    assert len(trades) == 1
    assert trades[0]["entry_time"] == "2026-07-31T10:58:46-04:00"
    assert trades[0]["exit_time"] == "2026-07-31T11:11:25-04:00"
    assert trades[0]["r_multiple"] == -0.74
    assert trades[0]["closed_how"] == "AUTO_EXIT"


def test_open_positions_are_not_reported_as_closed_trades(tmp_path, monkeypatch):
    monkeypatch.setattr(trading, "ROOT_DIR", _write_events(tmp_path, [{
        "event_type": "OPEN", "trade_key": "NVDA|OPT|2026-07-31 12:57:59",
        "symbol": "NVDA", "event_time_et": "2026-07-31T12:57:59-04:00",
    }]))

    assert trading._closed_trades("2026-07-31") == []


def test_post_market_switch_follows_the_close_and_the_weekend():
    from datetime import datetime

    friday_open = datetime(2026, 7, 31, 11, 0, tzinfo=trading.ET_TZ)
    friday_closed = datetime(2026, 7, 31, 16, 30, tzinfo=trading.ET_TZ)
    saturday = datetime(2026, 8, 1, 11, 0, tzinfo=trading.ET_TZ)

    assert not trading._is_post_market(friday_open)
    assert trading._is_post_market(friday_closed)
    assert trading._is_post_market(saturday)
