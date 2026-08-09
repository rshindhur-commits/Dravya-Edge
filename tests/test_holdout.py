"""The out-of-sample discipline has to hold when someone wants it not to.

Three findings in this project died on a hand-applied holdout, and roughly ten
arms have now been run against the same 21 sessions. These tests exist because
the discipline is only worth anything if it refuses results, so each one tries
to get a bad result accepted.
"""

import json

import pytest

from app.research import holdout


@pytest.fixture(autouse=True)
def isolated_research_dir(tmp_path, monkeypatch):
    """Never touch the real split -- a test that rewrites it destroys the point."""

    monkeypatch.setattr(holdout, "RESEARCH_DIR", tmp_path)
    monkeypatch.setattr(holdout, "SPLIT_FILE", tmp_path / "splits.json")
    monkeypatch.setattr(holdout, "LEDGER_FILE", tmp_path / "comparisons.jsonl")


SESSIONS = [f"2026-07-{day:02d}" for day in range(6, 27)]


def test_the_holdout_is_the_most_recent_sessions():
    """Not a random sample: the question is about days never seen."""

    split = holdout.fix_split(SESSIONS)

    assert split["train"][-1] < split["holdout"][0]
    assert len(split["holdout"]) == 7
    assert set(split["train"]) | set(split["holdout"]) == set(SESSIONS)


def test_the_split_does_not_move_once_fixed():
    """A split chosen after seeing a result is not a split."""

    first = holdout.fix_split(SESSIONS)
    second = holdout.fix_split(list(reversed(SESSIONS)), holdout_fraction=0.9)

    assert first["train"] == second["train"]
    assert first["holdout"] == second["holdout"]


def test_new_sessions_extend_the_holdout_and_leave_train_alone():

    first = holdout.fix_split(SESSIONS)
    extended = holdout.fix_split(SESSIONS + ["2026-07-27", "2026-07-28"])

    assert extended["train"] == first["train"]
    assert "2026-07-27" in extended["holdout"]
    assert "2026-07-28" in extended["holdout"]


def test_partition_refuses_to_guess_a_split():

    with pytest.raises(RuntimeError, match="No split has been fixed"):

        holdout.partition([{"day": "2026-07-06"}])


def test_partition_splits_by_session_not_by_trade():
    """Trades in one session share a market; splitting them leaks the answer."""

    holdout.fix_split(SESSIONS)

    rows = [{"day": day, "n": i} for i, day in enumerate(SESSIONS)]
    train, held = holdout.partition(rows)

    assert {r["day"] for r in train}.isdisjoint({r["day"] for r in held})
    assert len(train) + len(held) == len(rows)


def test_comparisons_are_counted():
    """At ten arms the best looks ~1.8 SE better than average by chance."""

    assert holdout.comparison_count() == 0

    holdout.record_comparison("cross_sectional_rs")
    count = holdout.record_comparison("regime_conditioning", {"bars": 8})

    assert count == 2

    logged = [
        json.loads(line)
        for line in holdout.LEDGER_FILE.read_text().splitlines()
        if line.strip()
    ]

    assert [entry["name"] for entry in logged] == [
        "cross_sectional_rs",
        "regime_conditioning",
    ]


def test_an_overfit_arm_is_refused():
    """Strong on train, nothing on holdout -- the shape that killed three of these."""

    verdict, why = holdout.judge(
        train_mean=0.400, holdout_mean=0.010, holdout_ci=(-0.20, 0.22)
    )

    assert verdict == "TRAIN_ONLY"
    assert "overfit" in why


def test_a_holdout_win_that_includes_zero_is_refused():
    """Clearing the bar on a noisy interval is not distinguishable from nothing.

    This is exactly the +14.43% failure: a mean that cleared its bar on an
    interval spanning zero, carried by five trades of 331.
    """

    verdict, why = holdout.judge(
        train_mean=0.300, holdout_mean=0.200, holdout_ci=(-0.05, 0.45)
    )

    assert verdict == "TRAIN_ONLY"
    assert "includes zero" in why


def test_a_weak_train_result_is_rejected_outright():

    verdict, _ = holdout.judge(
        train_mean=0.050, holdout_mean=0.900, holdout_ci=(0.5, 1.3)
    )

    assert verdict == "REJECTED"


def test_confirmation_needs_all_three_conditions():

    verdict, why = holdout.judge(
        train_mean=0.310, holdout_mean=0.260, holdout_ci=(0.08, 0.44)
    )

    assert verdict == "CONFIRMED"
    assert "both clear" in why


def test_the_bar_is_break_even_not_zero():
    """An arm that merely beats zero still loses money after the spread."""

    assert holdout.BREAK_EVEN_CAPTURED_PCT == pytest.approx(0.155)

    verdict, _ = holdout.judge(
        train_mean=0.100, holdout_mean=0.090, holdout_ci=(0.02, 0.16)
    )

    assert verdict == "REJECTED"
