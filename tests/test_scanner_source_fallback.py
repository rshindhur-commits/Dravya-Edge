"""The Trading page must never quietly serve the workbook committed to the repo.

On 2026-08-17 it did, all session. `_load_scanner_output` looked for
`data/live/scanner_output_latest.csv`, then the `.xlsx`, then fell back to
`scanner_output.xlsx` in the repo root. The first two are gitignored and the
worker that writes them runs on Render, while Streamlit Cloud serves the page
from a different container -- so on the host that matters only the third ever
existed. It was a committed snapshot from 2026-08-08 carrying
`NO_RECOMMENDATION`, `STALE_MARKET_DATA` and no candidates on every row.

The page refreshed on its five-minute timer and could not change, because the
refresh re-read a file baked into the image. Nothing errored, so nothing said so.

Postgres is the only surface the worker and the viewer share, which is why the
archive is now consulted before the committed file, and why the committed file
warns when it is used at all.
"""

import pandas as pd
import pytest


# --------------------------------------------------------------------------
# Which source gets chosen
# --------------------------------------------------------------------------

def test_the_committed_workbook_is_not_a_silent_fallback(monkeypatch):
    """`_scanner_file_for_display` returns None rather than `SCANNER_FILE`.

    This is the whole defect in one assertion: the committed workbook exists on
    every host, so returning it here meant no host ever reached the archive.
    """

    import app.dashboard as dashboard

    monkeypatch.setattr(
        type(dashboard.LIVE_SCANNER_CSV_FILE), "exists", lambda self: False
    )

    assert dashboard._scanner_file_for_display() is None


def test_a_live_file_still_wins_when_one_exists(monkeypatch):
    """Local development is unaffected -- a machine running its own scanner
    reads its own output and never touches the database for this."""

    import app.dashboard as dashboard

    monkeypatch.setattr(
        type(dashboard.LIVE_SCANNER_CSV_FILE),
        "exists",
        lambda self: self == dashboard.LIVE_SCANNER_CSV_FILE,
    )

    assert dashboard._scanner_file_for_display() == dashboard.LIVE_SCANNER_CSV_FILE


# --------------------------------------------------------------------------
# The archive rebuilds the same frame the file would have
# --------------------------------------------------------------------------

def _minimal_scanner_row(symbol="NVDA"):
    return {
        "Symbol": symbol,
        "Price": 100.0,
        "Final Signal": "BULLISH",
        "Risk Reward": 2.1,
        "Action Status": "WAIT",
        "Next Condition": "-",
        "15m Score": 12.0,
        "Alignment Score": 9.0,
        "Entry": "BREAKOUT",
        "Setup Valid": True,
        "Relative Volume": 1.4,
    }


def test_the_archive_frame_gets_the_same_derived_columns_as_a_file(monkeypatch):
    """Both sources go through `_decorate_scanner_frame`, so the Trading page
    cannot be handed a frame missing the columns it reads. Two copies of this
    logic would agree the day they were written and not after."""

    import app.dashboard as dashboard

    df = dashboard._decorate_scanner_frame(
        pd.DataFrame([_minimal_scanner_row()]), source="archive"
    )

    for column in (
        "Signal",
        "RR",
        "Action",
        "Next Trigger",
        "Setup %",
        "Setup Grade",
        "Option Moneyness",
        "Trend Phase",
        "Volume Score",
    ):
        assert column in df.columns, f"{column} missing from the archive frame"


def test_the_frame_records_which_source_drew_it():
    """So a caption can say "archive" or name the file, rather than the operator
    having to infer it from whether the numbers look plausible."""

    import app.dashboard as dashboard

    df = dashboard._decorate_scanner_frame(
        pd.DataFrame([_minimal_scanner_row()]), source="archive (2026-08-17)"
    )

    assert df.attrs["scanner_source"] == "archive (2026-08-17)"


def test_an_empty_frame_survives_decoration():
    """An empty scan is a real state -- a halted market, a failed provider call
    -- and must not raise on the way to an empty table."""

    import app.dashboard as dashboard

    assert dashboard._decorate_scanner_frame(pd.DataFrame()).empty
    assert dashboard._decorate_scanner_frame(None).empty


# --------------------------------------------------------------------------
# The repository read
# --------------------------------------------------------------------------

def test_load_latest_scan_returns_none_when_the_read_fails(monkeypatch):
    """None, not an empty list. The caller keeps its file path for local use and
    has to be able to tell "the archive is empty" from "the archive is
    unreachable" -- the same distinction `load_day(strict=True)` draws."""

    from app.db import scanner_snapshot_repository as repo_module

    def _explode():
        raise RuntimeError("no database")

    monkeypatch.setattr(repo_module, "get_engine", _explode)

    assert repo_module.ScannerSnapshotRepository().load_latest_scan() is None


def test_load_latest_scan_decodes_a_text_payload(monkeypatch):
    """`payload` is JSONB and comes back decoded, but tolerating a text column
    means a future migration cannot silently empty the Trading page."""

    import json

    from app.db import scanner_snapshot_repository as repo_module

    row = {
        "payload": json.dumps(_minimal_scanner_row()),
        "scan_id": "2026-08-17_095250",
        "scan_timestamp": "2026-08-17T09:52:50",
        "created_at": "2026-08-17T09:53:08",
    }

    class _Result:
        def mappings(self):
            return self

        def all(self):
            return [row]

    class _Connection:
        def execute(self, *_args, **_kwargs):
            return _Result()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(repo_module, "get_engine", lambda: type(
        "_Engine", (), {"connect": lambda self: _Connection()}
    )())

    latest = repo_module.ScannerSnapshotRepository().load_latest_scan()

    assert latest is not None
    assert latest["scan_id"] == "2026-08-17_095250"
    assert latest["rows"][0]["Symbol"] == "NVDA"
