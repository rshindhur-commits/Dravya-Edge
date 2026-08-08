"""The append path has to produce the file the rewrite path produced.

These writers were changed for cost, not behaviour, so the tests that matter
are the ones comparing the two implementations rather than asserting the new
one does something reasonable.
"""

import pandas as pd

from app.analytics.activity_trace import write_daily_activity_trace
from app.storage.incremental_csv import append_new_rows


def rewrite(path, frame, key_columns):
    """What the old code did, kept here to compare against."""

    existing = (
        pd.read_csv(path)
        if path.exists() and path.stat().st_size
        else pd.DataFrame()
    )
    merged = pd.concat([existing, frame], ignore_index=True, sort=False)

    return merged.drop_duplicates(key_columns, keep="last").reset_index(drop=True)


def frame_of(ids, note="a"):

    return pd.DataFrame([{"id": i, "value": f"{note}{i}"} for i in ids])


def test_append_matches_a_full_rewrite(tmp_path):

    appended = tmp_path / "appended.csv"
    rewritten = tmp_path / "rewritten.csv"
    batches = [frame_of([1, 2]), frame_of([2, 3]), frame_of([3, 4, 5]), frame_of([1, 5])]

    for batch in batches:

        append_new_rows(appended, batch, ["id"])
        rewrite(rewritten, batch, ["id"]).to_csv(rewritten, index=False)

    pd.testing.assert_frame_equal(
        pd.read_csv(appended).sort_values("id").reset_index(drop=True),
        pd.read_csv(rewritten).sort_values("id").reset_index(drop=True),
    )


def test_repeated_rows_are_not_written_twice(tmp_path):

    path = tmp_path / "rows.csv"

    assert append_new_rows(path, frame_of([1, 2]), ["id"]) == 2
    assert append_new_rows(path, frame_of([1, 2]), ["id"]) == 0
    assert append_new_rows(path, frame_of([2, 3]), ["id"]) == 1
    assert len(pd.read_csv(path)) == 3


def test_a_changed_schema_rewrites_rather_than_misaligning(tmp_path):

    path = tmp_path / "rows.csv"
    append_new_rows(path, frame_of([1, 2]), ["id"])

    widened = pd.DataFrame([{"id": 3, "value": "a3", "extra": "new"}])
    append_new_rows(path, widened, ["id"])

    stored = pd.read_csv(path)

    assert list(stored.columns) == ["id", "value", "extra"]
    assert sorted(stored["id"]) == [1, 2, 3]
    # The rows that predate the new column keep their values.
    assert stored.loc[stored["id"] == 1, "value"].item() == "a1"


def test_empty_and_keyless_frames_are_ignored(tmp_path):

    path = tmp_path / "rows.csv"

    assert append_new_rows(path, pd.DataFrame(), ["id"]) == 0
    assert append_new_rows(path, None, ["id"]) == 0
    assert append_new_rows(path, pd.DataFrame([{"other": 1}]), ["id"]) == 0
    assert not path.exists()


def test_activity_trace_returns_only_events_it_has_not_stored(tmp_path, monkeypatch):
    """The return value feeds the database upsert, so repeats are the bill."""

    monkeypatch.setattr(
        "app.analytics.activity_trace.daily_path",
        lambda *_args: tmp_path / "activity_trace.csv",
    )
    row = {
        "Symbol": "AAPL",
        "Action Status": "AVOID",
        "Current ET": "2026-07-28 09:55:00",
    }

    first = write_daily_activity_trace("2026-07-28", scanner_rows=[row])
    repeat = write_daily_activity_trace("2026-07-28", scanner_rows=[row])

    assert first["events"], "the first scan has to store something"
    assert repeat["events"] == [], "an unchanged scan should cost nothing"
    assert repeat["rows"] == first["rows"]

    stored = pd.read_csv(tmp_path / "activity_trace.csv")
    assert len(stored) == first["rows"]
    assert stored["event_id"].is_unique
