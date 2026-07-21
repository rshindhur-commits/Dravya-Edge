from __future__ import annotations

import pandas as pd

from app.storage.daily_paths import daily_path


def build_delay_attribution(trading_day):
    """Attribute candidate waiting time to observed lifecycle blocker groups."""
    path = daily_path(trading_day, "signal_state_transitions.csv")
    try:
        rows = pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=["Rule", "Scans", "Minutes"])
    if rows.empty:
        return pd.DataFrame(columns=["Rule", "Scans", "Minutes"])
    reason = rows.get("reason", pd.Series("UNKNOWN", index=rows.index)).fillna("UNKNOWN").astype(str).str.upper()
    group = reason.map(lambda value: "Realtime" if any(token in value for token in ["REALTIME", "QUOTE", "STALE", "DELAY"]) else "Option" if any(token in value for token in ["OPTION", "SPREAD", "LIQUID"]) else "RR" if "RR" in value else "Affordability" if "AFFORD" in value else "Review" if "REVIEW" in value else "Other")
    minutes = pd.to_numeric(rows.get("duration_minutes", pd.Series(0, index=rows.index)), errors="coerce").fillna(0)
    output = pd.DataFrame({"Rule": group, "Minutes": minutes})
    return output.groupby("Rule", as_index=False).agg(Scans=("Rule", "size"), Minutes=("Minutes", "sum")).sort_values("Minutes", ascending=False)
