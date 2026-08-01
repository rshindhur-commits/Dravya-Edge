"""The exit analysis moved into Postgres, and the review that reads it.

`trend_capture_analysis.csv` held the indicator state at exit and the analysis
built on it, on the container filesystem a redeploy wipes. Nothing in Postgres
held it at trade grain, so 2026-07-31's exit review was simply gone.
"""

import pandas as pd
import pytest

from app.analytics import post_market_review as review
from app.db import trade_exit_analysis_repository as repository


def _row(**overrides):
    row = {
        "Trading Day": "2026-08-03",
        "Trade Key": "NVDA|OPT|2026-08-03 10:58:46",
        "Session ID": "paper_2026-08-03",
        "Symbol": "NVDA",
        "Direction": "CALL",
        "Setup": "EMA_PULLBACK",
        "Entry Time": "2026-08-03 10:58:46-04:00",
        "Exit Time": "2026-08-03 11:30:00-04:00",
        "Entry Price": 197.96,
        "Exit Price": 198.55,
        "Bars Held": 6,
        "Available Move": 4.0,
        "Captured Move": 0.59,
        "Left On Table": 3.41,
        "Trend Capture %": 14.8,
        "Maximum Favorable Excursion": 1.66,
        "Maximum Adverse Excursion": 0.1,
        "EMA9 At Exit": 198.6,
        "EMA20 At Exit": 198.2,
        "VWAP At Exit": 198.1,
        "MACD At Exit": 0.25,
        "RSI At Exit": 61.0,
        "ATR At Exit": 0.27,
        "Trend Health Score": 75.0,
        "Trend Health State": "HEALTHY",
        "Exit Reason": "EMA9 invalidation (long)",
        "Exit Quality": "POOR",
        "Exit Verdict": "EXIT_TOO_EARLY",
        "Exit Comments": "Trend remained healthy after exit.",
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------
# Record mapping
# --------------------------------------------------------------------------

def test_the_indicator_state_at_exit_becomes_typed_columns():
    """ema9, vwap, macd, rsi, atr and bars_held existed only in the CSV."""
    record = repository.to_record("2026-08-03", "NVDA|OPT|1", _row())

    assert record["ema9"] == 198.6
    assert record["vwap"] == 198.1
    assert record["macd"] == 0.25
    assert record["rsi"] == 61.0
    assert record["atr"] == 0.27
    assert record["bars_held"] == 6
    assert record["trend_capture_pct"] == 14.8
    assert record["left_on_table"] == 3.41
    assert record["exit_verdict"] == "EXIT_TOO_EARLY"


def test_nan_never_reaches_the_json_payload():
    """`json.dumps` emits bare NaN, which Postgres rejects outright -- it broke
    the whole insert on the first real row."""
    record = repository.to_record(
        "2026-08-03", "NVDA|OPT|1",
        _row(**{"MACD Histogram At Exit": float("nan"), "RSI At Exit": float("nan")}),
    )

    assert "NaN" not in record["payload"]
    assert record["rsi"] is None


def test_timestamps_are_normalised_including_the_legacy_zone_format():
    record = repository.to_record(
        "2026-08-03", "NVDA|OPT|1",
        _row(**{"Entry Time": "2026-08-03 10:58:46 EDT"}),
    )

    assert record["entry_time"].hour == 14  # 10:58 EDT is 14:58 UTC


def test_the_whole_csv_row_is_kept_in_payload_against_schema_drift():
    record = repository.to_record(
        "2026-08-03", "NVDA|OPT|1", _row(**{"Some Future Column": 7}),
    )

    assert '"Some Future Column": 7' in record["payload"]


# --------------------------------------------------------------------------
# The review itself
# --------------------------------------------------------------------------

@pytest.fixture
def one_trade(monkeypatch):
    record = repository.to_record("2026-08-03", "NVDA|OPT|1", _row())
    monkeypatch.setattr(review, "load_exit_rows", lambda day: [record])
    return record


def test_the_review_names_what_was_left_behind(one_trade):
    html, summary = review.build_review("2026-08-03")

    assert summary["trades"] == 1
    assert summary["exits_too_early"] == 1
    assert "3.41 points on the table" in html
    assert "got out too early" in html.lower()


def test_the_review_explains_the_exit_rule_in_plain_words(one_trade):
    html, _ = review.build_review("2026-08-03")

    assert "slipped back under its short-term average" in html
    assert "EMA9 invalidation" not in html.split("<footer>")[0]


def test_the_review_describes_the_indicators_rather_than_tabulating_them(one_trade):
    html, _ = review.build_review("2026-08-03")

    assert "RSI 61 (neutral)" in html
    assert "trend read as healthy (75/100)" in html


def test_a_nonsense_capture_percentage_is_not_quoted_at_the_reader():
    """A losing 2026-07-30 row stores -2211% capture; printing that helps nobody."""
    losing = repository.to_record("2026-07-30", "NVDA|OPT|2", _row(**{
        "Captured Move": -3.54, "Available Move": 0.16,
        "Left On Table": 3.7, "Trend Capture %": -2211.87,
    }))

    sentence = review._what_was_available(losing)

    assert "-2211" not in sentence
    assert "wrong side" in sentence


def test_a_day_with_no_completed_trades_still_produces_a_readable_page(monkeypatch):
    monkeypatch.setattr(review, "load_exit_rows", lambda day: [])

    html, summary = review.build_review("2026-08-03")

    assert summary["trades"] == 0
    assert "No trades were completed" in html
    assert "nothing to review" in html


def test_apostrophes_are_not_double_escaped(one_trade):
    html, _ = review.build_review("2026-08-03")

    assert "&#x27;" not in html
    assert "day's average price" in html


def test_the_day_summary_totals_points_and_flags_early_exits():
    rows = [
        repository.to_record("2026-08-03", "a", _row(**{"Captured Move": 0.59, "Left On Table": 3.41})),
        repository.to_record("2026-08-03", "b", _row(**{
            "Captured Move": -0.74, "Left On Table": 0.0, "Exit Verdict": "GOOD_EXIT",
        })),
    ]

    summary = review.summarise(rows)

    assert summary["trades"] == 2
    assert summary["winners"] == 1
    assert summary["losers"] == 1
    assert summary["net_points"] == pytest.approx(-0.15)
    assert summary["left_on_table"] == pytest.approx(3.41)
    assert summary["exits_too_early"] == 1


def test_the_review_falls_back_to_the_csv_when_the_database_is_unreachable(tmp_path, monkeypatch):
    """The file is still the live artifact; the database is the copy that
    survives the container."""
    path = tmp_path / "trend_capture_analysis.csv"
    pd.DataFrame([_row()]).to_csv(path, index=False)
    monkeypatch.setattr(review, "daily_path", lambda day, name: path)
    monkeypatch.setattr(
        repository.TradeExitAnalysisRepository, "load_day", lambda self, day: []
    )

    rows = review.load_exit_rows("2026-08-03")

    assert len(rows) == 1
    assert rows[0]["ema9"] == 198.6
