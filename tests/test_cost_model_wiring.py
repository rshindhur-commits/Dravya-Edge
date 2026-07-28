import csv
import os
import unittest
from datetime import datetime
from unittest.mock import patch

from app.analytics.trade_snapshot import (
    TRADE_EXIT_SNAPSHOT_COLUMNS,
    append_trade_exit_snapshot,
)
from app.economics.trade_costs import cost_model_enabled
from app.state.paper_trade_manager import _cost_model_fields, _exit_option_quote
from app.trades.exit_snapshot import create_exit_snapshot


TICKER = "O:NVDA260821C00205000"

TRADE = {
    "option_ticker": TICKER,
    "option_bid": 4.95,
    "option_ask": 5.05,
    "option_mid": 5.00,
    "option_contracts": 1,
    "entry_price": 196.1337,
    "stop_loss": 195.424912,
    "opened_at_et": "2026-07-27T16:00:00-04:00",
}

ROW_MATCHING = {
    "Option Ticker": TICKER,
    "Option Bid": 5.50,
    "Option Ask": 5.60,
}

CLOSED_AT = datetime.fromisoformat("2026-07-27T18:00:00-04:00")


class TestExitOptionQuote(unittest.TestCase):

    def test_uses_scanner_row_quote_when_the_contract_matches(self):
        quote = _exit_option_quote(TRADE, ROW_MATCHING)

        self.assertEqual(quote["source"], "SCANNER_ROW")
        self.assertAlmostEqual(quote["mid"], 5.55, places=6)

    def test_refuses_a_quote_for_a_different_contract(self):
        row = dict(ROW_MATCHING)
        row["Option Ticker"] = "O:NVDA260821C00210000"

        quote = _exit_option_quote(TRADE, row)

        self.assertEqual(quote["source"], "UNAVAILABLE")
        self.assertIsNone(quote["mid"])

    def test_missing_row_or_quote_is_unavailable_never_the_entry_mid(self):
        for row in [None, {}, {"Option Ticker": TICKER}, {"Option Ticker": TICKER, "Option Bid": 0, "Option Ask": 0}]:
            quote = _exit_option_quote(TRADE, row)

            self.assertEqual(quote["source"], "UNAVAILABLE")
            self.assertIsNone(quote["mid"])
            self.assertNotEqual(quote["mid"], TRADE["option_mid"])

    def test_crossed_row_quote_is_rejected(self):
        row = {"Option Ticker": TICKER, "Option Bid": 5.60, "Option Ask": 5.50}

        self.assertEqual(_exit_option_quote(TRADE, row)["source"], "UNAVAILABLE")


class TestCostModelFlag(unittest.TestCase):

    def test_flag_defaults_on_after_s1_6(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COST_MODEL_ENABLED", None)

            self.assertTrue(cost_model_enabled())

    def test_flag_can_still_be_turned_off(self):
        with patch.dict(os.environ, {"COST_MODEL_ENABLED": "false"}):
            self.assertFalse(cost_model_enabled())

    def test_emits_nothing_when_disabled(self):
        with patch.dict(os.environ, {"COST_MODEL_ENABLED": "false"}):
            fields = _cost_model_fields(
                TRADE, 197.769364, "HARD_TARGET", {"mid": None}, CLOSED_AT
            )

            self.assertEqual(fields, {})

    def test_emits_dual_fields_when_enabled(self):
        with patch.dict(os.environ, {"COST_MODEL_ENABLED": "true", "COST_TICK_SIZE": "0.05"}):
            fields = _cost_model_fields(
                TRADE,
                197.769364,
                "HARD_TARGET",
                {"mid": None, "bid": None, "ask": None},
                CLOSED_AT,
            )

            self.assertEqual(fields["pnl_source"], "BS_ESTIMATE")
            self.assertIsNotNone(fields["r_multiple_net"])
            self.assertIsNotNone(fields["r_multiple_gross"])
            self.assertIsNotNone(fields["pnl_underlying_est"])
            self.assertLess(fields["r_multiple_net"], fields["r_multiple_gross"])

    def test_real_exit_quote_is_preferred_over_the_estimate(self):
        with patch.dict(os.environ, {"COST_MODEL_ENABLED": "true", "COST_TICK_SIZE": "0.05"}):
            quote = _exit_option_quote(TRADE, ROW_MATCHING)
            fields = _cost_model_fields(
                TRADE, 197.769364, "HARD_TARGET", quote, CLOSED_AT
            )

            self.assertEqual(fields["pnl_source"], "ACTUAL_QUOTE")
            self.assertEqual(fields["pnl_confidence"], "HIGH")
            self.assertIsNotNone(fields["r_multiple_net"])

    def test_never_raises_on_a_malformed_trade(self):
        with patch.dict(os.environ, {"COST_MODEL_ENABLED": "true"}):
            fields = _cost_model_fields(
                {"option_ticker": "garbage"}, 100.0, "HARD_STOP", {"mid": None}, CLOSED_AT
            )

            self.assertEqual(fields["pnl_source"], "UNAVAILABLE")
            self.assertIsNone(fields.get("r_multiple_net"))

    def test_legacy_fields_are_never_among_the_emitted_keys(self):
        with patch.dict(os.environ, {"COST_MODEL_ENABLED": "true", "COST_TICK_SIZE": "0.05"}):
            fields = _cost_model_fields(
                TRADE, 197.769364, "HARD_TARGET", {"mid": None}, CLOSED_AT
            )

            for protected in ["r_multiple", "pnl_pct", "outcome", "option_mid"]:
                self.assertNotIn(protected, fields)


class TestLinearPathDeprecation(unittest.TestCase):

    def test_linear_risk_at_stop_path_warns_once(self):
        import io
        from contextlib import redirect_stdout

        import app.dashboard as dashboard

        dashboard._LINEAR_PNL_WARNED = False

        first = io.StringIO()
        with redirect_stdout(first):
            dashboard._warn_linear_pnl_deprecated()

        second = io.StringIO()
        with redirect_stdout(second):
            dashboard._warn_linear_pnl_deprecated()

        self.assertIn("[DEPRECATED]", first.getvalue())
        self.assertIn("risk_at_stop", first.getvalue())
        self.assertEqual(second.getvalue(), "", "must warn once per process, not per render")

    def test_linear_path_still_exists_and_was_not_deleted(self):
        import app.dashboard as dashboard

        self.assertTrue(callable(dashboard._daily_realized_real_pnl))


class TestExitSnapshotCarriesOptionFields(unittest.TestCase):

    def test_snapshot_includes_option_economics(self):
        trade = dict(TRADE)
        trade.update({
            "trade_id": "t1",
            "r_multiple": 2.3,
            "r_multiple_net": 1.17,
            "pnl_option_est": 48.58,
            "cost_total": 12.70,
            "pnl_source": "ACTUAL_QUOTE",
            "option_current_mid": 5.55,
            "option_exit_quote_source": "SCANNER_ROW",
        })

        record = create_exit_snapshot(trade, {}).to_record()

        self.assertEqual(record["option_ticker"], TICKER)
        self.assertAlmostEqual(record["option_entry_mid"], 5.00, places=6)
        self.assertAlmostEqual(record["option_exit_mid"], 5.55, places=6)
        self.assertAlmostEqual(record["r_multiple_net"], 1.17, places=6)
        self.assertEqual(record["pnl_source"], "ACTUAL_QUOTE")

    def test_legacy_final_r_field_is_untouched(self):
        trade = dict(TRADE)
        trade.update({"trade_id": "t1", "r_multiple": 2.3, "r_multiple_net": 1.17})

        record = create_exit_snapshot(trade, {}).to_record()

        self.assertAlmostEqual(record["final_r"], 2.3, places=6)

    def test_new_columns_are_registered_for_persistence(self):
        for column in ["r_multiple_net", "pnl_option_est", "cost_total", "pnl_source"]:
            self.assertIn(column, TRADE_EXIT_SNAPSHOT_COLUMNS)


class TestSnapshotHeaderCompatibility(unittest.TestCase):

    def test_existing_narrow_header_is_not_misaligned(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "2026-07-27" / "trade_exit_snapshots.csv"
            path.parent.mkdir(parents=True)

            legacy = TRADE_EXIT_SNAPSHOT_COLUMNS[:30]

            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=legacy)
                writer.writeheader()
                writer.writerow({column: "x" for column in legacy})

            with patch("app.analytics.trade_snapshot.daily_path", return_value=path):
                append_trade_exit_snapshot("2026-07-27", {"trade_key": "t2", "r_multiple_net": 1.17})

            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["trade_key"], "t2")
            self.assertIsNone(rows[1].get(None), "row must not overflow the header")


if __name__ == "__main__":
    unittest.main()
