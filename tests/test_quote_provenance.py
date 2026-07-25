from datetime import datetime, timezone

from app.analytics.candidate_snapshot_writer import (
    SNAPSHOT_COLUMNS,
    normalize_candidate_row,
)
from app.analytics.quote_attribution import build_quote_attribution
from app.options.live_options_chain import _extract_quote_fields
from app.options.option_metrics import classify_quote_freshness
from app.storage.signal_lifecycle_store import _event_from_row


def test_quote_provenance_survives_snapshot_and_lifecycle_mapping():
    row = {
        "Symbol": "NVDA",
        "Option Quote Timestamp": "2026-07-22T14:01:00+00:00",
        "Option Quote Checked At": "2026-07-22T14:01:30+00:00",
        "Option Quote Timeframe": "REALTIME",
        "Option Quote Source": "quotes_endpoint",
        "Option Quote Timestamp Field": "last_updated",
        "Option Quote Age Minutes": 0.5,
        "Option Quote Age Seconds": 30,
        "Option Quote Allowed Age Seconds": 1800,
        "Option Quote Freshness Reason": "AGE_WITHIN_ALLOWED_AGE",
    }

    snapshot = normalize_candidate_row(row, scan_id="scan-1")
    event = _event_from_row(
        row,
        trading_day="2026-07-22",
        session_id="paper_validation_2026-07-22",
        scan_id="scan-1",
        observed_at=datetime(2026, 7, 22, 10, 1),
        market_session="AUTO_ENTRY_WINDOW",
        is_auto_entry_window=True,
    )

    for record in [snapshot, event]:
        assert record["option_quote_timestamp"] == "2026-07-22T14:01:00+00:00"
        assert record["option_quote_checked_at"] == "2026-07-22T14:01:30+00:00"
        assert record["option_quote_timeframe"] == "REALTIME"
        assert record["option_quote_source"] == "quotes_endpoint"
        assert record["option_quote_timestamp_field"] == "last_updated"
        assert record["option_quote_age_minutes"] == 0.5
        assert record["option_quote_age_seconds"] == 30
        assert record["option_quote_allowed_age_seconds"] == 1800
        assert record["option_quote_freshness_reason"] == "AGE_WITHIN_ALLOWED_AGE"


def test_entry_optimizer_fields_survive_snapshot_normalization():
    snapshot = normalize_candidate_row({
        "Symbol": "NVDA",
        "Entry Priority Adjustment": 35,
        "Expected Remaining Trend": 86,
        "Projected Entry Grade": "A",
        "Ranking Score": 93.5,
    }, scan_id="scan-1")

    assert snapshot["entry_priority_adjustment"] == 35
    assert snapshot["expected_remaining_trend"] == 86
    assert snapshot["projected_entry_grade"] == "A"
    assert snapshot["ranking_score"] == 93.5
    assert {
        "entry_priority_adjustment",
        "expected_remaining_trend",
        "projected_entry_grade",
        "ranking_score",
    }.issubset(SNAPSHOT_COLUMNS)


def test_quote_freshness_reports_current_time_age_threshold_and_reason():
    diagnostics = classify_quote_freshness(
        "2026-07-22T13:41:28+00:00",
        max_quote_age_minutes=0.5,
        now_utc=datetime(2026, 7, 22, 13, 42, 11, tzinfo=timezone.utc),
    )

    assert diagnostics["quote_checked_at_utc"] == "2026-07-22T13:42:11+00:00"
    assert diagnostics["quote_age_seconds"] == 43.0
    assert diagnostics["quote_allowed_age_seconds"] == 30.0
    assert diagnostics["quote_freshness"] == "STALE_QUOTE"
    assert diagnostics["quote_freshness_reason"] == "AGE_EXCEEDS_ALLOWED_AGE"


def test_quote_attribution_persists_timestamp_field_and_stale_decision():

    fields = _extract_quote_fields(
        {"sip_timestamp": 1_721_655_688_000_000_000},
        "quotes_endpoint"
    )
    assert fields["quote_timestamp_field"] == "sip_timestamp"

    attribution = build_quote_attribution(
        [{
            "Symbol": "AAPL",
            "Option Ticker": "O:AAPL260821C00200000",
            "Current ET": "2026-07-22T13:42:11+00:00",
            "Option Quote Timestamp": "2026-07-22T13:41:28+00:00",
            "Option Quote Age Seconds": 43,
            "Option Quote Timestamp Field": "sip_timestamp",
            "Option Quote Source": "quotes_endpoint",
            "Option Quote Allowed Age Seconds": 30,
            "Option Quote Freshness": "STALE_QUOTE",
            "Option Quote Freshness Reason": "AGE_EXCEEDS_ALLOWED_AGE",
        }],
        "2026-07-22",
        "2026-07-22_134211",
        datetime(2026, 7, 22, 13, 42, 11, tzinfo=timezone.utc)
    )

    assert len(attribution) == 1
    record = attribution.iloc[0]
    assert record["source_timestamp_field"] == "sip_timestamp"
    assert record["final_classification"] == "STALE_QUOTE"
    assert record["allowed_age_seconds"] == 30