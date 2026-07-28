"""Single source of truth for auto-paper control defaults.

Built for S2.2 (docs/specs/S2.1-headless-extraction-plan.md §3.1). Both the
dashboard (as the fallback seeding `st.session_state` widgets) and the
headless caller (as its only source, since it has no session_state) resolve
through `resolve_auto_paper_controls()`. Do not duplicate these literals
anywhere else -- a second copy of these defaults is exactly the silent
divergence risk S2.1 flagged as most likely to break parity.
"""
from __future__ import annotations

import os
from pathlib import Path

from app.runtime.paper_automation_support import DEFAULT_AUTO_PAPER_MIN_RR
from app.utils.json_store import load_json_file


ROOT_DIR = Path(__file__).resolve().parents[2]
AUTO_PAPER_SETTINGS_FILE = ROOT_DIR / "app" / "state" / "auto_paper_settings.json"


def _env_bool(name, default=False):

    value = os.getenv(name)

    if value is None:

        return default

    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _boolish(value):

    if isinstance(value, bool):

        return value

    return str(value).strip().lower() in {"true", "1", "yes"}


def load_auto_paper_settings():

    return load_json_file(str(AUTO_PAPER_SETTINGS_FILE), {})


def resolve_auto_paper_controls(saved_settings=None):

    """Resolve the 9 auto-paper controls: session_state (caller's concern) ->
    this file's tier-2/3 resolution (session_state -> JSON -> env/literal
    default).

    Returns the same shape `run_auto_paper_entries`/`run_auto_paper_exits`
    expect -- not the JSON's own key names.
    """

    saved = (
        saved_settings
        if saved_settings is not None
        else load_auto_paper_settings()
    )

    return {
        "auto_paper_enabled": bool(
            saved.get(
                "auto_paper_enabled",
                _env_bool("AUTO_PAPER_ENABLED", True)
            )
        ),
        "max_daily": int(saved.get("auto_paper_max_daily", 3)),
        "min_setup": float(saved.get("auto_paper_min_setup", 70)),
        "min_rr": float(
            saved.get("auto_paper_min_rr", DEFAULT_AUTO_PAPER_MIN_RR)
        ),
        "direction": saved.get("auto_paper_direction", "Both"),
        "auto_exit_enabled": bool(
            saved.get("auto_paper_exit_enabled", True)
        ),
        "eod_close_enabled": _boolish(
            saved.get("auto_paper_eod_close_enabled", False)
        ),
        "restore_multiday_positions": _boolish(
            saved.get("restore_multiday_positions", True)
        ),
        "profit_r": float(saved.get("auto_paper_profit_r", 1.0)),
    }
