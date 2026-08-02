"""What the scanner actually saw on an archived day.

The previous version read `scanner_output_close.csv` and `offline_replay.csv`
under `data/daily/`, generating the second from the first. Both are written by
the process that runs the scan, so on this host the tab reported "Not generated"
every session -- and the generation itself re-runs the scanner, which is not work
a dashboard should be doing on a Streamlit container while a page waits on it.

`scanner_snapshot` holds the archive: one row per symbol per scan, with the
decision the scanner reached, kept for every archived day rather than only the
one on local disk. That is the substance of what Replay was for -- what did we
see, and what did we conclude.

Re-running current code against an archived day is a genuinely different question
and still belongs in `tools/`, where the scanner is available.
"""

from __future__ import annotations


UNAVAILABLE = "Unavailable — the archive could not be read. This is not the same as a day that was never archived."

# The decision payload carries well over a hundred columns. These are the ones
# that say what the scanner concluded; the rest stay behind the expander.
DECISION_COLUMNS = [
    "Symbol", "Action Status", "Action Reason", "Candidate Direction",
    "Setup Valid", "Setup %", "Market Regime", "ENTRY_READINESS",
]


def render(df=None, refresh_state=None):
    import pandas as pd
    import streamlit as st

    from app.analytics.day_postmortem import previous_trading_days
    from app.db.scanner_snapshot_repository import ScannerSnapshotRepository

    st.subheader("Replay")
    st.caption(
        "The archived scanner record for a day, from Postgres. Re-running current "
        "code against an archive is a separate job and lives in tools/."
    )

    days = [day.isoformat() for day in previous_trading_days(15)]
    chosen = st.selectbox("Trading day", days, index=0, key="replay_day")

    rows = ScannerSnapshotRepository().load_day(chosen, strict=True)

    if rows is None:
        st.warning(UNAVAILABLE)
        return

    if not rows:
        st.caption(
            f"Nothing was archived for {chosen}. If the scanner ran that day, "
            "check the Postmortem tab for coverage gaps."
        )
        return

    scans = sorted({row.get("scan_id") for row in rows if row.get("scan_id")})
    symbols = sorted({row.get("symbol") for row in rows if row.get("symbol")})

    columns = st.columns(3)
    columns[0].metric("Archived rows", len(rows))
    columns[1].metric("Scans", len(scans))
    columns[2].metric("Symbols", len(symbols))

    if not scans:
        st.caption("Archived rows carry no scan id.")
        return

    scan = st.selectbox("Scan", scans, index=len(scans) - 1, key="replay_scan")
    frame = pd.DataFrame([
        {"Symbol": row.get("symbol"), **(row.get("decision_payload") or {})}
        for row in rows
        if row.get("scan_id") == scan
    ])

    if frame.empty:
        st.caption("No rows for this scan.")
        return

    present = [column for column in DECISION_COLUMNS if column in frame.columns]

    st.dataframe(frame[present] if present else frame,
                 width="stretch", hide_index=True)

    with st.expander("Every archived field for this scan", expanded=False):
        st.caption(
            f"{len(frame.columns)} columns. This is the decision payload as it "
            "was written at the time, not a reconstruction."
        )
        st.dataframe(frame, width="stretch", hide_index=True)
