"""An intraday position that outlives its session must be closed, not carried.

`restore_carried_intraday_positions` detected this case exactly and responded by
writing a warning string and putting the trade back in the book. It kept running.

`O:SMCI260814P00030000` opened 2026-08-05 on an INTRADAY profile and closed
2026-08-13 -- nine days, eight overnights, `updated_at` untouched for eight of
them. It was closed against a quote frozen at 2026-08-05 11:05 and stamped
`LIVE_QUOTE` with an age of 0.7 minutes, 22.7R past its stop, booked at -1.92%
when the contract had actually lost -99.5%. That trade and one like it are 90% of
all recorded losses, which contaminated every comparison drawn from live P&L.

Two properties are pinned. The position is **closed** rather than carried. And
the exit is **never labelled live** -- the trade is stamped
`RECONSTRUCTED_AT_FORCE_CLOSE` with `needs_repricing`, because the price it
closes on is the last one the trade recorded and is not a fill.

A MULTIDAY position must be untouched by all of this; it is supposed to carry.
"""

import unittest
from unittest.mock import patch

from app.state import trade_session_lifecycle as lifecycle


def _intraday_trade(opened="2026-08-05T10:11:00", **overrides):
    trade = {
        "symbol": "SMCI",
        "status": "OPEN",
        "direction": "PUT",
        "holding_profile": "INTRADAY",
        "opened_at_et": opened,
        "entry_price": 40.00,
        "current_price": 39.56,
        "stop_loss": 31.40,
        "initial_stop_loss": 31.40,
        "option_quote_freshness": "LIVE_QUOTE",
        "option_quote_age_minutes": 0.7,
    }
    trade.update(overrides)
    return trade


class ForceCloseTests(unittest.TestCase):

    def setUp(self):
        self.saved = {}

    def _run(self, state, trading_day="2026-08-13"):
        """Drive the function against an in-memory book."""

        closed = []

        def fake_close(symbol, close_price=None, exit_reason=None, **kwargs):
            for key, trade in state.items():
                if trade.get("symbol") == symbol and trade.get("status") == "OPEN":
                    trade["status"] = "CLOSED"
                    trade["close_price"] = close_price
                    trade["exit_reason"] = exit_reason
                    closed.append(trade)
                    return trade
            return None

        with patch.object(lifecycle, "load_paper_trades", return_value=state), \
             patch.object(lifecycle, "save_paper_trades",
                          side_effect=lambda s: self.saved.update(s)), \
             patch.object(lifecycle, "get_session_id", return_value="S-2026-08-13"), \
             patch("app.state.paper_trade_manager.close_paper_trade", fake_close):

            result = lifecycle.restore_carried_intraday_positions(trading_day)

        return result, closed

    def test_an_orphaned_intraday_position_is_closed(self):

        state = {"k1": _intraday_trade()}

        _result, closed = self._run(state)

        self.assertEqual(len(closed), 1)
        self.assertEqual(state["k1"]["status"], "CLOSED")
        self.assertEqual(closed[0]["exit_reason"], "ORPHANED_INTRADAY_FORCE_CLOSE")

    def test_the_exit_is_never_labelled_a_live_quote(self):
        """The specific defect: a 191-hour-old price stamped LIVE_QUOTE."""

        state = {"k1": _intraday_trade()}

        self._run(state)

        self.assertEqual(
            state["k1"]["option_quote_freshness"], "RECONSTRUCTED_AT_FORCE_CLOSE"
        )
        self.assertIsNone(state["k1"]["option_quote_age_minutes"])
        self.assertTrue(state["k1"]["needs_repricing"])

    def test_it_closes_on_the_last_recorded_price(self):

        state = {"k1": _intraday_trade()}

        _result, closed = self._run(state)

        self.assertEqual(closed[0]["close_price"], 39.56)

    def test_the_reason_names_both_dates(self):
        """So the record says how long it ran, not merely that it was orphaned."""

        state = {"k1": _intraday_trade()}

        self._run(state)

        reason = state["k1"]["force_close_reason"]
        self.assertIn("2026-08-05", reason)
        self.assertIn("2026-08-13", reason)


class LeaveAloneTests(unittest.TestCase):
    """Everything this must not touch."""

    def _run(self, state, trading_day="2026-08-13"):

        closed = []

        def fake_close(symbol, **kwargs):
            closed.append(symbol)
            return None

        with patch.object(lifecycle, "load_paper_trades", return_value=state), \
             patch.object(lifecycle, "save_paper_trades"), \
             patch.object(lifecycle, "get_session_id", return_value="S"), \
             patch("app.state.paper_trade_manager.close_paper_trade", fake_close):

            lifecycle.restore_carried_intraday_positions(trading_day)

        return closed

    def test_a_multiday_position_is_left_alone(self):
        """It sets force_eod_exit=False and is supposed to carry."""

        state = {"k": _intraday_trade(holding_profile="MULTIDAY")}

        self.assertEqual(self._run(state), [])
        self.assertEqual(state["k"]["status"], "OPEN")

    def test_a_position_opened_today_is_left_alone(self):

        state = {"k": _intraday_trade(opened="2026-08-13T10:11:00")}

        self.assertEqual(self._run(state), [])
        self.assertEqual(state["k"]["status"], "OPEN")

    def test_an_already_closed_trade_is_left_alone(self):

        state = {"k": _intraday_trade(status="CLOSED")}

        self.assertEqual(self._run(state), [])


class SwitchTests(unittest.TestCase):

    def test_it_is_on_by_default(self):

        self.assertTrue(lifecycle.force_close_orphaned_intraday())

    def test_disabling_restores_the_old_carry_behaviour(self, ):

        state = {"k": _intraday_trade()}
        closed = []

        with patch.object(lifecycle, "load_paper_trades", return_value=state), \
             patch.object(lifecycle, "save_paper_trades"), \
             patch.object(lifecycle, "get_session_id", return_value="S"), \
             patch.object(lifecycle, "force_close_orphaned_intraday",
                          return_value=False), \
             patch("app.state.paper_trade_manager.close_paper_trade",
                   side_effect=lambda s, **k: closed.append(s)):

            carried = lifecycle.restore_carried_intraday_positions("2026-08-13")

        self.assertEqual(closed, [])
        self.assertEqual(len(carried), 1)
        self.assertEqual(state["k"]["status"], "OPEN")
        self.assertIn("overnight_carry_warning", state["k"])


class LastPriceTests(unittest.TestCase):

    def test_it_prefers_the_most_recent_price(self):

        self.assertEqual(
            lifecycle._last_known_price(
                {"current_price": 39.56, "entry_price": 40.0}
            ),
            39.56,
        )

    def test_it_falls_back_through_to_entry(self):

        self.assertEqual(
            lifecycle._last_known_price({"entry_price": 40.0}), 40.0
        )

    def test_it_returns_none_when_nothing_is_usable(self):

        self.assertIsNone(lifecycle._last_known_price({"current_price": 0}))
        self.assertIsNone(lifecycle._last_known_price({"current_price": "nan"}))


if __name__ == "__main__":
    unittest.main()
