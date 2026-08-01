"""`format_timestamp` renders with `%Z`, so the scanner's `Current ET` column and
everything derived from it carries values like `2026-07-31 00:38:19 EDT`. Pandas
parses those with a FutureWarning saying the zone is dropped and will raise in a
future version -- and dropping it silently turns an Eastern instant into a naive
one, four hours off."""

import warnings

import pandas as pd
import pytest

from app.ui.timestamps import minutes_since, to_utc, to_utc_series


def test_zone_abbreviations_are_read_as_the_offsets_they_stand_for():
    assert to_utc("2026-07-31 00:38:19 EDT") == pd.Timestamp("2026-07-31 04:38:19", tz="UTC")
    assert to_utc("2026-01-15 09:30:00 EST") == pd.Timestamp("2026-01-15 14:30:00", tz="UTC")


def test_parsing_an_abbreviation_raises_no_future_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        assert to_utc("2026-07-31 00:38:19 EDT") is not pd.NaT
        assert to_utc_series(["2026-07-31 00:38:19 EDT"]).notna().all()


def test_a_naive_value_is_eastern_not_utc():
    """The column is named `Current ET`. Reading it as UTC moves it four hours."""
    assert to_utc("2026-07-31 10:58:46") == pd.Timestamp("2026-07-31 14:58:46", tz="UTC")


def test_an_explicit_offset_is_respected():
    assert to_utc("2026-07-31T10:58:46-04:00") == pd.Timestamp("2026-07-31 14:58:46", tz="UTC")
    assert to_utc("2026-07-31T14:58:46Z") == pd.Timestamp("2026-07-31 14:58:46", tz="UTC")


def test_unreadable_values_become_nat_rather_than_raising():
    for value in (None, "", "not a time", float("nan")):
        assert pd.isna(to_utc(value))


def test_a_column_of_mixed_shapes_parses_each_by_its_own_rules():
    parsed = to_utc_series([
        "2026-07-31 00:38:19 EDT",
        "2026-07-31T10:58:46-04:00",
        "2026-07-31 10:58:46",
        "garbage",
    ])

    assert str(parsed.dtype) == "datetime64[ns, UTC]"
    assert parsed[0] == pd.Timestamp("2026-07-31 04:38:19", tz="UTC")
    assert parsed[1] == pd.Timestamp("2026-07-31 14:58:46", tz="UTC")
    # The naive value means the same instant as the offset one beside it.
    assert parsed[2] == parsed[1]
    assert pd.isna(parsed[3])


def test_mixed_shapes_do_not_let_naive_values_be_read_as_utc():
    """Passing utc=True over the whole column would do exactly that."""
    parsed = to_utc_series(["2026-07-31T14:58:46+00:00", "2026-07-31 10:58:46"])

    assert parsed[0] == parsed[1]


def test_an_empty_column_returns_an_empty_utc_column():
    parsed = to_utc_series([])

    assert len(parsed) == 0
    assert str(parsed.dtype) == "datetime64[ns, UTC]"


def test_already_parsed_timestamps_pass_through_both_aware_and_naive():
    aware = pd.Timestamp("2026-07-31 14:58:46", tz="UTC")
    assert to_utc(aware) == aware
    assert to_utc(pd.Timestamp("2026-07-31 10:58:46")) == aware


def test_minutes_since_measures_against_now_and_survives_junk():
    now = pd.Timestamp("2026-07-31 15:00:00", tz="UTC")

    assert minutes_since("2026-07-31 10:30:00 EDT", now=now) == pytest.approx(30.0)
    assert minutes_since(None, now=now) is None
    assert minutes_since("nonsense", now=now) is None
