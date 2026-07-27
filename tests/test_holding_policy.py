from datetime import datetime
from unittest.mock import patch

from app.alerts.telegram_alerts import (
    _exit_reason_label,
    build_multiday_position_continue_message,
    build_paper_trade_update_message,
    build_trade_cancelled_alert_message,
    build_trade_exit_alert_message,
)
from app.runtime.paper_automation_support import _auto_exit_reason
from app.gates.entry_gate import has_active_symbol_trade
from app.state.holding_policy import HoldingProfile, derive_holding_profile
from app.state.paper_trade_manager import (
    override_paper_trade_holding_profile,
    pause_paper_trade,
    resume_paper_trade,
)
from app.state.trade_session_lifecycle import (
    archive_prior_session_candidates,
    initialize_session_lifecycle,
    restore_carried_intraday_positions,
    restore_open_multiday_positions,
)


def test_derives_multiday_profile_from_candidate_quality_and_expiration():
    profile = derive_holding_profile({
        "Expiration Bucket": "PREFERRED_14_30",
        "Setup %": 85,
        "Candidate RR": 2.0,
        "Option Quality Score": 80,
    })

    assert profile is HoldingProfile.MULTIDAY


def test_defaults_to_intraday_profile():
    assert derive_holding_profile({}) is HoldingProfile.INTRADAY


def test_eod_exit_respects_holding_policy():
    controls = {"auto_exit_enabled": False, "eod_close_enabled": True, "profit_r": 10}
    with patch(
        "app.runtime.paper_automation_support._current_et",
        return_value=datetime(2026, 7, 24, 16, 0),
    ):
        intraday_reason = _auto_exit_reason(
            {"holding_profile": "INTRADAY", "entry_price": 100, "stop_loss": 98},
            100,
            None,
            controls,
        )
        multiday_reason = _auto_exit_reason(
            {"holding_profile": "MULTIDAY", "entry_price": 100, "stop_loss": 98},
            100,
            None,
            controls,
        )

    assert intraday_reason == "Auto paper exit: end-of-day close"
    assert multiday_reason is None


def test_eod_uses_the_trade_current_holding_profile():
    controls = {"auto_exit_enabled": False, "eod_close_enabled": True}
    with patch(
        "app.runtime.paper_automation_support._current_et",
        return_value=datetime(2026, 7, 24, 16, 0),
    ):
        reason = _auto_exit_reason(
            {"holding_profile": "INTRADAY", "entry_price": 100, "stop_loss": 98},
            100,
            None,
            controls,
        )

    assert reason == "Auto paper exit: end-of-day close"


def test_paused_trade_blocks_reentry_and_can_resume():
    state = {
        "trade": {
            "trade_key": "trade",
            "symbol": "NFLX",
            "status": "OPEN",
            "trade_state": "OPEN",
            "holding_profile": "MULTIDAY",
        }
    }
    with patch(
        "app.state.paper_trade_manager.load_paper_trades", return_value=state
    ), patch(
        "app.state.paper_trade_manager.save_paper_trades"
    ), patch(
        "app.state.paper_trade_manager._append_paper_trade_event"
    ):
        paused = pause_paper_trade("NFLX", "Polygon outage")
        assert paused["status"] == "PAUSED"
        assert paused["trade_state"] == "PAUSED"
        assert has_active_symbol_trade(state, "NFLX") is True

        resumed = resume_paper_trade("NFLX")

    assert resumed["status"] == "OPEN"
    assert resumed["trade_state"] == "OPEN"


def test_holding_profile_override_requires_an_explicit_allowed_source():
    state = {
        "trade": {
            "trade_key": "trade",
            "symbol": "NFLX",
            "status": "OPEN",
            "holding_profile": "MULTIDAY",
        }
    }
    with patch(
        "app.state.paper_trade_manager.load_paper_trades", return_value=state
    ), patch(
        "app.state.paper_trade_manager.save_paper_trades"
    ), patch(
        "app.state.paper_trade_manager._append_paper_trade_event"
    ):
        updated = override_paper_trade_holding_profile(
            "NFLX",
            "INTRADAY",
            source="MANUAL_OVERRIDE",
        )

    assert updated["holding_profile"] == "INTRADAY"
    assert updated["holding_profile_override_source"] == "MANUAL_OVERRIDE"

    try:
        override_paper_trade_holding_profile("NFLX", "MULTIDAY", source="SCANNER")
    except ValueError as error:
        assert "MANUAL_OVERRIDE" in str(error)
    else:
        raise AssertionError("Scanner profile changes must be rejected")


def test_restore_marks_open_multiday_trade_for_continuation():
    state = {
        "multiday": {
            "trade_key": "multiday",
            "status": "OPEN",
            "holding_profile": "MULTIDAY",
            "opened_at": "2026-07-23 10:00:00",
        },
        "intraday": {
            "trade_key": "intraday",
            "status": "OPEN",
            "holding_profile": "INTRADAY",
            "opened_at": "2026-07-23 10:00:00",
        },
    }
    with patch(
        "app.state.trade_session_lifecycle.load_paper_trades", return_value=state
    ), patch(
        "app.state.trade_session_lifecycle.save_paper_trades"
    ) as save_state:
        restored = restore_open_multiday_positions("2026-07-24")

    assert [trade["trade_key"] for trade in restored] == ["multiday"]
    assert state["multiday"]["overnight_transition"] is True
    assert state["multiday"]["days_held"] == 2
    save_state.assert_called_once_with(state)


def test_carried_intraday_trade_stays_intraday_with_operational_warning():
    state = {
        "intraday": {
            "trade_key": "intraday",
            "symbol": "NFLX",
            "status": "OPEN",
            "holding_profile": "INTRADAY",
            "opened_at": "2026-07-23 14:30:00",
        }
    }
    with patch(
        "app.state.trade_session_lifecycle.load_paper_trades", return_value=state
    ), patch(
        "app.state.trade_session_lifecycle.save_paper_trades"
    ) as save_state:
        restored = restore_carried_intraday_positions("2026-07-24")

    assert [trade["trade_key"] for trade in restored] == ["intraday"]
    assert state["intraday"]["holding_profile"] == "INTRADAY"
    assert state["intraday"]["overnight_transition"] is False
    assert state["intraday"]["overnight_intraday_carry"] is True
    assert "Auto Close Intraday Trades was disabled" in state["intraday"]["overnight_carry_warning"]
    save_state.assert_called_once_with(state)


def test_restore_toggle_skips_multiday_restore_but_still_archives_candidates():
    with patch(
        "app.state.trade_session_lifecycle.restore_open_multiday_positions"
    ) as restore, patch(
        "app.state.trade_session_lifecycle.archive_prior_session_candidates",
        return_value=["stale-candidate"],
    ):
        result = initialize_session_lifecycle(
            "2026-07-24",
            restore_multiday_positions=False,
        )

    assert result == {
        "restored_positions": [],
        "carried_intraday_positions": [],
        "archived_candidates": ["stale-candidate"],
    }
    restore.assert_not_called()


def test_archives_prior_intraday_candidates_but_keeps_promoted_multiday():
    state = {
        "intraday": {
            "status": "NEW_CALL",
            "holding_profile": "INTRADAY",
            "last_seen_at": "2026-07-23T15:50:00-04:00",
        },
        "multiday": {
            "status": "PROMOTED_TO_PAPER",
            "holding_profile": "MULTIDAY",
            "last_seen_at": "2026-07-23T15:50:00-04:00",
        },
    }
    with patch(
        "app.state.trade_session_lifecycle.load_suggestions", return_value=state
    ), patch(
        "app.state.trade_session_lifecycle.save_suggestions"
    ) as save_state:
        archived = archive_prior_session_candidates("2026-07-24")

    assert archived == ["intraday"]
    assert state["intraday"]["status"] == "ARCHIVED"
    assert state["multiday"]["status"] == "PROMOTED_TO_PAPER"
    save_state.assert_called_once_with(state)


def test_position_continues_message_identifies_an_existing_multiday_trade():
    message = build_multiday_position_continue_message(
        {
            "symbol": "SPCX",
            "direction": "CALL",
            "entry_price": 100,
            "stop_loss": 98,
                "opened_at": "2026-07-23 10:15:00",
            "days_held": 2,
        },
        102,
        "HEALTHY",
        event_timestamp="2026-07-24 09:30:00",
    )

    assert "POSITION CONTINUES" in message
    assert "Opened: 23 Jul" in message
    assert "Current: 1.0R" in message
    assert "Holding: Day 2" in message
    assert "Action: Continue Holding" in message
    assert "Jul 24, 2026 · 09:30 ET" in message


def test_trade_update_and_partial_profit_follow_the_subscriber_contract():
    trade = {
        "symbol": "NFLX",
        "direction": "CALL",
        "entry_price": 100,
        "stop_loss": 98,
        "option_strike": 700,
        "option_expiration": "2026-08-21",
        "option_mid": 2.35,
    }
    update = build_paper_trade_update_message(
        trade,
        0.6,
        "HEALTHY",
        updated_stop=99,
        event_type="STOP_MOVED",
        event_timestamp="2026-07-24 10:24:00",
    )
    partial = build_paper_trade_update_message(
        trade,
        1.3,
        "HEALTHY",
        updated_stop=100,
        event_type="PARTIAL",
        event_timestamp="2026-07-24 10:24:00",
    )

    assert "TRADE UPDATE" in update
    assert "Current: 0.6R" in update
    assert "Risk: Reduced" in update
    assert "Stop: $98.00 → $99.00" in update
    assert "Contract: 700C" in update
    assert "Expiry: 2026-08-21" in update
    assert "Contract Cost: $235.00" in update
    assert "Jul 24, 2026 · 10:24 ET" in update
    assert "PARTIAL PROFIT" in partial
    assert "Runner: Still Open" in partial
    assert "Stop: Moved to Breakeven" in partial
    assert "Execution" not in partial


def test_close_and_cancelled_messages_include_subscriber_closure_details():
    close = build_trade_exit_alert_message(
        "NFLX",
        {
            "direction": "CALL",
            "entry_price": 100,
            "option_entry_mid": 2.0,
            "option_contracts": 1,
            "option_strike": 700,
            "option_expiration": "2026-08-21",
            "opened_at": "2026-07-24 09:30:00",
        },
        "STOP_HIT",
        current_price=98,
        option_current_mid=1.48,
        r_multiple=-1.0,
        trend_capture_pct=86,
        event_timestamp="2026-07-24 12:42:00",
    )
    cancelled = build_trade_cancelled_alert_message(
        {"symbol": "AMZN", "direction": "PUT"},
        "Entry conditions never confirmed.",
        "2026-07-24 11:00:00",
    )

    assert "TRADE CLOSED" in close
    assert "🟥 Stop Loss" in close
    assert "Risk Managed: According to Plan" in close
    assert "Holding Time: 3h 12m" in close
    assert "Contract: 700C" in close
    assert "Expiry: 2026-08-21" in close
    assert "Contract Cost: $200.00" in close
    assert "Jul 24, 2026 · 12:42 ET" in close
    assert "TRADE CANCELLED" in cancelled
    assert "No action taken." in cancelled
    assert "Jul 24, 2026 · 11:00 ET" in cancelled


def test_closed_trade_exit_categories_are_consistent():
    assert _exit_reason_label("TARGET_HIT") == "🟩 Target Hit"
    assert _exit_reason_label("STOP_HIT") == "🟥 Stop Loss"
    assert _exit_reason_label("EMA20_BREAK") == "🟨 EMA Exit"
    assert _exit_reason_label("VWAP_LOSS") == "🟦 VWAP Exit"
    assert _exit_reason_label("END_OF_DAY") == "🟪 Time Exit"
    assert _exit_reason_label("NEAR_CLOSE") == "🟪 Time Exit"
    assert _exit_reason_label("Near-close exit without sufficient profit") == "🟪 Time Exit"
    assert _exit_reason_label("Auto paper exit: end-of-day close") == "🟪 Time Exit"
    assert _exit_reason_label("FAILED_BREAKOUT") == "⚠️ Failed Breakout"
    assert _exit_reason_label("Manual paper exit") == "📈 Manual Exit"