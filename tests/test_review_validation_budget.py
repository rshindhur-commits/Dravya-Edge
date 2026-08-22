"""Spread-tolerated validation entries hold their own daily budget.

They exist to answer one question: over 30 days, does a 3% spread ceiling produce
better trades than no ceiling? That answer needs the baseline book -- the trades
taken under the ceiling -- to be exactly the size it has always been. Both kinds
of entry write notes beginning "Auto paper", so under one shared MAX_DAILY_ENTRIES
a validation entry would take a slot from the control group and the comparison
would be measuring its own interference.

`include_in_strategy_stats=False` already keeps them out of the statistics. This
keeps them out of the cap for the same reason.
"""

import unittest
from unittest.mock import patch

from app.runtime.paper_automation_support import (
    _auto_paper_trade_count_today,
    max_daily_review_validation_entries,
)


def _book(*entry_sources, day="2026-08-21"):

    return {
        f"t{index}": {
            "symbol": f"SYM{index}",
            "opened_at": f"{day} 10:0{index}:00",
            "entry_source": source,
            "notes": (
                "Auto paper review validation entry"
                if source == "AUTO_PAPER_REVIEW_VALIDATION"
                else "Auto paper entry"
            ),
        }
        for index, source in enumerate(entry_sources)
    }


class ReviewValidationBudgetTests(unittest.TestCase):

    BOOK = _book(
        "AUTO_PAPER",
        "AUTO_PAPER",
        "AUTO_PAPER_REVIEW_VALIDATION",
        "AUTO_PAPER_REVIEW_VALIDATION",
        "AUTO_PAPER_REVIEW_VALIDATION",
    )

    def _count(self, **kwargs):

        import datetime

        with patch(
            "app.runtime.paper_automation_support._current_et",
            return_value=datetime.datetime(2026, 8, 21, 15, 0)
        ):
            return _auto_paper_trade_count_today(self.BOOK, **kwargs)

    def test_the_normal_cap_does_not_see_validation_entries(self):
        """Three validation entries must not consume three of the five slots."""

        self.assertEqual(self._count(review_validation=False), 2)

    def test_the_validation_budget_sees_only_its_own(self):

        self.assertEqual(self._count(review_validation=True), 3)

    def test_the_unfiltered_count_is_unchanged(self):
        """Callers that ask for the whole book still get the whole book."""

        self.assertEqual(self._count(), 5)

    def test_the_budget_has_a_default_so_it_is_never_unbounded(self):

        self.assertGreater(max_daily_review_validation_entries(), 0)

    def test_the_entry_path_uses_the_split_count(self):

        import inspect

        from app.runtime import paper_automation_support

        source = inspect.getsource(
            paper_automation_support._auto_paper_entry_reason
        )

        self.assertIn("review_validation=False", source)
        self.assertIn("DAILY_REVIEW_VALIDATION_LIMIT_REACHED", source)

    def test_the_blanket_cap_sweep_is_gone(self):
        """Un-evaluated rows must not be labelled as daily-cap blocks.

        The loop stamped DAILY_AUTO_PAPER_LIMIT_REACHED on every remaining
        candidate and broke, so 133 blocks across 13 sessions carried a reason
        nothing had tested. With two budgets a full book no longer means nothing
        else can open, so the break would also skip candidates that should run.
        """

        import inspect

        from app.runtime import paper_automation

        source = inspect.getsource(paper_automation.run_auto_paper_entries)

        self.assertNotIn(
            'record_terminal(remaining_row, "BLOCKED"', source
        )


if __name__ == "__main__":

    unittest.main()
