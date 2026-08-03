"""A forward run must not trade what it could not buy.

``_open_trade`` runs the entry path and then resolves a contract. When a
selector is configured and finds nothing, the signal is untradeable, and the
distinction matters twice over: the trade would otherwise contribute an R to
the results for a position that could never have been placed, and it would hold
the single ``max_open_positions`` slot while doing it, hiding signals that were
tradeable behind one that was not.

Parity runs are the other case -- they leave ``contract_selector`` at None and
pin the contract live actually bought -- so the gate must key on the selector
being configured, not on the ticker being absent.
"""

import unittest
from unittest.mock import patch

import pandas as pd

from app.backtesting.replay_engine import ReplayConfig, _open_trade


def _frames():
    """A frame shaped enough for the entry path; the gates are patched."""

    index = pd.date_range(
        "2026-07-29 09:30", periods=60, freq="5min", tz="America/New_York"
    )

    return pd.DataFrame(
        {
            "Open": 100.0,
            "High": 101.0,
            "Low": 99.0,
            "Close": 100.0,
            "Volume": 1000.0,
        },
        index=index,
    )


class ForwardSelectionTests(unittest.TestCase):

    def setUp(self):

        self.df = _frames()

        self.entry = patch(
            "app.backtesting.replay_engine.detect_entry",
            return_value={"entry_type": "BREAKOUT"},
        )
        self.risk = patch(
            "app.backtesting.replay_engine.calculate_risk",
            return_value={
                "trade_allowed": True,
                "stop_loss": 99.0,
                "take_profit": 102.0,
            },
        )

        self.entry.start()
        self.risk.start()
        self.addCleanup(self.entry.stop)
        self.addCleanup(self.risk.stop)

    def _open(self, config):

        return _open_trade(
            "NVDA",
            pd.Timestamp("2026-07-29 11:00", tz="America/New_York"),
            "2026-07-29_110000",
            self.df,
            self.df,
            {},
            config,
        )

    def test_a_selector_that_finds_nothing_means_no_trade(self):

        config = ReplayConfig(contract_selector=lambda *_: None)

        self.assertIsNone(self._open(config))

    def test_parity_runs_still_open_without_a_selector(self):
        """contract_selector None is the pinned-contract parity path."""

        config = ReplayConfig(contract_selector=None)

        trade = self._open(config)

        self.assertIsNotNone(trade)
        self.assertIsNone(trade.option_ticker)

    def test_a_resolved_contract_still_opens(self):

        config = ReplayConfig(contract_selector=lambda *_: "O:NVDA260814C00100000")

        quote = {"bid": 4.00, "ask": 4.10, "mid": 4.05, "spread_pct": 2.5}

        with patch(
            "app.backtesting.replay_engine.quote_at", return_value=quote
        ), patch(
            "app.backtesting.replay_engine.is_tradeable",
            return_value=(True, None),
        ):

            trade = self._open(config)

        self.assertIsNotNone(trade)
        self.assertEqual(trade.option_ticker, "O:NVDA260814C00100000")


if __name__ == "__main__":

    unittest.main()
