import unittest
from unittest.mock import patch

import pandas as pd

from app.main import (
    _build_candidate_funnel,
    _dispatch_telegram_entry_alerts,
    _finalize_scan_outputs,
    _json_list,
)
from app.runtime.scan_generation import ScanGeneration


class ScannerDiagnosticsTests(unittest.TestCase):

    def test_json_list_parses_liquidity_attempts(self):

        attempts = _json_list(
            '[{"source": "active", "code": "WIDE_SPREAD"}]'
        )

        self.assertEqual(attempts[0]["source"], "active")
        self.assertEqual(attempts[0]["code"], "WIDE_SPREAD")

    def test_candidate_funnel_counts_scanner_stages(self):

        df_results = pd.DataFrame([
            {
                "Final Signal": "HIGH CONVICTION BULLISH",
                "Entry": "BREAKOUT_LONG",
                "Candidate Direction": "CALL",
                "Setup Valid": True,
                "Trade Allowed": True,
                "Option Ticker": "NVDA_CALL",
                "Option Liquidity Passed": True,
                "Affordable": True,
                "Action Status": "ENTER_PAPER",
            },
            {
                "Final Signal": "NEUTRAL",
                "Entry": "NO_ENTRY",
                "Candidate Direction": "NONE",
                "Setup Valid": False,
                "Trade Allowed": False,
                "Option Ticker": None,
                "Option Liquidity Passed": False,
                "Affordable": False,
                "Action Status": "WAIT",
            },
        ])

        funnel = _build_candidate_funnel(
            df_results,
            {"sent_count": 1}
        )

        self.assertEqual(funnel["scanned"], 2)
        self.assertEqual(funnel["directional"], 1)
        self.assertEqual(funnel["entry_ready"], 1)
        self.assertEqual(funnel["risk_passed"], 1)
        self.assertEqual(funnel["option_selected"], 1)
        self.assertEqual(funnel["liquidity_passed"], 1)
        self.assertEqual(funnel["affordability_passed"], 1)
        self.assertEqual(funnel["ema_rejection_short"], 0)
        self.assertEqual(funnel["enter_paper"], 1)
        self.assertEqual(funnel["telegram"], 1)

    def test_candidate_funnel_counts_ema_rejection_short(self):

        df_results = pd.DataFrame([
            {
                "Final Signal": "BEARISH",
                "Entry": "EMA_REJECTION_SHORT",
                "Candidate Direction": "PUT",
                "Setup Valid": True,
                "Trade Allowed": True,
                "Option Ticker": "AMD_PUT",
                "Option Liquidity Passed": True,
                "Affordable": True,
                "Action Status": "ENTER_PAPER",
            },
            {
                "Final Signal": "BEARISH",
                "Entry": "VWAP_REJECTION",
                "Candidate Direction": "PUT",
                "Setup Valid": True,
                "Trade Allowed": True,
                "Option Ticker": "TSLA_PUT",
                "Option Liquidity Passed": True,
                "Affordable": True,
                "Action Status": "ENTER_PAPER",
            },
        ])

        funnel = _build_candidate_funnel(df_results)

        self.assertEqual(funnel["ema_rejection_short"], 1)

    def test_telegram_dispatch_returns_enter_paper_and_sent_counts(self):

        df_results = pd.DataFrame([
            {
                "Symbol": "NVDA",
                "Final Signal": "HIGH CONVICTION BULLISH",
                "Action Status": "ENTER_PAPER",
                "Entry": "BREAKOUT_LONG",
                "Risk Reward": 2.4,
                "Option Ticker": "NVDA_CALL",
                "Candidate Direction": "CALL",
                "Option Quality Score": 90,
                "Option Spread %": 4,
                "Option Quote Freshness": "LIVE_QUOTE",
                "15m Score": 50,
                "Setup %": 87,
                "ENTRY_GATE_SETUP": 87,
                "ENTRY_GATE_MIN_SETUP": 70,
                "ENTRY_GATE_RESULT": "PASS",
                "Alignment Score": 5,
                "RS Rank Score": 2,
                "Relative Volume": 2,
            },
            {
                "Symbol": "AAPL",
                "Final Signal": "BULLISH",
                "Action Status": "WAIT",
                "Entry": "NO_ENTRY",
                "Risk Reward": 0,
                "Option Ticker": None,
                "Candidate Direction": "NONE",
                "Option Quality Score": None,
                "Option Spread %": None,
                "Option Quote Freshness": None,
                "15m Score": 50,
                "Alignment Score": 1,
                "RS Rank Score": 0,
                "Relative Volume": 1,
            },
        ])

        with patch(
            "app.main.maybe_send_scanner_entry_alert",
            side_effect=[
                {"sent": True, "reason": "SENT"},
                {"sent": False, "reason": "NOT_ACTIONABLE_STATUS"},
            ],
        ) as send_alert:

            summary = _dispatch_telegram_entry_alerts(df_results)

        self.assertEqual(summary["enter_paper_count"], 1)
        self.assertEqual(summary["attempted_count"], 2)
        self.assertEqual(summary["sent_count"], 1)
        self.assertEqual(summary["blocked_count"], 1)
        self.assertEqual(summary["reasons"]["SENT"], 1)
        self.assertEqual(summary["reasons"]["NOT_ACTIONABLE_STATUS"], 1)
        nvda_call = next(
            call
            for call in send_alert.call_args_list
            if call.kwargs["symbol"] == "NVDA"
        )
        self.assertEqual(
            nvda_call.kwargs["setup_score"],
            87
        )
        self.assertEqual(df_results.loc[0, "Telegram Eligibility"], "SENT")
        self.assertIsNone(df_results.loc[0, "Telegram Block Reason"])
        self.assertTrue(df_results.loc[0, "Telegram Sent"])
        self.assertEqual(
            df_results.loc[1, "Telegram Eligibility"],
            "NOT_ACTIONABLE_STATUS"
        )
        self.assertEqual(
            df_results.loc[1, "Telegram Block Reason"],
            "NOT_ACTIONABLE_STATUS"
        )
        self.assertFalse(df_results.loc[1, "Telegram Sent"])

    def test_finalize_scan_outputs_persists_and_queues_cache_jobs(self):

        df_results = pd.DataFrame([
            {
                "Symbol": "NVDA",
                "Action Status": "ENTER_PAPER",
                "Final Signal": "BULLISH",
                "Entry": "EMA_PULLBACK",
                "Candidate Direction": "CALL",
                "Risk Reward": 2.5,
                "Option Quote Freshness": "LIVE_QUOTE",
            }
        ])

        with patch(
            "app.main._add_candidate_persistence",
            side_effect=lambda df, *_args, **_kwargs: df
        ), patch(
            "app.main._add_relative_strength_rankings",
            side_effect=lambda df: df
        ), patch(
            "app.main._dispatch_telegram_entry_alerts",
            return_value={"sent_count": 1}
        ), patch(
            "app.main._persist_scan_outputs"
        ) as persist, patch(
            "app.main.get_runtime_scheduler"
        ) as scheduler_factory:

            scheduler = scheduler_factory.return_value
            _finalize_scan_outputs(
                df_results=df_results,
                table=None,
                generation=ScanGeneration.new("scan"),
                trading_day="2026-07-18",
                scan_id="scan",
                scanner_watchlist=["NVDA"],
                symbol_runtimes={"NVDA": 1.0},
                symbol_failures={},
                scan_runtime_sec=2.0,
                output_file="scanner_output.xlsx",
                foreground_timings={},
                observed_at=pd.Timestamp("2026-07-18T10:00:00")
            )

        persist.assert_called_once()
        self.assertEqual(scheduler.submit_normal.call_count, 1)
        self.assertEqual(scheduler.submit_low.call_count, 3)


if __name__ == "__main__":

    unittest.main()