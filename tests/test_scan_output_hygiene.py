"""Two write-side defects the reader had been compensating for.

`candles_5m.csv` accumulated several interleaved 5m grids plus a quarter of the
file in exact duplicates, and `format_timestamp` wrote instants pandas could not
read back without a FutureWarning.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------------
# Aggregate window anchoring
# --------------------------------------------------------------------------

def test_two_scans_a_few_minutes_apart_request_the_same_grid():
    """Polygon anchors its aggregates to `from_`. An unrounded "now minus N
    days" gave scan A bars on :00/:05/:10 and scan B bars on :02/:07/:12."""
    from app.indicators.technical_indicators import floor_to_bar

    first = floor_to_bar(datetime(2026, 7, 31, 9, 32, 17, 483000, tzinfo=ET), "minute", 5)
    second = floor_to_bar(datetime(2026, 7, 31, 9, 34, 58, 991000, tzinfo=ET), "minute", 5)

    assert first == second == datetime(2026, 7, 31, 9, 30, tzinfo=ET)


def test_flooring_matches_the_bar_size_being_requested():
    from app.indicators.technical_indicators import floor_to_bar

    moment = datetime(2026, 7, 31, 9, 47, 3, tzinfo=ET)

    assert floor_to_bar(moment, "minute", 15).minute == 45
    assert floor_to_bar(moment, "minute", 1).second == 0
    assert floor_to_bar(moment, "hour", 1) == datetime(2026, 7, 31, 9, 0, tzinfo=ET)
    assert floor_to_bar(moment, "day", 1) == datetime(2026, 7, 31, 0, 0, tzinfo=ET)


def test_a_zero_multiplier_does_not_divide_by_zero():
    from app.indicators.technical_indicators import floor_to_bar

    assert floor_to_bar(datetime(2026, 7, 31, 9, 47, 3, tzinfo=ET), "minute", 0).second == 0


# --------------------------------------------------------------------------
# Candle file de-duplication
# --------------------------------------------------------------------------

def _bars(start, count=3):
    index = pd.date_range(start, periods=count, freq="5min", tz="UTC")
    return pd.DataFrame(
        {"Open": [1.0] * count, "High": [2.0] * count,
         "Low": [0.5] * count, "Close": [1.5] * count, "Volume": [10] * count},
        index=index,
    )


@pytest.fixture
def candle_day(tmp_path, monkeypatch):
    import app.main as main

    path = tmp_path / "candles_5m.csv"
    monkeypatch.setattr(main, "daily_path", lambda day, name: path)
    main._DAILY_CANDLE_KEYS.clear()
    return main, path


def test_re_fetching_the_same_session_does_not_duplicate_it(candle_day):
    """Every scan re-fetches the whole session: 4,879 of 19,496 rows on
    2026-07-31 were exact repeats."""
    main, path = candle_day

    assert main._append_daily_candles("NVDA", _bars("2026-07-31 14:00"), "2026-07-31", "s1") == 3
    assert main._append_daily_candles("NVDA", _bars("2026-07-31 14:00"), "2026-07-31", "s2") == 0

    written = pd.read_csv(path)
    assert len(written) == 3
    assert not written.duplicated(["symbol", "timestamp"]).any()


def test_only_the_bars_a_scan_actually_adds_are_appended(candle_day):
    main, path = candle_day

    main._append_daily_candles("NVDA", _bars("2026-07-31 14:00"), "2026-07-31", "s1")
    added = main._append_daily_candles("NVDA", _bars("2026-07-31 14:05", 4), "2026-07-31", "s2")

    assert added == 2
    assert len(pd.read_csv(path)) == 5


def test_symbols_do_not_shadow_each_other(candle_day):
    main, path = candle_day

    main._append_daily_candles("NVDA", _bars("2026-07-31 14:00"), "2026-07-31", "s1")
    main._append_daily_candles("CRWD", _bars("2026-07-31 14:00"), "2026-07-31", "s1")

    assert pd.read_csv(path).groupby("symbol").size().to_dict() == {"CRWD": 3, "NVDA": 3}


def test_a_restart_mid_session_reseeds_from_the_file_instead_of_duplicating(candle_day):
    main, path = candle_day

    main._append_daily_candles("NVDA", _bars("2026-07-31 14:00"), "2026-07-31", "s1")
    main._DAILY_CANDLE_KEYS.clear()  # process restart

    assert main._append_daily_candles("NVDA", _bars("2026-07-31 14:00"), "2026-07-31", "s2") == 0
    assert len(pd.read_csv(path)) == 3


def test_an_empty_frame_writes_nothing(candle_day):
    main, path = candle_day

    assert main._append_daily_candles("NVDA", pd.DataFrame(), "2026-07-31", "s1") == 0
    assert not path.exists()


# --------------------------------------------------------------------------
# Timestamp format
# --------------------------------------------------------------------------

def test_timestamps_are_written_with_an_offset_pandas_can_read():
    """`%Z` produced `2026-07-31 00:38:19 EDT`, which pandas parses only with a
    FutureWarning and will eventually refuse."""
    import warnings

    from app.main import format_timestamp

    written = format_timestamp(datetime(2026, 7, 31, 0, 38, 19, tzinfo=ET))

    assert written == "2026-07-31 00:38:19-04:00"
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        assert pd.to_datetime(written) == pd.Timestamp("2026-07-31 00:38:19-04:00")


def test_the_offset_follows_daylight_saving():
    from app.main import format_timestamp

    assert format_timestamp(datetime(2026, 1, 15, 9, 30, tzinfo=ET)).endswith("-05:00")
    assert format_timestamp(datetime(2026, 7, 15, 9, 30, tzinfo=ET)).endswith("-04:00")


def test_a_naive_value_is_written_as_eastern_rather_than_ambiguous():
    """These columns are named ET; leaving the zone off is what made them
    unreadable in the first place."""
    from app.main import format_timestamp

    assert format_timestamp(datetime(2026, 7, 31, 0, 38, 19)) == "2026-07-31 00:38:19-04:00"


def test_no_timestamp_stays_none():
    from app.main import format_timestamp

    assert format_timestamp(None) is None


def test_both_the_old_and_new_forms_read_back_to_the_same_instant():
    """Archived files still hold the abbreviation form."""
    from app.main import format_timestamp
    from app.ui.timestamps import to_utc

    assert to_utc(format_timestamp(datetime(2026, 7, 31, 0, 38, 19, tzinfo=ET))) == to_utc(
        "2026-07-31 00:38:19 EDT"
    )
