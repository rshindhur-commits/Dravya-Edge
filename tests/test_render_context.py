"""The render context exists to stop one page render reading the same file
four times. These tests hold that property, and cover the data rules that moved
out of the page module with it."""

import pandas as pd

from app.ui import render_context
from app.ui.render_context import RenderContext


def _events(rows):
    return pd.DataFrame(rows)


def test_each_source_is_read_at_most_once_per_render(monkeypatch):
    """`_active_positions` was called four times per render -- health cards,
    book, risk monitor and market pulse -- each one a fresh disk read."""
    calls = {"positions": 0, "telegram": 0, "events": 0}

    monkeypatch.setattr(render_context, "active_positions",
                        lambda: calls.__setitem__("positions", calls["positions"] + 1) or [])
    monkeypatch.setattr(render_context, "telegram_rows",
                        lambda day: calls.__setitem__("telegram", calls["telegram"] + 1) or [])
    monkeypatch.setattr(render_context, "paper_events",
                        lambda day: calls.__setitem__("events", calls["events"] + 1) or pd.DataFrame())

    context = RenderContext(state={"trading_day": "2026-07-31"})
    for _ in range(4):
        context.positions
        context.telegram
        context.paper_events
    context.entries_used
    context.closed_trades

    assert calls == {"positions": 1, "telegram": 1, "events": 1}


def test_a_source_no_renderer_asks_for_is_never_read(monkeypatch):
    """The intraday board must not pay for the post-market card's data."""
    read = []
    monkeypatch.setattr(render_context, "paper_events",
                        lambda day: read.append(day) or pd.DataFrame())

    context = RenderContext(state={"trading_day": "2026-07-31"})
    context.trading_day

    assert read == []


def test_trading_day_prefers_explicit_value_then_scan_id_then_today():
    assert RenderContext(state={"trading_day": "2026-07-28"}).trading_day == "2026-07-28"
    assert RenderContext(state={"scan_id": "2026-07-28_155902"}).trading_day == "2026-07-28"
    assert len(RenderContext(state={}).trading_day) == 10


def test_entries_used_counts_the_day_even_when_the_open_row_is_missing():
    """The 2026-07-30 file holds an AUTO_EXIT whose OPEN row never landed;
    counting OPEN events reported zero entries for a day that took a trade."""
    events = _events([{
        "event_type": "AUTO_EXIT",
        "trade_key": "NVDA|O:NVDA260807C00197500|2026-07-31 10:58:46",
    }])

    assert render_context.entries_used(events, "2026-07-31") == 1


def test_entries_used_ignores_a_position_carried_in_from_a_previous_day():
    events = _events([
        {"event_type": "AUTO_EXIT", "trade_key": "NVDA|OPT|2026-07-30 15:10:00"},
        {"event_type": "OPEN", "trade_key": "CRWD|OPT|2026-07-31 11:36:33"},
    ])

    assert render_context.entries_used(events, "2026-07-31") == 1


def test_entries_used_survives_an_empty_or_shapeless_file():
    assert render_context.entries_used(pd.DataFrame(), "2026-07-31") == 0
    assert render_context.entries_used(_events([{"other": 1}]), "2026-07-31") == 0


def test_closed_trades_stitch_entry_time_back_onto_the_close_row():
    events = _events([
        {"event_type": "OPEN", "trade_key": "NVDA|OPT|2026-07-31 10:58:46",
         "symbol": "NVDA", "direction": "CALL",
         "event_time_et": "2026-07-31T10:58:46-04:00",
         "entry_price": 198.24, "exit_price": None, "r_multiple": None, "exit_reason": None},
        {"event_type": "AUTO_EXIT", "trade_key": "NVDA|OPT|2026-07-31 10:58:46",
         "symbol": "NVDA", "direction": "CALL",
         "event_time_et": "2026-07-31T11:11:25-04:00",
         "entry_price": 198.24, "exit_price": 197.5, "r_multiple": -0.74,
         "exit_reason": "VWAP invalidation"},
    ])

    trades = render_context.closed_trades(events)

    assert len(trades) == 1
    assert trades[0]["entry_time"] == "2026-07-31T10:58:46-04:00"
    assert trades[0]["exit_time"] == "2026-07-31T11:11:25-04:00"
    assert trades[0]["r_multiple"] == -0.74
    assert trades[0]["closed_how"] == "AUTO_EXIT"


def test_an_open_position_is_not_reported_as_a_closed_trade():
    events = _events([{
        "event_type": "OPEN", "trade_key": "NVDA|OPT|2026-07-31 12:57:59",
        "symbol": "NVDA", "event_time_et": "2026-07-31T12:57:59-04:00",
    }])

    assert render_context.closed_trades(events) == []


def test_a_close_without_its_open_row_still_reports_the_trade():
    events = _events([{
        "event_type": "AUTO_EXIT", "trade_key": "NVDA|OPT|2026-07-30 14:23:04",
        "symbol": "NVDA", "event_time_et": "2026-07-31T00:38:27-04:00",
        "exit_price": 196.839, "r_multiple": -4.12,
    }])

    trades = render_context.closed_trades(events)

    assert len(trades) == 1
    assert trades[0]["entry_time"] is None
    assert trades[0]["exit_time"] == "2026-07-31T00:38:27-04:00"


def test_post_market_switch_follows_the_close_and_the_weekend():
    from datetime import datetime

    et = render_context.ET_TZ
    assert not render_context.is_post_market(datetime(2026, 7, 31, 11, 0, tzinfo=et))
    assert render_context.is_post_market(datetime(2026, 7, 31, 16, 30, tzinfo=et))
    assert render_context.is_post_market(datetime(2026, 8, 1, 11, 0, tzinfo=et))


def test_delivered_trade_ids_come_only_from_successful_sends():
    context = RenderContext()
    context.__dict__["telegram"] = [
        {"event": "SENT", "trade_id": "t1"},
        {"event": "FAILED", "trade_id": "t2"},
        {"event": "ATTEMPT", "trade_id": "t3"},
        {"event": "SENT"},
    ]

    assert context.delivered_trade_ids == {"t1"}


def test_engine_label_names_which_engine_is_being_reported():
    """`Engine SLEEPING_WEEKEND · worker` read as one opaque string, and once
    scanning moved to Render the owner became the fact that mattered most."""

    assert render_context.engine_label({"owner": "worker"}) == "Worker engine (Render)"
    assert render_context.engine_label(
        {"owner": "dashboard"}) == "Dashboard engine (Streamlit)"
    assert render_context.engine_label({"owner": "worker"}, short=True) == "WORKER ENGINE"


def test_engine_label_infers_the_dashboard_only_from_a_local_thread():
    """A supervisor thread in this process is proof of which engine it is; an
    empty dict is not, and guessing there would name an engine that never ran."""

    assert render_context.engine_label({"thread_alive": True}) == "Dashboard engine (Streamlit)"
    assert render_context.engine_label({}) == "Unknown engine"
    assert render_context.engine_label(None) == "Unknown engine"


def test_engine_label_passes_through_an_owner_it_does_not_know():

    assert render_context.engine_label({"owner": "backfill"}) == "Backfill engine"
