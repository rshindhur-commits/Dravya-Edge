"""Headless caller for auto-paper trading.

Runs the exact same decision path as `app/dashboard.py`'s Trading page
auto-paper trigger, without Streamlit and without requiring an operator to
have that page open. Built per
docs/specs/S2.1-headless-extraction-plan.md.

This is a SECOND CALLER, not an extraction: `app/runtime/paper_automation.py`,
`app/state/paper_trade_manager.py`, and `app/gates/entry_gate.py` are
unchanged and already have zero Streamlit dependencies (verified in S2.1 --
they were already clean). This module owns exactly four things:

  1. Assembling the same input frame the dashboard assembles -- by calling
     the SAME `app.dashboard` functions, not a reimplementation of them (see
     the note below).
  2. Resolving the same auto-paper controls, via `auto_paper_controls.py`.
  3. Replicating the dashboard's exit-before-entry ordering and its
     `st.rerun()`-after-exit skip semantics (S2.1 §4.1) -- deliberately, for
     parity. This is a known, verified quirk of the current dashboard
     behavior being reproduced on purpose, not a design choice made here.
  4. A market-hours-aware polling loop.

Deliberate deviation from the S2.1 plan's literal proposed shape: the four
enrichment functions (`_load_scanner_output`, `_sync_suggested_trades`,
`_add_paper_trade_opened`, `_add_real_trade_readiness`,
`_enrich_with_suggestion_lifecycle`) are NOT copied into this module.
`app/dashboard.py` has no module-level Streamlit calls -- its Streamlit
entrypoint is guarded by `if __name__ == "__main__":` -- so it is safe to
import as a plain module and call its functions directly (verified: importing
it runs no UI code). Calling the real functions is a stronger parity guarantee
than moving a copy would be: there is no second copy to drift. The two
`st.error`/`st.warning` calls inside them are verified no-ops when there is no
active Streamlit `ScriptRunContext` -- they print a "missing ScriptRunContext"
notice to stderr and continue; they do not raise.
"""
from __future__ import annotations

import os
import time as time_module

from app.runtime.auto_paper_controls import resolve_auto_paper_controls
from app.utils.runtime_logging import debug_print


DEFAULT_POLL_SECONDS = 60  # matches the dashboard's "1 min" auto-refresh default


def is_market_hours():

    from app.dashboard import _is_market_hours

    return _is_market_hours()


def build_automation_frame():

    """Assemble the same input frame the dashboard assembles before calling
    `run_auto_paper_exits` / `run_auto_paper_entries`. See module docstring
    for why this calls into `app.dashboard` rather than duplicating it.
    """

    from app.dashboard import (
        _add_paper_trade_opened,
        _add_real_trade_readiness,
        _enrich_with_suggestion_lifecycle,
        _load_scanner_output,
        _sync_suggested_trades,
    )

    df = _load_scanner_output()

    if df.empty:

        return df

    _sync_suggested_trades(df)
    df = _add_paper_trade_opened(df)
    df = _add_real_trade_readiness(df)
    df = _enrich_with_suggestion_lifecycle(df)

    return df


def run_cycle(controls=None):

    """One pass: exits, then entries -- replicating `dashboard.py`'s trigger
    (around line 9399) exactly, including the `st.rerun()`-after-exit skip
    (S2.1 §4.1).

    Returns {"opened": [...], "closed": [...], "skipped_entries": bool}.
    """

    from app.runtime.paper_automation import (
        run_auto_paper_entries,
        run_auto_paper_exits,
    )

    controls = controls if controls is not None else resolve_auto_paper_controls()
    df = build_automation_frame()

    if df.empty:

        return {"opened": [], "closed": [], "skipped_entries": False}

    closed = run_auto_paper_exits(df, controls)

    if closed:

        # Dashboard parity, not a design choice: `st.rerun()` after a close
        # raises and ends the pass before any entry is evaluated (S2.1 §4.1).
        # Entries resume on the next cycle against a reloaded frame and
        # reloaded paper state -- exactly as the dashboard behaves today. Do
        # not "fix" this here; changing it is a decision-affecting change and
        # belongs in a later session behind a flag with the counterfactual
        # logged (I6).
        debug_print(
            f"[HEADLESS PAPER] closed {closed}; skipping entries this cycle "
            f"(dashboard st.rerun() parity)"
        )

        return {"opened": [], "closed": closed, "skipped_entries": True}

    opened = run_auto_paper_entries(df, controls)

    return {"opened": opened, "closed": closed, "skipped_entries": False}


def main(poll_seconds=None):

    """Market-hours-aware polling loop. Runs with the dashboard closed.

    The precise entry-window (09:45-15:30 ET) and EOD-close (15:55 ET) gating
    already live inside `paper_automation_support.py` and are enforced on
    every call regardless of caller -- this loop's own market-hours check is
    a coarse skip to avoid needless polling overnight/on weekends, not a
    second copy of that gating logic.
    """

    poll_seconds = poll_seconds or int(
        os.getenv("HEADLESS_PAPER_POLL_SECONDS", DEFAULT_POLL_SECONDS)
    )

    debug_print(f"[HEADLESS PAPER] starting, poll_seconds={poll_seconds}")

    while True:

        try:

            if is_market_hours():

                controls = resolve_auto_paper_controls()

                if controls.get("auto_paper_enabled"):

                    result = run_cycle(controls)

                    if result["opened"] or result["closed"]:

                        debug_print(f"[HEADLESS PAPER] cycle result: {result}")

        except Exception as exc:

            print(f"[HEADLESS PAPER ERROR] {exc}")

        time_module.sleep(poll_seconds)


if __name__ == "__main__":

    main()
