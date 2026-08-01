"""HSR was unrunnable because the database layer depended on import order.

`app/db/connection.py` only read `os.getenv("DATABASE_URL")` and relied on some
other module -- `app.config.settings`, `app.utils.polygon_client` -- having
called `load_dotenv()` as an import side effect. Entry points that reach the
database directly do not import those. `tools/regression_runner.py` imports only
`app.regression`, so the URL was unset, every read failed, and the swallowed
exception surfaced to the operator as "No scanner snapshots in Neon or local
fallback" for a day holding 702 archived rows.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_isolated(tmp_path, script):
    """Import in a fresh process from a directory holding its own .env."""
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql+psycopg2://probe/db\n", encoding="utf-8"
    )
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "DRAVYA_DATA_DIR", "DRAVYA_STATE_DIR"}
    }
    environment["PYTHONPATH"] = str(REPO_ROOT)
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_database_url_resolves_without_importing_the_settings_module(tmp_path):
    """The import path `tools/regression_runner.py` actually takes."""
    result = _run_isolated(tmp_path, """
        import os, sys
        import app.db.connection
        assert "app.config.settings" not in sys.modules, "test no longer isolates the fix"
        print(os.getenv("DATABASE_URL"))
    """)

    assert result.returncode == 0, result.stderr
    assert "postgresql+psycopg2://probe/db" in result.stdout


def test_regression_entrypoint_alone_reaches_a_configured_engine(tmp_path):
    """`app.regression` is the whole import surface the HSR CLI has."""
    result = _run_isolated(tmp_path, """
        import app.regression
        from app.db.connection import get_engine

        engine = get_engine()
        print("host:", engine.url.host)
    """)

    assert result.returncode == 0, result.stderr
    assert "host: probe" in result.stdout


def test_a_missing_database_url_still_raises_rather_than_reporting_success(tmp_path):
    (tmp_path / ".env").write_text("SOMETHING_ELSE=1\n", encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"DATABASE_URL", "DRAVYA_DATA_DIR", "DRAVYA_STATE_DIR"}
    }
    environment["PYTHONPATH"] = str(REPO_ROOT)

    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            from app.db.connection import get_engine
            try:
                get_engine()
            except RuntimeError as exc:
                print("raised:", exc)
        """)],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=120,
    )

    assert "raised: DATABASE_URL is not configured" in result.stdout


def test_archive_status_reports_the_failure_instead_of_an_empty_archive(monkeypatch):
    """A swallowed read error read as "no data", which is how a configuration
    fault was mistaken for a day that was never archived."""
    from app.db import scanner_snapshot_repository as repository

    def _explode():
        raise RuntimeError("DATABASE_URL is not configured")

    monkeypatch.setattr(repository, "get_engine", _explode)

    status = repository.ScannerSnapshotRepository().archive_status("2026-07-30")

    assert status["available"] is False
    assert status["rows"] == 0
    assert "DATABASE_URL is not configured" in status["error"]


def test_load_day_returns_empty_but_announces_the_failure(monkeypatch, capsys):
    from app.db import scanner_snapshot_repository as repository

    def _explode():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(repository, "get_engine", _explode)

    assert repository.ScannerSnapshotRepository().load_day("2026-07-30") == []
    assert "connection refused" in capsys.readouterr().out
