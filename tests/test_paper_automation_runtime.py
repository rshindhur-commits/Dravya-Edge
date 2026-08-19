import os
import unittest
from unittest.mock import patch

import pandas as pd

from app.runtime.paper_automation import run_auto_paper_entries
from app.runtime.paper_position_lifecycle import run_paper_position_lifecycle
from app.runtime.paper_automation_support import (
    _auto_paper_entry_reason,
    _record_auto_paper_decision,
    load_auto_paper_controls,
    should_record_auto_paper_session_skip,
    _scanner_block_reason,
)


class PaperAutomationRuntimeTests(unittest.TestCase):

    def test_control_loader_reads_the_environment(self):
        """Env is the only source. The settings file used to win over it.

        The file was written by the dashboard sidebar and read by the scanner --
        fine while one process did both, wrong the moment SCAN_ENGINE_OWNER moved
        scanning to the Render worker, which has its own empty disk. Every
        control was inert while still displaying its value.
        """

        with patch.dict(
            os.environ,
            {
                "AUTO_PAPER_ENABLED": "true",
                "MAX_DAILY_ENTRIES": "2",
                "AUTO_PAPER_MIN_SETUP": "81",
                "AUTO_PAPER_MIN_RR": "2.3",
                "AUTO_PAPER_DIRECTION": "Calls",
                "AUTO_PAPER_EOD_CLOSE_ENABLED": "true",
            },
            clear=False,
        ):
            controls = load_auto_paper_controls()

        self.assertTrue(controls["auto_paper_enabled"])
        self.assertEqual(controls["max_daily"], 2)
        self.assertEqual(controls["min_setup"], 81.0)
        self.assertEqual(controls["min_rr"], 2.3)
        self.assertEqual(controls["direction"], "Calls")
        self.assertTrue(controls["eod_close_enabled"])

    def test_eod_close_defaults_on_when_unset(self):
        """An unset variable must not carry day trades overnight."""

        with patch.dict(os.environ, {}, clear=True):
            controls = load_auto_paper_controls()

        self.assertTrue(controls["eod_close_enabled"])
        self.assertEqual(controls["direction"], "Both")

    def test_the_daily_entry_default_matches_the_documented_intent(self):
        """The code default is what production runs; .env never reaches it.

        Every DAILY_AUTO_PAPER_LIMIT_REACHED block in the ledger is 2026-07-31,
        all AMZN, on a day that opened three trades -- because the running value
        was the code default of 3 while `.env` said 5. It is the only position cap
        that has ever cost a trade, and AUTO_PAPER_MAX_CANDIDATE_RANK was already
        raised to 5 specifically to match it.
        """

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_auto_paper_controls()["max_daily"], 5)

    def test_auto_paper_entries_logs_disabled_candidates(self):

        df = pd.DataFrame([
            {
                "Symbol": "NVDA",
                "Action Status": "ENTER_PAPER",
            }
        ])
        controls = {
            "auto_paper_enabled": False,
            "max_daily": 1,
        }

        with patch(
            "app.runtime.paper_automation_support._paper_trade_candidates",
            return_value=df
        ), patch(
            "app.runtime.paper_automation_support._record_auto_paper_decision"
        ) as record_decision, patch(
            "app.state.paper_trade_manager.load_paper_trades",
            return_value={}
        ):

            result = run_auto_paper_entries(df, controls)

        self.assertEqual(result, [])
        record_decision.assert_called_once()
        self.assertEqual(record_decision.call_args.args[1], "SKIPPED")

    def test_filtered_enter_paper_candidate_receives_terminal_audit(self):

        row = {
            "Symbol": "NFLX",
            "Action Status": "ENTER_PAPER",
            "Setup Valid": False,
            "Candidate Direction": "PUT",
            "Candidate Entry Price": 100,
            "Candidate Stop Price": 101,
            "Candidate Target Price": 98,
            "Candidate RR": 2.0,
            "Entry": "VWAP_REJECTION",
            "Next Condition": "-",
            "Live Chart Checklist": "-",
            "Realtime Ready": True,
        }
        controls = {"auto_paper_enabled": True, "max_daily": 3}

        with patch(
            "app.runtime.paper_automation_support.auto_paper_session_block_reason",
            return_value=None,
        ), patch(
            "app.runtime.paper_automation_support._record_auto_paper_decision",
        ) as record_decision, patch(
            "app.state.paper_trade_manager.load_paper_trades",
            return_value={},
        ):
            frame = pd.DataFrame([row])
            result = run_auto_paper_entries(frame, controls)

        self.assertEqual(result, [])
        self.assertEqual(frame.loc[0, "Execution Eligibility"], "NOT_EXECUTED")
        self.assertEqual(frame.loc[0, "Execution Outcome"], "SKIPPED")
        self.assertEqual(frame.loc[0, "Execution Reason"], "SETUP_INVALID")
        self.assertEqual(frame.loc[0, "Trade Status"], "NOT_CREATED")
        self.assertEqual(frame.loc[0, "Telegram Status"], "NO_LIFECYCLE_EVENT")
        self.assertEqual(frame.loc[0, "Telegram Reason"], "NO_LIFECYCLE_EVENT")
        terminal = [
            call for call in record_decision.call_args_list
            if call.args[1] in {"OPENED", "BLOCKED", "SKIPPED"}
            and call.args[0] == "NFLX"
        ]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0].args[1], "SKIPPED")
        self.assertEqual(terminal[0].args[2], "SETUP_INVALID")

    def test_a_held_symbol_is_reported_as_held_not_as_a_malformed_entry(self):
        """The scanner stamps ACTIVE_TRADE instead of running detect_entry once a
        symbol is open, so a re-scan is not a broken row -- it is a position we
        already have. Both used to log INVALID_ENTRY_TYPE, and that single string
        was 43% of every actionable decision in the ledger. A funnel read through
        it says entries are being silently dropped when nothing is."""

        row = {
            "Symbol": "PLTR",
            "Action Status": "ENTER_PAPER",
            "Setup Valid": True,
            "Candidate Direction": "CALL",
            "Candidate Entry Price": 173,
            "Candidate Stop Price": 171,
            "Candidate Target Price": 177,
            "Candidate RR": 2.0,
            "Entry": "ACTIVE_TRADE",
            "Next Condition": "-",
            "Live Chart Checklist": "-",
            "Realtime Ready": True,
        }
        controls = {"auto_paper_enabled": True, "max_daily": 3}

        with patch(
            "app.runtime.paper_automation_support.auto_paper_session_block_reason",
            return_value=None,
        ), patch(
            "app.runtime.paper_automation_support._record_auto_paper_decision",
        ), patch(
            "app.state.paper_trade_manager.load_paper_trades",
            return_value={},
        ):
            frame = pd.DataFrame([row])
            result = run_auto_paper_entries(frame, controls)

        self.assertEqual(result, [])
        self.assertEqual(
            frame.loc[0, "Execution Reason"],
            "ALREADY_HOLDING_NO_ADDITIONAL_ENTRY",
        )

    def test_a_symbol_with_no_setup_is_still_reported_separately(self):
        """The other half of the split. NO_SETUP means the scanner looked and
        found nothing, which is a different fact from holding the name."""

        row = {
            "Symbol": "PLTR",
            "Action Status": "ENTER_PAPER",
            "Setup Valid": True,
            "Candidate Direction": "CALL",
            "Candidate Entry Price": 173,
            "Candidate Stop Price": 171,
            "Candidate Target Price": 177,
            "Candidate RR": 2.0,
            "Entry": "NO_SETUP",
            "Next Condition": "-",
            "Live Chart Checklist": "-",
            "Realtime Ready": True,
        }
        controls = {"auto_paper_enabled": True, "max_daily": 3}

        with patch(
            "app.runtime.paper_automation_support.auto_paper_session_block_reason",
            return_value=None,
        ), patch(
            "app.runtime.paper_automation_support._record_auto_paper_decision",
        ), patch(
            "app.state.paper_trade_manager.load_paper_trades",
            return_value={},
        ):
            frame = pd.DataFrame([row])
            result = run_auto_paper_entries(frame, controls)

        self.assertEqual(result, [])
        self.assertEqual(
            frame.loc[0, "Execution Reason"], "NO_ENTRY_SETUP_DETECTED"
        )

    def test_opened_candidate_is_audited_when_telegram_fails(self):

        row = {
            "Symbol": "NFLX",
            "Action Status": "ENTER_PAPER",
            "Setup Valid": True,
            "Candidate Direction": "PUT",
            "Candidate Entry Price": 100,
            "Candidate Stop Price": 101,
            "Candidate Target Price": 98,
            "Candidate RR": 2.0,
            "Entry": "VWAP_REJECTION",
            "Next Condition": "-",
            "Live Chart Checklist": "-",
            "Realtime Ready": True,
            "Option Bid": 2.0,
            "Option Ask": 2.2,
        }
        controls = {"auto_paper_enabled": True, "max_daily": 3}

        with patch(
            "app.runtime.paper_automation_support.auto_paper_session_block_reason",
            return_value=None,
        ), patch(
            "app.runtime.paper_automation_support._auto_paper_entry_reason",
            return_value=(True, "ELIGIBLE"),
        ), patch(
            "app.runtime.paper_automation_support._record_auto_paper_decision",
        ) as record_decision, patch(
            "app.state.paper_trade_manager.load_paper_trades",
            return_value={},
        ), patch(
            "app.state.paper_trade_manager.open_paper_trade",
            return_value={"trade_key": "nflx-trade", "opened_at": "2026-07-29 10:00:00"},
        ), patch(
            "app.alerts.telegram_alerts.maybe_send_paper_entry_alert",
            side_effect=RuntimeError("transport unavailable"),
        ):
            result = run_auto_paper_entries(pd.DataFrame([row]), controls)

        self.assertEqual(result, ["NFLX"])
        terminal = [
            call for call in record_decision.call_args_list
            if call.args[1] == "OPENED" and call.args[0] == "NFLX"
        ]
        self.assertEqual(len(terminal), 1)

    def test_auto_paper_entry_accepts_top_rank_without_display_tag(self):

        row = pd.Series({
            "Symbol": "AAPL",
            "Scan ID": "2026-07-28_100000",
            "Action Status": "ENTER_PAPER",
            "Top Candidate": None,
            "Candidate Rank": 2,
            "Setup %": 85,
            "Candidate RR": 2.2,
            "Option Quality Score": 85,
            "Option Spread %": 4,
            "Option Bid": 3.9,
            "Option Ask": 4.1,
            "Realtime Ready": True,
            "Candidate Direction": "CALL",
            "Event Blocked": False,
            "Regime Blocked": False,
        })
        controls = {
            "auto_paper_enabled": True,
            "min_rr": 1.8,
            "min_setup": 70,
            "direction": "Both",
            "max_daily": 3,
        }
        with patch(
            "app.runtime.paper_automation_support._current_et",
            return_value=pd.Timestamp("2026-07-28 10:00:00", tz="America/New_York").to_pydatetime(),
        ), patch(
            "app.runtime.paper_automation_support.evaluate_entry_gate",
            return_value=(True, "ELIGIBLE"),
        ):
            allowed, reason = _auto_paper_entry_reason(row, controls, {})

        self.assertTrue(allowed)
        self.assertEqual(reason, "ELIGIBLE")

    def test_auto_paper_logs_one_off_window_system_skip(self):

        df = pd.DataFrame([
            {"Symbol": "NVDA"},
            {"Symbol": "AAPL"},
        ])
        controls = {"auto_paper_enabled": True}
        with patch(
            "app.runtime.paper_automation_support.auto_paper_session_block_reason",
            return_value="outside auto-entry window",
        ), patch(
            "app.runtime.paper_automation_support.should_record_auto_paper_session_skip",
            return_value=True,
        ), patch(
            "app.runtime.paper_automation_support._record_auto_paper_decision",
        ) as record_decision:
            result = run_auto_paper_entries(df, controls)

        self.assertEqual(result, [])
        record_decision.assert_called_once_with(
            "SYSTEM",
            "SKIPPED",
            "outside auto-entry window",
            controls=controls,
        )

    def test_session_skip_is_not_repeated_within_market_session(self):

        now = pd.Timestamp("2026-07-28 08:00:00", tz="America/New_York").to_pydatetime()
        existing = [{
            "trading_day": "2026-07-28",
            "market_session": "PREMARKET",
            "symbol": "SYSTEM",
            "decision": "SKIPPED",
            "reason": "outside auto-entry window",
        }]
        with patch(
            "app.runtime.paper_automation_support.load_json_file",
            return_value=existing,
        ):
            should_record = should_record_auto_paper_session_skip(
                "outside auto-entry window",
                now=now,
            )

        self.assertFalse(should_record)

    def test_scanner_block_reason_does_not_use_action_status_as_fallback(self):

        reason = _scanner_block_reason(pd.Series({"Action Status": "ENTER_PAPER"}))

        self.assertEqual(reason, "auto paper enabled; no eligible entry candidate")

    def test_lifecycle_does_not_close_on_stop_or_target(self):
        """Market exits belong to the exit engine, not the lifecycle sweep.

        The retired `auto_exit_enabled` / `profit_r` controls are passed on
        purpose: an unrecognised control must not be able to resurrect the second
        exit rule set. Do not remove them from this call. (They used to arrive
        from a stale auto_paper_settings.json; that file is gone, but controls
        now come from env vars, which persist across deploys just as stubbornly.)
        """

        df = pd.DataFrame([
            {
                "Symbol": "NVDA",
                "Price": 90,
                "Live Exit Signal": False,
                "Live Exit Reason": "Hold",
            }
        ])
        trades = {
            "trade": {
                "symbol": "NVDA",
                "status": "OPEN",
                "entry_price": 100,
                "stop_loss": 98,
                "take_profit": 110,
                "holding_profile": "INTRADAY",
            }
        }

        with patch(
            "app.state.paper_trade_manager.load_paper_trades",
            return_value=trades
        ), patch(
            "app.runtime.paper_automation_support._close_paper_trade"
        ) as close_trade:

            result = run_paper_position_lifecycle(
                df,
                {"auto_exit_enabled": True, "profit_r": 0.1},
            )

        close_trade.assert_not_called()
        self.assertEqual(result["eod_closed"], [])

    def test_lifecycle_force_closes_intraday_at_end_of_day(self):

        df = pd.DataFrame([
            {
                "Symbol": "NVDA",
                "Price": 105,
                "Live Exit Signal": False,
                "Live Exit Reason": "Hold",
            }
        ])
        trades = {
            "trade": {
                "symbol": "NVDA",
                "status": "OPEN",
                "entry_price": 100,
                "holding_profile": "INTRADAY",
            }
        }

        with patch(
            "app.state.paper_trade_manager.load_paper_trades",
            return_value=trades
        ), patch(
            "app.runtime.paper_automation_support._current_et"
        ) as current_et, patch(
            "app.runtime.paper_automation_support._close_paper_trade"
        ) as close_trade:

            current_et.return_value = pd.Timestamp("2026-07-28 16:00:00").to_pydatetime()
            result = run_paper_position_lifecycle(
                df,
                {"eod_close_enabled": True},
            )

        self.assertEqual(result["eod_closed"], ["NVDA"])
        close_trade.assert_called_once()
        self.assertEqual(close_trade.call_args.args[0], "NVDA")

    def test_lifecycle_flags_open_position_the_scanner_could_not_manage(self):

        df = pd.DataFrame([
            {
                "Symbol": "NVDA",
                "Price": 105,
                "Live Exit Reason": "No active trade",
                "Blocked By": "NO_5M_DATA",
            }
        ])
        trades = {
            "trade": {
                "symbol": "NVDA",
                "status": "OPEN",
                "entry_price": 100,
                "holding_profile": "INTRADAY",
            }
        }

        with patch(
            "app.state.paper_trade_manager.load_paper_trades",
            return_value=trades
        ):

            result = run_paper_position_lifecycle(df, {})

        self.assertEqual(result["unmanaged"], ["NVDA"])

    def test_blocked_entry_records_actual_gate_not_action_status(self):

        row = pd.Series({
            "Symbol": "AAPL",
            "Action Status": "ENTER_PAPER",
            "Blocked By": "ENTER_PAPER",
            "Action Reason": "Risk and option checks passed",
        })
        with patch(
            "app.runtime.paper_automation_support.append_daily_auto_paper_decision"
        ) as append_daily, patch(
            "app.runtime.paper_automation_support.update_recent_auto_paper_log"
        ), patch(
            "app.runtime.paper_automation_support._current_et"
        ) as current_et:
            current_et.return_value = pd.Timestamp("2026-07-28 10:00:00").to_pydatetime()
            _record_auto_paper_decision(
                "AAPL",
                "BLOCKED",
                "not top candidate",
                row,
                controls={"min_rr": 2.0, "min_setup": 70},
            )

        entry = append_daily.call_args.args[0]
        self.assertEqual(entry["decision"], "BLOCKED")
        self.assertEqual(entry["action_status"], "ENTER_PAPER")
        self.assertEqual(entry["blocked_by"], "not top candidate")
        self.assertEqual(entry["scanner_blocked_by"], "ENTER_PAPER")
        self.assertEqual(entry["action_reason"], "Risk and option checks passed")
        self.assertEqual(entry["scan_id"], "2026-07-28_100000")


class ProfileBudgetTests(unittest.TestCase):
    """An overnight carry must not spend tomorrow's intraday capacity.

    INTRADAY positions are flattened at 15:55 and give their slot back; MULTIDAY
    positions set force_eod_exit=False and hold for days. Under one shared
    MAX_ACTIVE_PAPER_TRADES the slow profile crowds out the fast one, and the
    operator sees only MAX_ACTIVE_PAPER_TRADES_REACHED.
    """

    def _candidate(self, symbol="NVDA", profile="INTRADAY"):
        return pd.Series({
            "Symbol": symbol,
            "Action Status": "ENTER_PAPER",
            "Realtime Ready": True,
            "Top Candidate": "BULLISH_TOP_1",
            "Candidate Direction": "CALL",
            "Candidate Entry Price": 100.0,
            "Candidate Stop Price": 98.0,
            "Candidate Target Price": 106.0,
            "Candidate RR": 3.0,
            "Setup %": 90.0,
            "Option Quality Score": 95.0,
            "Option Quote Freshness": "LIVE_QUOTE",
            "Option Spread %": 2.0,
            "Option Bid": 2.60,
            "Option Ask": 2.70,
            "Option Mid Price": 2.65,
            "Option Delta": 0.55,
            "Affordable": True,
            "Holding Profile": profile,
        })

    def _open_trade(self, symbol, profile):
        return {
            "status": "OPEN", "symbol": symbol, "direction": "CALL",
            "holding_profile": profile, "notes": "Auto paper entry",
            "opened_at": "2026-08-03 10:00:00",
        }

    def _decide(self, row, paper_trades, env):
        base = {
            "AUTO_PAPER_ENABLED": "true", "MAX_ACTIVE_PAPER_TRADES": "9",
            "MAX_ACTIVE_PER_DIRECTION": "9", "MAX_DAILY_ENTRIES": "9",
            "MAX_TRADES_PER_SYMBOL_PER_DAY": "9",
            "AUTO_PAPER_SYMBOL_COOLDOWN_MINUTES": "0",
        }
        base.update(env)

        with patch.dict(os.environ, base, clear=False), patch(
            "app.runtime.paper_automation_support._current_et"
        ) as current_et:
            current_et.return_value = pd.Timestamp("2026-08-03 11:00:00").to_pydatetime()

            return _auto_paper_entry_reason(
                row, load_auto_paper_controls(), paper_trades
            )

    def test_multiday_carries_do_not_block_an_intraday_entry(self):
        """The defect: two overnight carries filling a shared book."""

        held = {
            "AMD": self._open_trade("AMD", "MULTIDAY"),
            "SMCI": self._open_trade("SMCI", "MULTIDAY"),
        }

        allowed, reason = self._decide(
            self._candidate(profile="INTRADAY"), held,
            {"MAX_ACTIVE_MULTIDAY_TRADES": "2", "MAX_ACTIVE_INTRADAY_TRADES": "2"},
        )

        self.assertTrue(allowed, reason)

    def test_the_multiday_budget_stops_a_third_carry(self):

        held = {
            "AMD": self._open_trade("AMD", "MULTIDAY"),
            "SMCI": self._open_trade("SMCI", "MULTIDAY"),
        }

        allowed, reason = self._decide(
            self._candidate(profile="MULTIDAY"), held,
            {"MAX_ACTIVE_MULTIDAY_TRADES": "2"},
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "MAX_ACTIVE_MULTIDAY_TRADES_REACHED")

    def test_the_intraday_budget_is_counted_separately(self):

        held = {
            "AMD": self._open_trade("AMD", "INTRADAY"),
            "SMCI": self._open_trade("SMCI", "MULTIDAY"),
        }

        allowed, reason = self._decide(
            self._candidate(profile="INTRADAY"), held,
            {"MAX_ACTIVE_INTRADAY_TRADES": "1"},
        )

        self.assertFalse(allowed)
        self.assertEqual(reason, "MAX_ACTIVE_INTRADAY_TRADES_REACHED")

    def test_the_daily_budget_splits_by_profile(self):

        closed = {
            "AMD": {
                "status": "CLOSED", "symbol": "AMD", "direction": "CALL",
                "holding_profile": "MULTIDAY", "notes": "Auto paper entry",
                "opened_at": "2026-08-03 10:00:00",
            },
        }

        blocked, reason = self._decide(
            self._candidate(profile="MULTIDAY"), closed,
            {"MAX_DAILY_MULTIDAY_ENTRIES": "1"},
        )
        self.assertFalse(blocked)
        self.assertEqual(reason, "DAILY_MULTIDAY_LIMIT_REACHED")

        # The intraday budget is untouched by the multiday entry.
        allowed, reason = self._decide(
            self._candidate(profile="INTRADAY"), closed,
            {"MAX_DAILY_MULTIDAY_ENTRIES": "1", "MAX_DAILY_INTRADAY_ENTRIES": "1"},
        )
        self.assertTrue(allowed, reason)

    def test_unset_budgets_fall_back_to_the_shared_caps(self):
        """Inert until someone sets it: behaviour must not change on deploy."""

        held = {
            "AMD": self._open_trade("AMD", "MULTIDAY"),
            "SMCI": self._open_trade("SMCI", "MULTIDAY"),
        }

        for name in ("MAX_ACTIVE_MULTIDAY_TRADES", "MAX_ACTIVE_INTRADAY_TRADES",
                     "MAX_DAILY_MULTIDAY_ENTRIES", "MAX_DAILY_INTRADAY_ENTRIES"):
            os.environ.pop(name, None)

        allowed, reason = self._decide(
            self._candidate(profile="MULTIDAY"), held,
            {"MAX_ACTIVE_PAPER_TRADES": "3"},
        )

        self.assertTrue(allowed, reason)

    def test_an_unlabelled_position_counts_as_intraday(self):
        """The profile that gives its slot back is the safe unknown."""

        from app.runtime.paper_automation_support import _active_profile_count

        untagged = [{"status": "OPEN", "holding_profile": None}]

        self.assertEqual(_active_profile_count(untagged, "INTRADAY"), 1)
        self.assertEqual(_active_profile_count(untagged, "MULTIDAY"), 0)


class DecisionLedgerContentTests(unittest.TestCase):
    """What the ledger has to carry to be usable for tuning.

    Two defects, both measured against 2026-08-03's 869 rows: `min_setup_used`
    recorded the auto-paper control (62) rather than the scanner floor that
    actually rejected the candidate (70, or 83/85 after regime escalation), and
    not one row carried the spread, delta or premium behind its
    `stop_spread_multiple`.
    """

    def _record(self, row, controls=None):
        with patch(
            "app.runtime.paper_automation_support.append_daily_auto_paper_decision"
        ) as append_daily, patch(
            "app.runtime.paper_automation_support.update_recent_auto_paper_log"
        ), patch(
            "app.runtime.paper_automation_support._persist_auto_paper_decision"
        ), patch(
            "app.runtime.paper_automation_support._current_et"
        ) as current_et:
            current_et.return_value = pd.Timestamp("2026-08-03 12:28:33").to_pydatetime()
            _record_auto_paper_decision(
                "SMCI", "SKIPPED", "Stop is inside the spread", row,
                controls=controls or {"min_rr": 1.8, "min_setup": 62.0},
            )

        return append_daily.call_args.args[0]

    def test_the_floor_recorded_is_the_one_that_applied(self):

        entry = self._record(pd.Series({
            "Symbol": "SMCI",
            "Action Status": "REVIEW_TV_CHART",
            "Setup %": 70.0,
            "ENTRY_GATE_MIN_SETUP": 83.0,
            "ENTRY_GATE_MIN_RR": 2.0,
        }))

        # The regime-escalated scanner floor, not the 62 the control carries --
        # which is what made "setup 70, blocked, floor 62" rows unreadable.
        self.assertEqual(entry["min_setup_used"], 83.0)
        self.assertEqual(entry["min_rr_used"], 2.0)

        # The control is still recorded, so a change to it that had no effect is
        # still visible as one that had no effect.
        self.assertEqual(entry["auto_paper_min_setup"], 62.0)
        self.assertEqual(entry["auto_paper_min_rr"], 1.8)

    def test_a_row_without_gate_diagnostics_falls_back_to_the_control(self):
        """SYSTEM rows and manual entries never run the scanner gate."""

        entry = self._record(pd.Series({"Symbol": "SMCI", "Action Status": "WAIT"}))

        self.assertEqual(entry["min_setup_used"], 62.0)
        self.assertEqual(entry["min_rr_used"], 1.8)

    def test_the_stop_viability_inputs_are_recorded(self):
        """A multiple with no inputs cannot be recalibrated."""

        entry = self._record(pd.Series({
            "Symbol": "SMCI",
            "Action Status": "AVOID",
            "Blocked By": "STOP_INSIDE_OPTION_SPREAD",
            "STOP_SPREAD_MULTIPLE": 0.56,
            "STOP_MOVE_PCT_OF_PREMIUM": 2.67,
            "STOP_ROUND_TRIP_SPREAD_PCT": 4.76,
            "STOP_REQUIRED_SPREAD_MULTIPLE": 1.0,
            "Option Delta": 0.524,
            "Option Mid Price": 2.645,
            "Option Spread %": 4.76,
            "Option Quality Score": 85,
            "Candidate Entry Price": 28.62,
            "Candidate Stop Price": 28.40,
        }))

        self.assertEqual(entry["stop_move_pct_of_premium"], 2.67)
        self.assertEqual(entry["stop_round_trip_spread_pct"], 4.76)
        self.assertEqual(entry["stop_required_spread_multiple"], 1.0)
        self.assertEqual(entry["option_delta"], 0.524)
        self.assertEqual(entry["option_mid_price"], 2.645)
        self.assertEqual(entry["option_quality_score"], 85)
        self.assertEqual(entry["candidate_entry_price"], 28.62)
        self.assertEqual(entry["candidate_stop_price"], 28.40)

    def test_absent_option_fields_are_omitted_not_blanked(self):
        """A candidate that died before contract selection has no option data.

        Writing None for each would be indistinguishable from a contract that
        priced at nothing, which is the same conflation the rule emitter had.
        """

        entry = self._record(pd.Series({
            "Symbol": "SMCI",
            "Action Status": "WAIT",
            "Option Delta": None,
            "Option Spread %": float("nan"),
            "Option Ticker": "",
        }))

        for field in ("option_delta", "option_spread_pct", "option_ticker"):
            self.assertNotIn(field, entry)


if __name__ == "__main__":

    unittest.main()