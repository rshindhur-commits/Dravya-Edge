from unittest.mock import patch

from app.state.paper_trade_manager import (
    _queue_paper_trade_upsert,
    get_open_paper_trade,
    update_paper_trade,
)


def test_scanner_management_reads_and_updates_paper_trade_state():
    state = {
        "trade-1": {
            "trade_key": "trade-1",
            "symbol": "SMCI",
            "status": "OPEN",
            "entry_price": 28.44,
            "stop_loss": 28.33,
            "highest_price": 28.44,
            "rr_progress": 0.0,
        }
    }
    with patch(
        "app.state.paper_trade_manager.load_paper_trades",
        return_value=state,
    ), patch(
        "app.state.paper_trade_manager.save_paper_trades",
    ) as save_state, patch(
        "app.state.paper_trade_manager._queue_paper_trade_upsert",
    ) as queue_upsert:
        active = get_open_paper_trade("SMCI")
        updated = update_paper_trade(
            "SMCI",
            highest_price=28.70,
            rr_progress=1.2,
            updated_stop=28.44,
            current_price=28.62,
            lowest_price=28.40,
            bars_in_trade=3,
            partial_profit_taken=True,
            execution_metrics={
                "mfe_r": 1.4,
                "mae_r": 0.2,
                "trend_health_status": "HEALTHY",
                "exit_confidence_score": 22,
            },
        )

    assert active is state["trade-1"]
    assert updated is state["trade-1"]
    assert updated["highest_price"] == 28.70
    assert updated["lowest_price"] == 28.40
    assert updated["rr_progress"] == 1.2
    assert updated["current_price"] == 28.62
    assert updated["stop_loss"] == 28.44
    assert updated["bars_in_trade"] == 3
    assert updated["partial_profit_taken"] is True
    assert updated["mfe_r"] == 1.4
    assert updated["mae_r"] == 0.2
    assert updated["last_trend_health_status"] == "HEALTHY"
    assert updated["last_exit_confidence_score"] == 22
    save_state.assert_called_once_with(state)
    queue_upsert.assert_called_once_with(updated)


def test_legacy_scanner_trade_is_migrated_into_paper_state():
    paper_state = {}
    legacy_state = {
        "SMCI": {
            "symbol": "SMCI",
            "status": "OPEN",
            "entry_price": 28.44,
            "stop_loss": 28.33,
            "take_profit": 28.89,
            "entry_type": "EMA_PULLBACK",
            "direction": "CALL",
            "option_ticker": "O:SMCI260821C00028000",
            "option_entry_mid": 3.25,
            "option_expiration": "2026-08-21",
            "option_expiration_bucket": "PREFERRED_14_30",
            "option_quality_score": 100,
            "opened_at": "2026-07-28T16:00:22-04:00",
        }
    }
    with patch(
        "app.state.paper_trade_manager.load_paper_trades",
        return_value=paper_state,
    ), patch(
        "app.state.paper_trade_manager.load_json_file",
        return_value=legacy_state,
    ), patch(
        "app.state.paper_trade_manager.save_paper_trades",
    ) as save_paper, patch(
        "app.state.paper_trade_manager.save_json_file",
    ) as save_legacy, patch(
        "app.state.paper_trade_manager._append_paper_trade_event",
    ) as append_event:
        migrated = get_open_paper_trade("SMCI")

    assert migrated["symbol"] == "SMCI"
    assert migrated["entry_source"] == "LEGACY_SCANNER_STATE_MIGRATION"
    assert migrated["trade_mode"] == "PAPER"
    assert migrated["trade_key"] in paper_state
    assert "SMCI" not in legacy_state
    save_paper.assert_called_once_with(paper_state)
    save_legacy.assert_called_once()
    assert str(save_legacy.call_args.args[0]).endswith("app\\state\\trade_state.json")
    assert save_legacy.call_args.args[1] == legacy_state
    append_event.assert_called_once_with(migrated, "OPEN")


def test_paper_trade_upsert_is_queued_without_blocking_trade_lifecycle():
    trade = {"trade_key": "trade-1", "scan_id": "scan-1"}
    with patch(
        "app.runtime.get_runtime_scheduler"
    ) as scheduler_factory:
        _queue_paper_trade_upsert(trade)

    scheduler_factory.return_value.submit_normal.assert_called_once()
    job = scheduler_factory.return_value.submit_normal.call_args.args[0]
    assert job.name == "upsert_paper_trade_db"
    assert job.args == (trade,)
    assert job.scan_id == "scan-1"