from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.regression import (
    freeze_baseline,
    run_historical_regression,
    write_scan_snapshot,
)
from app.db.artifact_persistence import persist_regression_snapshot


def _snapshot(price, action):
    return pd.DataFrame([{
        "Symbol": "NVDA",
        "Candidate Direction": "CALL",
        "Entry": "EMA_PULLBACK",
        "Action Status": action,
        "Candidate Entry Price": 100.0,
        "Candidate Stop Price": 98.0,
        "Candidate Target Price": 104.0,
        "Candidate RR": 2.0,
        "Option Quality Score": 80,
        "Price": price,
    }])


def test_historical_regression_compares_reconstructed_trades_against_frozen_baseline(tmp_path):
    trading_day = "2026-07-15"
    daily_dir = Path(tmp_path) / trading_day
    data_dir = Path(tmp_path) / "data"
    regression_dir = data_dir / "regression"

    with patch(
        "app.regression.historical_scanner.get_daily_dir",
        return_value=daily_dir,
    ), patch(
        "app.regression.historical_scanner.DATA_DIR",
        data_dir,
    ), patch(
        "app.db.scanner_snapshot_repository.ScannerSnapshotRepository.load_day",
        return_value=[],
    ), patch(
        "app.db.scanner_snapshot_repository.RegressionBaselineRepository.load",
        return_value=None,
    ), patch(
        "app.db.scanner_snapshot_repository.RegressionBaselineRepository.freeze",
    ), patch(
        "app.db.scanner_snapshot_repository.RegressionRunRepository.record",
        return_value=None,
    ):
        first = write_scan_snapshot(_snapshot(100, "ENTER_PAPER"), trading_day, "093000", "2026-07-15 09:30:00")
        duplicate = write_scan_snapshot(_snapshot(100, "ENTER_PAPER"), trading_day, "093000", "2026-07-15 09:30:00")
        write_scan_snapshot(_snapshot(104, "WAIT"), trading_day, "093500", "2026-07-15 09:35:00")
        write_scan_snapshot(_snapshot(106, "WAIT"), trading_day, "094000", "2026-07-15 09:40:00")

        baseline_path = freeze_baseline(trading_day)

        def improved_evaluator(row, _context):
            return {
                "action": row.get("Action Status"),
                "holding_profile": "INTRADAY",
                "setup": row.get("Entry"),
                "entry": row.get("Candidate Entry Price"),
                "stop": row.get("Candidate Stop Price"),
                "target": 106.0,
                "direction": row.get("Candidate Direction"),
            }

        summary = run_historical_regression(trading_day, evaluator=improved_evaluator)

    assert first["created"] is True
    assert duplicate["created"] is False
    assert baseline_path.exists()
    assert summary["baseline"]["total_r"] == 2.0
    assert summary["current"]["total_r"] == 3.0
    assert summary["net_gain_r"] == 1.0
    assert len(summary["comparison"]["changed"]) == 1
    assert summary["verdict"] == "✅ Strategy Improved"
    assert (regression_dir / trading_day / "regression_summary.json").exists()
    assert (regression_dir / trading_day / "regression_trades.csv").exists()
    assert (regression_dir / trading_day / "regression_report.html").exists()


def test_incomplete_scan_never_calls_regression_recorder():
    with patch(
        "app.db.artifact_persistence.ScannerSnapshotRepository.batch_insert"
    ) as persist:
        result = persist_regression_snapshot(
            records=[{"Symbol": "NVDA"}],
            trading_day="2026-07-15",
            scan_id="2026-07-15_093000",
            health_payload={"scan_completed_successfully": False},
        )

    assert result == {"enabled": False, "reason": "SCAN_INCOMPLETE"}
    persist.assert_not_called()