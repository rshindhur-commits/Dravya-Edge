from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd


def render(state, df, refresh_state):

    from app.dashboard import (
        _render_command_center,
        _render_current_opportunities,
        _render_metadata_card,
        _render_missed_opportunities,
        _render_why_no_trade,
        _scan_metadata,
        _status_label,
    )

    metadata = _scan_metadata(df, refresh_state=refresh_state)
    _render_metadata_card(
        "Trading Session",
        [
            ("Scanner Status", "LIVE OK"),
            ("Current Scan ID", metadata["scan_id"]),
            ("Scanner Started", metadata["scanner_started"]),
            ("Scanner Finished", metadata["scanner_finished"]),
            ("Last Refreshed", metadata["last_refreshed"]),
            ("Scan Age", metadata["scan_age"]),
            ("Symbols Scanned", metadata["symbols"]),
            ("Status", _status_label(metadata["status"])),
        ]
    )
    _render_command_center(state, df, refresh_state)
    _render_current_opportunities(state)
    _render_why_no_trade(state)
    _render_missed_opportunities(state)


def render_from_state(state, refresh_state):

    from app.dashboard import (
        _render_command_center,
        _render_current_opportunities,
        _render_metadata_card,
        _render_missed_opportunities,
        _render_why_no_trade,
        _status_label,
    )

    metadata = {
        "scan_id": state.get("scan_id") or state.get("data_version") or "N/A",
        "scanner_started": "cached",
        "scanner_finished": state.get("generated_at") or "cached",
        "last_refreshed": datetime.now(ZoneInfo("America/New_York")).strftime("%m/%d/%Y %H:%M:%S ET"),
        "scan_age": "cached",
        "symbols": (state.get("summary") or {}).get("scanned", 0),
        "status": state.get("scanner") or "LIVE",
    }
    _render_metadata_card(
        "Trading Session",
        [
            ("Scanner Status", _status_label(metadata["status"])),
            ("Current Scan ID", metadata["scan_id"]),
            ("Scanner Started", metadata["scanner_started"]),
            ("Scanner Finished", metadata["scanner_finished"]),
            ("Last Refreshed", metadata["last_refreshed"]),
            ("Scan Age", metadata["scan_age"]),
            ("Symbols Scanned", metadata["symbols"]),
            ("Status", _status_label(metadata["status"])),
        ]
    )
    _render_command_center(state, pd.DataFrame(), refresh_state)
    _render_current_opportunities(state)
    _render_why_no_trade(state)
    _render_missed_opportunities(state)