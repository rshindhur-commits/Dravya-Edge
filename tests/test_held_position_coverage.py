"""Open paper positions must always reach the scanner's exit evaluation.

A position whose symbol is skipped or never scanned goes a whole scan with no
exit evaluation, which is how an unnoticed blown stop happens.
"""

import unittest
from unittest.mock import patch

import pandas as pd

from app.indicators.technical_indicators import MIN_5M_BARS_FOR_15M_INDICATORS
from app.main import (
    HELD_POSITION_LOOKBACK_DAYS,
    _held_symbol_data_shortfall,
    _include_open_position_symbols,
    _market_data_for_held_symbol,
    _market_data_unusable,
    _open_position_symbols,
)
from app.runtime.paper_position_lifecycle import (
    EXIT_NOT_EVALUATED_MARKER,
    _unmanaged_positions,
)
from app.ui.pages.trading import _unmanaged_position_alerts


def _result(success=True, rows=MIN_5M_BARS_FOR_15M_INDICATORS, error=None):
    return {
        "symbol": "NVDA",
        "data": pd.DataFrame({"Close": [1.0] * rows}) if rows else pd.DataFrame(),
        "runtime": 0.01,
        "success": success,
        "error": error,
    }


class HeldPositionCoverageTests(unittest.TestCase):

    def test_open_position_symbols_ignores_closed_trades(self):

        trades = {
            "a": {"symbol": "NVDA", "status": "OPEN"},
            "b": {"symbol": "AAPL", "status": "CLOSED"},
            "c": {"symbol": "msft", "status": "open"},
        }

        with patch(
            "app.state.paper_trade_manager.load_paper_trades",
            return_value=trades
        ):

            symbols = _open_position_symbols()

        self.assertEqual(symbols, {"NVDA", "MSFT"})

    def test_open_position_symbols_survives_unreadable_state(self):

        with patch(
            "app.state.paper_trade_manager.load_paper_trades",
            side_effect=ValueError("corrupt")
        ):

            self.assertEqual(_open_position_symbols(), set())

    def test_off_watchlist_held_symbol_is_added_to_the_scan(self):

        watchlist = _include_open_position_symbols(
            ["QQQ", "SPY"],
            {"NVDA", "SPY"}
        )

        self.assertEqual(watchlist, ["QQQ", "SPY", "NVDA"])

    def test_watchlist_is_unchanged_when_all_held_symbols_are_scanned(self):

        watchlist = _include_open_position_symbols(["QQQ", "SPY"], {"SPY"})

        self.assertEqual(watchlist, ["QQQ", "SPY"])

    def test_market_data_unusable_detects_failure_and_empty_data(self):

        self.assertTrue(_market_data_unusable(None))
        self.assertTrue(_market_data_unusable(_result(success=False, error="429")))
        self.assertTrue(_market_data_unusable(_result(rows=0)))
        self.assertFalse(_market_data_unusable(_result()))

    def test_shortfall_distinguishes_unusable_from_too_short(self):

        self.assertEqual(_held_symbol_data_shortfall(None), "unusable")
        self.assertEqual(
            _held_symbol_data_shortfall(_result(success=False, error="429")),
            "unusable",
        )
        self.assertEqual(
            _held_symbol_data_shortfall(
                _result(rows=MIN_5M_BARS_FOR_15M_INDICATORS - 1)
            ),
            "too short for 15m indicators",
        )
        self.assertIsNone(_held_symbol_data_shortfall(_result()))

    def test_held_symbol_retries_and_recovers_market_data(self):

        failed = _result(success=False, error="timeout")
        recovered = _result()

        with patch(
            "app.main.process_symbol",
            return_value=recovered
        ) as process_symbol:

            result = _market_data_for_held_symbol("NVDA", failed)

        self.assertIs(result, recovered)
        process_symbol.assert_called_once_with(
            "NVDA",
            force_refresh=True,
            days_back=HELD_POSITION_LOOKBACK_DAYS,
        )

    def test_short_history_triggers_a_longer_lookback_refetch(self):
        """A refresh cannot fix short data; only a longer lookback can."""

        short = _result(rows=MIN_5M_BARS_FOR_15M_INDICATORS - 1)
        recovered = _result(rows=MIN_5M_BARS_FOR_15M_INDICATORS * 4)

        with patch(
            "app.main.process_symbol",
            return_value=recovered
        ) as process_symbol:

            result = _market_data_for_held_symbol("NVDA", short)

        self.assertIs(result, recovered)
        self.assertEqual(
            process_symbol.call_args.kwargs["days_back"],
            HELD_POSITION_LOOKBACK_DAYS,
        )

    def test_short_history_that_cannot_be_extended_is_reported_not_evaluated(self):

        short = _result(rows=MIN_5M_BARS_FOR_15M_INDICATORS - 1)

        with patch(
            "app.main.process_symbol",
            return_value=_result(rows=MIN_5M_BARS_FOR_15M_INDICATORS - 1)
        ):

            result = _market_data_for_held_symbol("NVDA", short)

        self.assertIs(result, short)

        # The scanner then writes the honest marker, which the sweep detects.
        unmanaged = _unmanaged_positions(
            [{"symbol": "NVDA", "status": "OPEN"}],
            {"NVDA": {"Live Exit Reason": EXIT_NOT_EVALUATED_MARKER}},
        )

        self.assertEqual(unmanaged, [("NVDA", "INSUFFICIENT_15M_DATA")])

    def test_managed_position_is_never_flagged_as_unmanaged(self):

        unmanaged = _unmanaged_positions(
            [{"symbol": "NVDA", "status": "OPEN"}],
            {"NVDA": {"Live Exit Reason": "Hold"}},
        )

        self.assertEqual(unmanaged, [])

    def test_held_symbol_retry_is_skipped_when_data_is_already_good(self):

        good = _result()

        with patch("app.main.process_symbol") as process_symbol:

            result = _market_data_for_held_symbol("NVDA", good)

        self.assertIs(result, good)
        process_symbol.assert_not_called()

    def test_failed_retry_keeps_the_original_result(self):

        failed = _result(success=False, error="timeout")

        with patch(
            "app.main.process_symbol",
            return_value=_result(success=False, error="timeout again")
        ):

            result = _market_data_for_held_symbol("NVDA", failed)

        self.assertIs(result, failed)

    def test_risk_monitor_surfaces_unmanaged_positions(self):

        alerts = _unmanaged_position_alerts({
            "scanner_health": {"paper_lifecycle": {"unmanaged": ["NVDA"]}}
        })

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][0], "NVDA")

    def test_risk_monitor_is_quiet_when_every_position_was_managed(self):

        self.assertEqual(_unmanaged_position_alerts({}), [])
        self.assertEqual(
            _unmanaged_position_alerts({"scanner_health": {"paper_lifecycle": {}}}),
            [],
        )


if __name__ == "__main__":
    unittest.main()
