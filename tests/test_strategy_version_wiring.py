"""S2.5 -- verifies strategy_version is actually stamped where it's supposed
to be, not just that the utility function works in isolation.

`open_paper_trade` has real side effects gated by env (`DB_WRITE_ENABLED`,
Telegram) that this suite must never trigger for real -- see
docs/specs/S2.4-parallel-run-procedure.md for why that matters in this repo's
.env specifically. Every I/O boundary is mocked; only the trade-dict
construction itself is exercised for real.
"""
import unittest
from unittest.mock import MagicMock, patch

from app.alerts.telegram_alerts import _alert_strategy_version
from app.analytics.trade_snapshot import TRADE_EXIT_SNAPSHOT_COLUMNS
from app.state.paper_trade_manager import PAPER_TRADE_EVENT_COLUMNS, open_paper_trade
from app.trades.exit_snapshot import create_exit_snapshot
from app.versioning.strategy_version import UNVERSIONED, compute_strategy_version


class TestOpenPaperTradeStampsTheCurrentVersion(unittest.TestCase):

    def test_new_trade_carries_compute_strategy_version(self):
        with patch("app.state.paper_trade_manager.load_paper_trades", return_value={}), \
             patch("app.state.paper_trade_manager.save_paper_trades"), \
             patch("app.state.paper_trade_manager._append_paper_trade_event"), \
             patch("app.trades.entry_snapshot.create_entry_snapshot") as entry_snapshot, \
             patch("app.trades.timeline.append_trade_timeline_event", return_value={}), \
             patch("app.runtime.get_runtime_scheduler") as scheduler, \
             patch("app.db.artifact_persistence.persist_timeline_event"):

            entry_snapshot.return_value = MagicMock(trade_id="t1", entered_at=None, to_record=lambda: {})
            scheduler.return_value.submit_normal = MagicMock()

            trade = open_paper_trade(
                symbol="NVDA",
                direction="CALL",
                entry_price=196.13,
                stop_loss=195.42,
                take_profit=197.77,
                entry_type="EMA_PULLBACK",
                option_ticker="O:NVDA260821C00205000",
                option_bid=4.95,
                option_ask=5.05,
            )

        self.assertEqual(trade["strategy_version"], compute_strategy_version())
        self.assertEqual(len(trade["strategy_version"]), 12)


class TestAlertStrategyVersionFallback(unittest.TestCase):

    def test_uses_the_trades_own_frozen_stamp(self):
        trade = {"strategy_version": "abc123def456"}

        self.assertEqual(_alert_strategy_version(trade), "abc123def456")

    def test_falls_back_to_unversioned_for_a_legacy_trade(self):
        trade = {"symbol": "NVDA"}  # no strategy_version key at all

        self.assertEqual(_alert_strategy_version(trade), UNVERSIONED)

    def test_none_trade_does_not_raise(self):
        self.assertEqual(_alert_strategy_version(None), UNVERSIONED)

    def test_does_not_recompute_the_current_version(self):
        # A stale trade's alert must report what decided IT, not today's code.
        stale = {"strategy_version": "0000deadbeef"}

        self.assertNotEqual(_alert_strategy_version(stale), compute_strategy_version())
        self.assertEqual(_alert_strategy_version(stale), "0000deadbeef")


class TestExitSnapshotBackfillRule(unittest.TestCase):

    def test_legacy_trade_with_no_stamp_gets_the_unversioned_sentinel(self):
        trade = {"trade_id": "t1", "r_multiple": 1.0}

        record = create_exit_snapshot(trade, {}).to_record()

        self.assertEqual(record["strategy_version"], UNVERSIONED)

    def test_versioned_trade_keeps_its_own_stamp(self):
        trade = {"trade_id": "t1", "r_multiple": 1.0, "strategy_version": "abc123def456"}

        record = create_exit_snapshot(trade, {}).to_record()

        self.assertEqual(record["strategy_version"], "abc123def456")


class TestEvidenceColumnsIncludeStrategyVersion(unittest.TestCase):

    def test_trade_exit_snapshot_columns(self):
        self.assertIn("strategy_version", TRADE_EXIT_SNAPSHOT_COLUMNS)

    def test_paper_trade_event_columns(self):
        self.assertIn("strategy_version", PAPER_TRADE_EVENT_COLUMNS)


if __name__ == "__main__":
    unittest.main()
