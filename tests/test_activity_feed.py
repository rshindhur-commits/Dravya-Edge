"""The activity timeline has two sources: `activity_trace.csv` when it exists,
and a reconstruction from four partial sources when it does not. Both paths
parse timestamps the scanner wrote with `%Z`."""

import json

import pandas as pd
import pytest

from app.ui.pages import activity


class FakeContext:
    def __init__(self, trading_day="2026-07-31", telegram=None, df=None):
        self.trading_day = trading_day
        self.telegram = telegram or []
        self.df = df if df is not None else pd.DataFrame()


@pytest.fixture
def daily_dir(tmp_path, monkeypatch):
    directory = tmp_path / "data" / "daily" / "2026-07-31"
    directory.mkdir(parents=True)
    monkeypatch.setattr(activity, "ROOT_DIR", tmp_path)
    return directory


def test_the_trace_is_the_whole_story_when_it_exists(daily_dir):
    pd.DataFrame([
        {"time": "2026-07-31 09:35:00 EDT", "symbol": "NVDA",
         "category": "Scanner", "event": "ENTER_PAPER", "context": "ok"},
        {"time": "2026-07-31 09:40:00 EDT", "symbol": "CRWD",
         "category": "Paper", "event": "BLOCKED", "context": "RR"},
    ]).to_csv(daily_dir / "activity_trace.csv", index=False)
    # Present but must be ignored while the trace exists.
    pd.DataFrame([{"timestamp": "2026-07-31 10:00:00", "symbol": "AAPL",
                   "decision": "OPENED"}]).to_csv(
        daily_dir / "auto_paper_decisions.csv", index=False)

    rows = activity.activity_rows(FakeContext())

    assert list(rows["Symbol"]) == ["CRWD", "NVDA"], "newest first"
    assert "AAPL" not in set(rows["Symbol"])
    assert list(rows["Marker"]) == ["YELLOW", "GREEN"], "BLOCKED then ENTER_PAPER"


def test_zone_abbreviations_in_the_trace_sort_correctly(daily_dir):
    """`2026-07-31 00:38:19 EDT` is what the scanner writes. Dropping the zone
    would shift it four hours and could reorder the feed."""
    pd.DataFrame([
        {"time": "2026-07-31 09:00:00 EDT", "symbol": "EARLY",
         "category": "Scanner", "event": "WAIT"},
        {"time": "2026-07-31T14:30:00+00:00", "symbol": "LATER",
         "category": "Scanner", "event": "WAIT"},
    ]).to_csv(daily_dir / "activity_trace.csv", index=False)

    rows = activity.activity_rows(FakeContext())

    # 09:00 EDT is 13:00Z, so the 14:30Z row is genuinely later.
    assert list(rows["Symbol"]) == ["LATER", "EARLY"]


def test_an_unreadable_time_is_kept_rather_than_dropped(daily_dir):
    pd.DataFrame([
        {"time": "garbage", "symbol": "ODD", "category": "System", "event": "WAIT"},
        {"time": "2026-07-31 09:00:00 EDT", "symbol": "NVDA",
         "category": "Scanner", "event": "WAIT"},
    ]).to_csv(daily_dir / "activity_trace.csv", index=False)

    rows = activity.activity_rows(FakeContext())

    assert len(rows) == 2
    assert "ODD" in set(rows["Symbol"])


def test_the_timeline_is_rebuilt_from_four_sources_without_a_trace(daily_dir):
    (daily_dir / "trade_timeline.jsonl").write_text(
        json.dumps({"occurred_at": "2026-07-31T13:00:00+00:00",
                    "event_type": "EntryOpened",
                    "payload": {"symbol": "NVDA", "entry_reason": "EMA"}}) + "\n",
        encoding="utf-8",
    )
    pd.DataFrame([{"timestamp": "2026-07-31 09:40:00 EDT", "symbol": "CRWD",
                   "decision": "BLOCKED", "reason": "RR_BELOW_THRESHOLD",
                   "blocked_by": "RR"}]).to_csv(
        daily_dir / "auto_paper_decisions.csv", index=False)
    telegram = [{"observed_at_utc": "2026-07-31T14:00:00+00:00", "symbol": "NVDA",
                 "message_type": "NEW TRADE", "event": "SENT"}]
    scanner = pd.DataFrame([{"Symbol": "AAPL", "Action Status": "WAIT",
                             "Current ET": "2026-07-31 09:50:00 EDT",
                             "Action Reason": "no setup"}])

    rows = activity.activity_rows(FakeContext(telegram=telegram, df=scanner))

    assert set(rows["Symbol"]) == {"NVDA", "CRWD", "AAPL"}
    assert set(rows["Origin"]) == {
        "Trade lifecycle", "Auto-paper gate", "Telegram dispatcher", "Scanner decision"
    }
    assert rows.iloc[0]["Symbol"] == "NVDA", "telegram send at 14:00Z is newest"


def test_system_level_skips_are_left_out_of_the_rebuilt_timeline(daily_dir):
    pd.DataFrame([
        {"timestamp": "2026-07-31 09:40:00 EDT", "symbol": "SYSTEM", "decision": "SKIPPED"},
        {"timestamp": "2026-07-31 09:41:00 EDT", "symbol": "NVDA", "decision": "OPENED"},
    ]).to_csv(daily_dir / "auto_paper_decisions.csv", index=False)

    rows = activity.activity_rows(FakeContext())

    assert list(rows["Symbol"]) == ["NVDA"]


def test_a_day_with_nothing_recorded_returns_an_empty_frame(daily_dir):
    assert activity.activity_rows(FakeContext()).empty


def test_an_empty_trace_file_falls_through_to_reconstruction(daily_dir):
    (daily_dir / "activity_trace.csv").write_text("", encoding="utf-8")
    pd.DataFrame([{"timestamp": "2026-07-31 09:41:00 EDT", "symbol": "NVDA",
                   "decision": "OPENED"}]).to_csv(
        daily_dir / "auto_paper_decisions.csv", index=False)

    rows = activity.activity_rows(FakeContext())

    assert list(rows["Symbol"]) == ["NVDA"]
