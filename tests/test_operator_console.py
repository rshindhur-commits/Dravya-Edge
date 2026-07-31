import io
import json
import zipfile

import pandas as pd

from app.analytics import daily_review_export
from app.ui import components
from app.ui.pages import trading


def _stub_health(monkeypatch, **overrides):
    defaults = {
        "_engine_status": lambda: {"thread_alive": True, "status": "RUNNING",
                                   "scans": 12, "failures": 0},
        "_active_positions": lambda: [],
        "_telegram_rows": lambda day: [],
        "_entries_used": lambda day: 0,
        "_db_writes_active": lambda: True,
        "_minutes_since": lambda value: 3.0,
        "_is_post_market": lambda now=None: False,
    }
    defaults.update(overrides)
    for name, value in defaults.items():
        monkeypatch.setattr(trading, name, value)


def _tones(cells):
    return {label: tone for label, _value, tone in cells}


def test_health_cells_are_all_green_on_a_healthy_live_session(monkeypatch):
    _stub_health(monkeypatch)

    assert _tones(trading._health_cells({}))["Engine"] == "ok"
    assert _tones(trading._health_cells({}))["Last scan"] == "ok"
    assert _tones(trading._health_cells({}))["DB writes"] == "ok"


def test_a_dead_engine_and_dead_db_writer_both_read_as_faults(monkeypatch):
    _stub_health(
        monkeypatch,
        _engine_status=lambda: {"thread_alive": False, "status": "RUNNING"},
        _db_writes_active=lambda: False,
    )

    tones = _tones(trading._health_cells({}))

    assert tones["Engine"] == "bad"
    assert tones["DB writes"] == "bad"


def test_a_stale_scan_is_a_fault_in_session_and_expected_after_the_close(monkeypatch):
    """A quiet engine at 16:30 is correct behaviour; at 11:00 it is an outage."""
    _stub_health(monkeypatch, _minutes_since=lambda value: 90.0)
    assert _tones(trading._health_cells({}))["Last scan"] == "bad"

    _stub_health(monkeypatch, _minutes_since=lambda value: 90.0,
                 _is_post_market=lambda now=None: True)
    assert _tones(trading._health_cells({}))["Last scan"] == "neutral"


def test_scan_age_escalates_from_ok_through_warn_to_bad(monkeypatch):
    for minutes, expected in ((5.0, "ok"), (30.0, "warn"), (120.0, "bad")):
        _stub_health(monkeypatch, _minutes_since=lambda value, m=minutes: m)
        assert _tones(trading._health_cells({}))["Last scan"] == expected


def test_a_failed_telegram_send_and_a_reached_entry_cap_are_surfaced(monkeypatch):
    # `auto_paper_settings.json` is gitignored, so the cap has to be stated here
    # rather than read from whatever the machine happens to have.
    from app.runtime import paper_automation_support

    monkeypatch.setattr(
        paper_automation_support, "load_auto_paper_controls", lambda: {"max_daily": 3}
    )
    _stub_health(
        monkeypatch,
        _telegram_rows=lambda day: [{"event": "SENT"}, {"event": "FAILED"}],
        _entries_used=lambda day: 3,
    )

    cells = {label: (value, tone) for label, value, tone in trading._health_cells({})}

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
