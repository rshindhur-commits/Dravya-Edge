"""Recovering the book after a restart must not be a one-shot attempt.

`paper_trade_state.json` sits on an ephemeral filesystem, so a fresh container
starts with an empty book and Postgres is the only record of what is open.
`_restore_lost_positions_once` reads it back on the first scan of a process.

It latched before the read was attempted, so a container that started during a
database blip concluded the book was empty and never asked again. What that
costs is documented in `restore_open_trades_from_db` because it has happened: an
NVDA put opened at 14:23 vanished from state, a second NVDA position opened at
14:42 against the invisible first, and the daily cap counted from the same lost
file -- a limit of 3 produced 6 trades.
"""

import unittest
from unittest.mock import patch

from app.db.paper_trade_repository import PaperTradeRepository
from app.runtime import paper_position_lifecycle as lifecycle


class RestoreRetriesAfterAFailedReadTests(unittest.TestCase):

    def setUp(self):
        lifecycle._positions_restored = False
        self.addCleanup(setattr, lifecycle, "_positions_restored", False)

    def test_a_failed_read_does_not_latch(self):

        with patch("app.state.paper_trade_manager.restore_open_trades_from_db",
                   return_value=None):

            self.assertEqual(lifecycle._restore_lost_positions_once(), [])

        self.assertFalse(lifecycle._positions_restored)

    def test_a_failed_read_is_retried_and_can_succeed(self):
        """The blip is the whole point: the next scan must still recover."""

        with patch("app.state.paper_trade_manager.restore_open_trades_from_db",
                   side_effect=[None, [{"symbol": "NVDA"}]]) as restore:

            first = lifecycle._restore_lost_positions_once()
            second = lifecycle._restore_lost_positions_once()

        self.assertEqual(first, [])
        self.assertEqual([trade["symbol"] for trade in second], ["NVDA"])
        self.assertEqual(restore.call_count, 2)

    def test_a_successful_read_latches_so_it_runs_once_per_process(self):

        with patch("app.state.paper_trade_manager.restore_open_trades_from_db",
                   return_value=[{"symbol": "AMD"}]) as restore:

            lifecycle._restore_lost_positions_once()
            lifecycle._restore_lost_positions_once()

        restore.assert_called_once()

    def test_an_empty_book_is_a_real_answer_and_latches(self):
        """No open positions is a genuine state, not a failure, and must not
        keep the query running on every scan."""

        with patch("app.state.paper_trade_manager.restore_open_trades_from_db",
                   return_value=[]) as restore:

            lifecycle._restore_lost_positions_once()
            lifecycle._restore_lost_positions_once()

        restore.assert_called_once()
        self.assertTrue(lifecycle._positions_restored)


class FetchOpenSeparatesFailureFromEmptyTests(unittest.TestCase):
    """"the database says nothing is open" and "the database did not answer"
    lead to opposite actions, so they cannot share a return value."""

    def test_a_failed_read_is_none(self):

        with patch.object(PaperTradeRepository, "_fetch_optional", return_value=None):

            self.assertIsNone(PaperTradeRepository().fetch_open())

    def test_no_open_rows_is_an_empty_list(self):

        with patch.object(PaperTradeRepository, "_fetch_optional", return_value=[]):

            self.assertEqual(PaperTradeRepository().fetch_open(), [])

    def test_open_rows_carry_their_key_and_payload(self):

        with patch.object(PaperTradeRepository, "_fetch_optional",
                          return_value=[{"trade_key": "K", "payload": {"symbol": "SPY"}}]):

            rows = PaperTradeRepository().fetch_open()

        self.assertEqual(rows, [{"trade_key": "K", "payload": {"symbol": "SPY"}}])


if __name__ == "__main__":
    unittest.main()
