import io
import json
import zipfile

import pandas as pd

from app.analytics import daily_review_export
from app.ui import components
from app.ui.pages import trading


class FakeContext:
    """Stands in for RenderContext: the health cards only read attributes."""

    def __init__(self, **overrides):
        self.state = {}
        self.engine = {"thread_alive": True, "status": "RUNNING", "scans": 12, "failures": 0}
        self.positions = []
        self.telegram = []
        self.entries_used = 0
        self.max_daily_entries = 3
        self.db_writes_active = True
        self.scan_age_minutes = 3.0
        self.post_market = False
        self.__dict__.update(overrides)


def _tones(cells):
    return {label: tone for label, _value, tone in cells}


def test_health_cells_are_all_green_on_a_healthy_live_session():
    tones = _tones(trading._health_cells(FakeContext()))

    assert tones["Engine"] == "ok"
    assert tones["Last scan"] == "ok"
    assert tones["DB writes"] == "ok"
    assert tones["Telegram"] == "ok"


def test_a_dead_engine_and_dead_db_writer_both_read_as_faults():
    tones = _tones(trading._health_cells(FakeContext(
        engine={"thread_alive": False, "status": "RUNNING"},
        db_writes_active=False,
    )))

    assert tones["Engine"] == "bad"
    assert tones["DB writes"] == "bad"


def test_a_stale_scan_is_a_fault_in_session_and_expected_after_the_close():
    """A quiet engine at 16:30 is correct behaviour; at 11:00 it is an outage."""
    in_session = FakeContext(scan_age_minutes=90.0, post_market=False)
    after_close = FakeContext(scan_age_minutes=90.0, post_market=True)

    assert _tones(trading._health_cells(in_session))["Last scan"] == "bad"
    assert _tones(trading._health_cells(after_close))["Last scan"] == "neutral"


def test_scan_age_escalates_from_ok_through_warn_to_bad():
    for minutes, expected in ((5.0, "ok"), (30.0, "warn"), (120.0, "bad")):
        context = FakeContext(scan_age_minutes=minutes)
        assert _tones(trading._health_cells(context))["Last scan"] == expected


def test_a_never_scanned_session_is_a_fault_but_a_quiet_evening_is_not():
    assert _tones(trading._health_cells(
        FakeContext(scan_age_minutes=None)
    ))["Last scan"] == "bad"
    assert _tones(trading._health_cells(
        FakeContext(scan_age_minutes=None, post_market=True)
    ))["Last scan"] == "neutral"


def test_a_failed_telegram_send_and_a_reached_entry_cap_are_surfaced():
    context = FakeContext(
        telegram=[{"event": "SENT"}, {"event": "FAILED"}],
        entries_used=3,
        max_daily_entries=3,
    )

    cells = {label: (value, tone) for label, value, tone in trading._health_cells(context)}

    assert cells["Telegram"] == ("1 sent / 1 failed", "bad")
    assert cells["Book"][1] == "warn"
    assert "3/3" in cells["Book"][0]


def test_position_tone_escalates_on_stop_proximity_and_exit_pressure():
    assert trading._position_tone({"rr_progress": 0.8}) == "ok"
    assert trading._position_tone({"rr_progress": -0.9}) == "bad"
    assert trading._position_tone({"last_exit_phase": "TREND_FAILURE"}) == "bad"
    assert trading._position_tone({"last_exit_confidence_score": 95}) == "warn"
    assert trading._position_tone({}) == "neutral"


def test_r_gauge_anchors_on_the_stop_not_on_zero():
    """Half a bar must mean breakeven, so the track runs -1R to +3R."""
    zero = components._r_gauge(0.0)
    assert "left:25.0%" in zero

    winner = components._r_gauge(1.0)
    assert "#22c55e" in winner and "left:25.0%" in winner

    loser = components._r_gauge(-0.5)
    assert "#ef4444" in loser

    assert "r-fill" not in components._r_gauge(None)


def test_r_gauge_clamps_beyond_the_track_instead_of_overflowing():
    assert "width:75.0%" in components._r_gauge(9.0)
    assert "r-fill" in components._r_gauge(-9.0)


def test_status_card_grid_falls_back_to_neutral_for_an_unknown_tone():
    rendered = []
    components.st = type("Stub", (), {
        "markdown": staticmethod(lambda html, **kwargs: rendered.append(html))
    })
    try:
        components.status_card_grid([("Engine", "RUNNING", "sparkly")])
    finally:
        import streamlit
        components.st = streamlit

    assert "compact-neutral" in rendered[0]


def test_card_values_are_escaped_rather_than_injected():
    rendered = []
    components.st = type("Stub", (), {
        "markdown": staticmethod(lambda html, **kwargs: rendered.append(html))
    })
    try:
        components.status_card_grid([("<script>", "</div><b>x", "ok")])
    finally:
        import streamlit
        components.st = streamlit

    assert "<script>" not in rendered[0]
    assert "&lt;script&gt;" in rendered[0]


def _seed_day(tmp_path):
    directory = tmp_path / "2026-07-31"
    directory.mkdir()
    pd.DataFrame([{"symbol": "NVDA"}]).to_csv(directory / "signal_lifecycle_events.csv", index=False)
    pd.DataFrame([{"symbol": "NVDA"}]).to_csv(directory / "trade_exit_snapshots.csv", index=False)
    (directory / "market_opportunity_audit.csv").write_text("symbol\nNVDA\n", encoding="utf-8")
    return directory


def test_review_export_carries_the_artifacts_only_this_container_holds(tmp_path, monkeypatch):
    """A redeploy wipes the container filesystem, and these live nowhere else --
    not in Postgres, and not in the generated frames."""
    directory = _seed_day(tmp_path)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "paper_trade_state.json").write_text('{"t1": {}}', encoding="utf-8")
    monkeypatch.setattr(daily_review_export, "state_path", lambda name: state_dir / name)

    archive, manifest = daily_review_export.build_daily_review_export(
        "2026-07-31", directory=directory
    )
    names = set(zipfile.ZipFile(io.BytesIO(archive)).namelist())

    assert "raw/signal_lifecycle_events.csv" in names
    assert "raw/trade_exit_snapshots.csv" in names
    assert "raw/market_opportunity_audit.csv" in names
    assert "state/paper_trade_state.json" in names
    assert manifest["artifacts"]["raw/signal_lifecycle_events.csv"]["available"] is True


def test_review_export_copies_raw_files_verbatim(tmp_path, monkeypatch):
    directory = _seed_day(tmp_path)
    monkeypatch.setattr(daily_review_export, "state_path", lambda name: tmp_path / "absent" / name)

    archive, _ = daily_review_export.build_daily_review_export(
        "2026-07-31", directory=directory
    )
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        assert bundle.read("raw/market_opportunity_audit.csv") == (
            directory / "market_opportunity_audit.csv"
        ).read_bytes()


def test_review_export_skips_empty_and_absent_artifacts(tmp_path, monkeypatch):
    directory = _seed_day(tmp_path)
    (directory / "engine_trade_events.csv").write_text("", encoding="utf-8")
    monkeypatch.setattr(daily_review_export, "state_path", lambda name: tmp_path / "absent" / name)

    archive, manifest = daily_review_export.build_daily_review_export(
        "2026-07-31", directory=directory
    )
    names = set(zipfile.ZipFile(io.BytesIO(archive)).namelist())

    assert "raw/engine_trade_events.csv" not in names
    assert "raw/candles_5m.csv" not in names
    assert "manifest.json" in names
    assert json.loads(
        zipfile.ZipFile(io.BytesIO(archive)).read("manifest.json")
    )["trading_day"] == "2026-07-31"
