"""The daily record has to survive the day it describes.

Two defects observed live on 2026-07-31 sit behind these tests.

The scan's own audit trail was being shed under load: `persist_scan_artifacts`
and `persist_regression_snapshot` were submitted cancelable, and
`cancel_old_jobs` kills any QUEUED cancelable job from an earlier scan the
moment the next one starts. During the opening range a 120s cadence against a
~150s scan left roughly five seconds to drain, so 13 of 50 runs archived nothing
and 9 never left STARTED, `record_scanner_run_finish` being inside the job that
got cancelled.

And `rule_performance` had never received a row in the table's lifetime, because
`write_daily_learning_summary` passed `entry_exit_v2_shadow.csv` where the
waterfall was expected. That file has no `stage` column and no `blocking` flag,
so the counts it produced were always empty.
"""

import pandas as pd

from app.analytics.learning_engine import (
    _blocking_rule_counts,
    build_daily_learning_summary,
)


def _waterfall_frame():
    """One scan: AAPL stopped at RR, MSFT at Setup, NVDA at Momentum."""

    return pd.DataFrame([
        {"symbol": "AAPL", "stage": "Momentum", "rule_name": "Directional Signal",
         "passed": True, "blocking": False},
        {"symbol": "AAPL", "stage": "Entry", "rule_name": "Setup",
         "passed": True, "blocking": False},
        {"symbol": "AAPL", "stage": "Risk", "rule_name": "RR",
         "passed": False, "blocking": True},
        # Cascade artifact: AAPL died at RR, so no contract was ever priced.
        {"symbol": "AAPL", "stage": "Option", "rule_name": "Option Quality",
         "passed": False, "blocking": False},
        {"symbol": "MSFT", "stage": "Entry", "rule_name": "Setup",
         "passed": False, "blocking": True},
        {"symbol": "MSFT", "stage": "Option", "rule_name": "Option Quality",
         "passed": False, "blocking": False},
        {"symbol": "NVDA", "stage": "Momentum", "rule_name": "Directional Signal",
         "passed": False, "blocking": True},
    ])


def test_blocking_rules_counts_only_the_rule_that_stopped_the_candidate():
    counts = _blocking_rule_counts(_waterfall_frame())

    assert counts == {"Setup": 1, "RR": 1, "Directional Signal": 1}


def test_cascade_failures_are_not_counted_as_rejections():
    """Option Quality fails on two rows and stopped neither candidate.

    This is the misreading the flag exists to prevent: on 2026-07-31 Option
    Quality recorded 1,039 failures and genuinely blocked six.
    """

    counts = _blocking_rule_counts(_waterfall_frame())

    assert "Option Quality" not in counts


def test_blocking_rules_survives_a_frame_without_the_columns():
    """entry_exit_v2_shadow.csv shaped input -- the original defect."""

    shadow = pd.DataFrame([
        {"shadow_id": "1", "symbol": "AAPL", "v1_entry_type": "EMA_PULLBACK"},
    ])

    assert _blocking_rule_counts(shadow) == {}
    assert _blocking_rule_counts(pd.DataFrame()) == {}
    assert _blocking_rule_counts(None) == {}


def test_daily_summary_exposes_blocking_rules_for_rule_performance():
    empty = pd.DataFrame()

    summary = build_daily_learning_summary(
        "2026-07-31", empty, empty, empty, _waterfall_frame()
    )

    assert summary["blocking_rules"] == {"Setup": 1, "RR": 1, "Directional Signal": 1}
    # The stage tally is kept, but it counts every evaluated row and so cannot
    # stand in for the blocking rule.
    assert summary["blocking_stages"]["Option"] == 2


def test_daily_summary_with_shadow_frame_yields_no_rule_rows():
    """Reproduces the empty table: wrong frame in, nothing to persist."""

    empty = pd.DataFrame()
    shadow = pd.DataFrame([{"shadow_id": "1", "symbol": "AAPL"}])

    summary = build_daily_learning_summary(
        "2026-07-31", empty, empty, empty, shadow
    )

    assert summary["blocking_rules"] == {}


def test_scan_audit_jobs_are_not_cancelable():
    """Both jobs must outlive the scan that queued them.

    Asserted against the source because constructing them needs a live
    scheduler, a scan's worth of records and a database.
    """

    import inspect

    from app import main

    source = inspect.getsource(main._finalize_scan_persistence) \
        if hasattr(main, "_finalize_scan_persistence") else inspect.getsource(main)

    for job in ("persist_scan_artifacts_db", "persist_regression_snapshot"):
        start = source.index(f'name="{job}"')
        window = source[start:start + 600]
        assert "cancelable=False" in window, (
            f"{job} is cancelable; cancel_old_jobs will drop the scan's own "
            f"record when the next scan starts"
        )
