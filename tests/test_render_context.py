"""The render context exists to stop one page render reading the same file
four times. These tests hold that property, and cover the data rules that moved
out of the page module with it."""

import unittest
from unittest.mock import patch

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


class DatabaseStateIsReachabilityNotIntentTests(unittest.TestCase):
    """"DB writes on" was true of a container that could not reach Postgres, so
    the one indicator that should have caught a blind process vouched for it."""

    def setUp(self):
        render_context._DB_STATE_CACHE.update({"checked_at": 0.0, "state": None})
        self.addCleanup(render_context._DB_STATE_CACHE.update,
                        {"checked_at": 0.0, "state": None})

    def test_a_configured_but_unreachable_database_is_not_on(self):
        with patch("app.db.persistence.db_writes_enabled", return_value=True),              patch("app.db.persistence.database_reachable", return_value=False):

            self.assertEqual(render_context.database_state(), "UNREACHABLE")

    def test_a_reachable_database_is_on(self):
        with patch("app.db.persistence.db_writes_enabled", return_value=True),              patch("app.db.persistence.database_reachable", return_value=True):

            self.assertEqual(render_context.database_state(), "ON")

    def test_deliberately_switched_off_is_distinct_from_broken(self):
        """OFF is a choice and UNREACHABLE is a fault; collapsing them would put
        an operator on the wrong hunt."""

        with patch("app.db.persistence.db_writes_enabled", return_value=False):

            self.assertEqual(render_context.database_state(), "OFF")

    def test_the_result_is_cached_so_a_rerun_is_not_a_round_trip(self):
        with patch("app.db.persistence.db_writes_enabled", return_value=True),              patch("app.db.persistence.database_reachable",
                   return_value=True) as reachable:

            render_context.database_state()
            render_context.database_state()

        reachable.assert_called_once()


class SidebarSurvivesAStaleRenderContextTests(unittest.TestCase):
    """Streamlit Cloud has twice served a new dashboard.py against an older
    render_context.py. `_render_system_status` runs before page routing, so an
    ImportError there is not a broken panel -- it is a blank site. It happened
    with `scan_engine_heartbeats`, was fixed locally, then happened again with
    `database_state` because the guard was never made general."""

    def test_a_missing_name_falls_back_instead_of_raising(self):
        from app import dashboard

        sentinel = object()
        got = dashboard._render_context_symbol("no_such_name_at_all", sentinel)

        self.assertIs(got, sentinel)

    def test_an_existing_name_is_returned(self):
        from app import dashboard
        from app.ui import render_context

        got = dashboard._render_context_symbol("database_state", None)

        self.assertIs(got, render_context.database_state)

    def test_the_engine_label_fallback_still_names_the_owner(self):
        from app import dashboard

        self.assertEqual(
            dashboard._fallback_engine_label({"owner": "worker"}), "Worker engine")
        self.assertEqual(
            dashboard._fallback_engine_label({"owner": "worker"}, short=True),
            "WORKER ENGINE")
        self.assertEqual(dashboard._fallback_engine_label({}), "Engine")


class CrossHostReadsTests(unittest.TestCase):
    """With scanning on Render, the dashboard's container never writes the files
    this page used to read. Every panel below rendered a confident empty state
    while the worker held a book and sent alerts."""

    def test_open_positions_come_from_postgres_when_the_local_file_is_empty(self):
        with patch.object(render_context, "_local_open_positions", return_value={}),              patch.object(render_context, "_remote_open_positions",
                          return_value={"NVDA|X|2026-08-03 10:00:00":
                                        {"symbol": "NVDA", "status": "OPEN"}}):

            positions = render_context.active_positions()

        self.assertEqual([p["symbol"] for p in positions], ["NVDA"])

    def test_local_state_wins_so_a_lagging_mirror_cannot_resurrect_a_close(self):
        """The mirror is written through a background queue and can lag. A
        position closed locally must not reappear because it has not caught up."""

        key = "NVDA|X|2026-08-03 10:00:00"

        with patch.object(render_context, "_local_open_positions",
                          return_value={key: {"symbol": "NVDA", "status": "PAUSED"}}),              patch.object(render_context, "_remote_open_positions",
                          return_value={key: {"symbol": "NVDA", "status": "OPEN"}}):

            positions = render_context.active_positions()

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["status"], "PAUSED")

    def test_an_unreadable_database_leaves_local_positions_alone(self):
        with patch.object(render_context, "_local_open_positions",
                          return_value={"K": {"symbol": "AMD", "status": "OPEN"}}),              patch("app.db.paper_trade_repository.PaperTradeRepository.fetch_open",
                   return_value=None):

            positions = render_context.active_positions()

        self.assertEqual([p["symbol"] for p in positions], ["AMD"])

    def test_the_entry_cap_takes_the_larger_of_file_and_database(self):
        """Both undercount in different ways -- the file misses another host, the
        mirror lags -- and under-reporting against a cap is the harmful direction."""

        with patch.object(render_context, "_remote_entries_used", return_value=3):

            self.assertEqual(render_context.entries_used(None, "2026-08-03"), 3)

        with patch.object(render_context, "_remote_entries_used", return_value=None):

            self.assertEqual(render_context.entries_used(None, "2026-08-03"), 0)

    def test_telegram_counts_fall_back_to_the_dispatch_table(self):
        with patch.object(render_context, "read_jsonl", return_value=[]),              patch.object(render_context, "_remote_telegram_rows",
                          return_value=[{"event": "SENT", "trade_id": "t1"},
                                        {"event": "FAILED", "trade_id": "t2"}]):

            rows = render_context.telegram_rows("2026-08-03")

        self.assertEqual([r["event"] for r in rows], ["SENT", "FAILED"])

    def test_a_queued_but_undelivered_send_is_not_counted_as_sent(self):
        """ATTEMPTED means handed off, not delivered. Counting it as a success is
        how a stuck queue would read as a healthy one."""

        from app.db.telegram_dispatch_repository import TelegramDispatchRepository

        with patch.object(TelegramDispatchRepository, "_fetch_optional",
                          return_value=[{"status": "ATTEMPTED", "trade_id": "t1"}]):

            rows = TelegramDispatchRepository().fetch_for_day("2026-08-03")

        self.assertEqual(rows[0]["event"], "ATTEMPT")

    def test_closed_trades_come_from_postgres_when_the_csv_is_missing(self):
        with patch.object(render_context, "_remote_closed_trades",
                          return_value=[{"symbol": "SPY"}]) as remote:

            trades = render_context.closed_trades_for_day(None, "2026-08-03")

        remote.assert_called_once()
        self.assertEqual(trades[0]["symbol"], "SPY")


class ArchiveReadDistinguishesFailureFromAbsenceTests(unittest.TestCase):
    """Replay draws "nothing was archived" from an empty result, so a swallowed
    read failure would render an outage as a day the scanner never ran. The
    repository's own comment said these must be distinguishable; until now the
    return value could not express it."""

    def test_strict_mode_returns_none_when_the_read_fails(self):
        from app.db.scanner_snapshot_repository import ScannerSnapshotRepository

        with patch("app.db.scanner_snapshot_repository.get_engine",
                   side_effect=RuntimeError("no database")):

            self.assertIsNone(
                ScannerSnapshotRepository().load_day("2026-07-31", strict=True))

    def test_the_default_still_returns_a_list_for_existing_callers(self):
        """HSR falls back to the local snapshot folder and iterates the result,
        so the default must not start handing it None."""

        from app.db.scanner_snapshot_repository import ScannerSnapshotRepository

        with patch("app.db.scanner_snapshot_repository.get_engine",
                   side_effect=RuntimeError("no database")):

            self.assertEqual(
                ScannerSnapshotRepository().load_day("2026-07-31"), [])
