"""Freezing a baseline must not depend on local disk.

`freeze_baseline` called `reconstruct_trades` only when the day's local
`scanner_snapshots/` folder existed. `_snapshot_frames`, inside that call,
already reads `scanner_snapshot` from Postgres and only falls back to parquet --
so the guard tested a source the loader does not prefer.

On Render that folder is ephemeral and never survives a deploy, so the call was
skipped every time; `freeze_baseline` then fell through to
`paper_trade_events.csv`, also local, and returned None. Regression looked
broken while capture was working perfectly: on 2026-08-13 the database held
2,499 snapshot rows across 109 scans and the newest frozen baseline was
2026-07-31, two weeks stale.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.regression import historical_scanner as hs


class BaselineSourceTests(unittest.TestCase):

    def test_it_reconstructs_when_no_local_folder_exists(self):
        """The regression: database has the day, disk has nothing."""

        reconstructed = pd.DataFrame([{
            "trade_key": "NVDA|O:NVDA260821C00225000|2026-08-13 10",
            "symbol": "NVDA", "setup": "EMA_PULLBACK",
            "holding_profile": "INTRADAY", "outcome": "WIN", "r_multiple": 1.2,
        }])

        with patch.object(hs, "_baseline_folder",
                          return_value=Path("nonexistent-baseline-dir")), \
             patch.object(hs, "_snapshot_folder",
                          return_value=Path("nonexistent-snapshot-dir")), \
             patch.object(hs, "reconstruct_trades",
                          return_value=reconstructed) as reconstruct, \
             patch("app.db.scanner_snapshot_repository.RegressionBaselineRepository") as repo, \
             patch.object(pd.DataFrame, "to_csv"), \
             patch.object(Path, "mkdir"), \
             patch.object(Path, "write_text"), \
             patch.object(Path, "exists", return_value=False):

            repo.return_value.load.return_value = None

            result = hs.freeze_baseline("2026-08-13")

        reconstruct.assert_called_once()
        self.assertIsNotNone(
            result,
            "a day present only in the database must still freeze"
        )

    def test_a_day_that_never_traded_returns_none(self):
        """2026-08-11 and 08-12 had zero entries; None is correct, not a bug."""

        with patch.object(hs, "_baseline_folder",
                          return_value=Path("nonexistent-baseline-dir")), \
             patch.object(hs, "_snapshot_folder",
                          return_value=Path("nonexistent-snapshot-dir")), \
             patch.object(hs, "reconstruct_trades",
                          return_value=pd.DataFrame()), \
             patch("app.db.scanner_snapshot_repository.RegressionBaselineRepository") as repo, \
             patch.object(Path, "exists", return_value=False):

            repo.return_value.load.return_value = None

            self.assertIsNone(hs.freeze_baseline("2026-08-11"))

    def test_an_existing_durable_baseline_is_reused(self):
        """Freezing twice must not silently rewrite history."""

        with patch("app.db.scanner_snapshot_repository.RegressionBaselineRepository") as repo, \
             patch.object(hs, "_baseline_folder",
                          return_value=Path("nonexistent-baseline-dir")), \
             patch.object(hs, "reconstruct_trades") as reconstruct, \
             patch.object(pd.DataFrame, "to_csv"), \
             patch.object(Path, "mkdir"):

            repo.return_value.load.return_value = {
                "payload": [{"trade_key": "X", "r_multiple": 1.0}]
            }

            result = hs.freeze_baseline("2026-08-13")

        self.assertIsNotNone(result)
        reconstruct.assert_not_called()


if __name__ == "__main__":
    unittest.main()
