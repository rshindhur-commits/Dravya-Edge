"""The mid-session question: nothing has fired, is that the market or is it us?

Nothing in the dashboard answered it. Every diagnostic was retrospective, so the
only way to tell a quiet tape from a rule eating everything was to wait for the
day to end -- by which point the answer changes nothing.

The narrative is what these tests mostly hold, because it is the part that turns
data into a decision, and each branch of it points somewhere different: at the
engine, at the rules, or at the entry layer.
"""

import unittest
from unittest.mock import patch

from app.analytics import live_funnel
from app.db.decision_funnel_repository import DecisionFunnelRepository


def _stage(name, order, seen, blocked, blocks=None):
    return {
        "stage": name,
        "stage_order": order,
        "symbols_seen": seen,
        "symbols_blocked": blocked,
        "blocks": blocks if blocks is not None else blocked,
    }


class FunnelShapeTests(unittest.TestCase):

    def test_an_unreadable_stage_table_is_none_not_an_empty_funnel(self):

        self.assertIsNone(live_funnel.funnel_rows(None))

    def test_passed_is_seen_minus_blocked_not_a_count_of_passing_rules(self):
        """A stage holds several rules per symbol. Counting rows where `passed`
        is true would score a symbol that cleared four rules and failed the
        fifth as a pass."""

        rows = live_funnel.funnel_rows([_stage("Entry", 2, 26, 10, blocks=40)])

        self.assertEqual(rows[0]["passed"], 16)
        self.assertEqual(rows[0]["blocked"], 10)
        self.assertEqual(rows[0]["blocks"], 40)
        self.assertEqual(rows[0]["pass_rate"], 61.5)

    def test_stages_are_ordered_by_stage_order_not_by_arrival(self):

        rows = live_funnel.funnel_rows([
            _stage("Option", 4, 10, 2),
            _stage("Momentum", 1, 26, 4),
        ])

        self.assertEqual([row["stage"] for row in rows], ["Momentum", "Option"])

    def test_a_stage_nothing_reached_has_no_pass_rate_rather_than_zero(self):
        """0% implies it was evaluated and everything failed. Nothing reaching a
        stage is a different fact and belongs further up the funnel."""

        rows = live_funnel.funnel_rows([_stage("Option", 4, 0, 0)])

        self.assertIsNone(rows[0]["pass_rate"])


class NarrativeTests(unittest.TestCase):
    """Each branch sends the operator somewhere different, so each is pinned."""

    def _report(self, **overrides):
        report = {
            "minutes": 60,
            "freshness": {"age_minutes": 3, "run_id": "r"},
            "stages": [],
            "blocking_rules": [],
            "near_misses": [],
            "entry_decisions": [],
        }
        report.update(overrides)

        return report

    def test_an_unreadable_database_is_never_reported_as_a_quiet_market(self):

        message = live_funnel.narrative(self._report(freshness=None))

        self.assertIn("could not be read", message)
        self.assertIn("not the same as a quiet market", message)

    def test_a_stale_scan_is_called_an_engine_question(self):
        """The most important branch: with nothing scanning, every rule number on
        the page is describing a scan that did not happen."""

        message = live_funnel.narrative(
            self._report(freshness={"age_minutes": 45, "run_id": "r"}))

        self.assertIn("engine question", message)

    def test_a_fresh_scan_with_no_evaluations_says_so(self):

        message = live_funnel.narrative(self._report(stages=[]))

        self.assertIn("no candidate was evaluated", message.lower())

    def test_a_stage_that_stopped_everything_is_named(self):

        message = live_funnel.narrative(self._report(stages=live_funnel.funnel_rows([
            _stage("Momentum", 1, 26, 26),
            _stage("Entry", 2, 26, 26),
        ])))

        self.assertIn("Momentum", message)
        self.assertIn("26", message)

    def test_candidates_clearing_every_stage_points_at_the_entry_layer(self):
        """The scanner is not the problem here, and saying so stops an operator
        loosening thresholds that were never the constraint."""

        message = live_funnel.narrative(self._report(stages=live_funnel.funnel_rows([
            _stage("Momentum", 1, 26, 10),
            _stage("Entry", 2, 16, 4),
        ])))

        self.assertIn("entry decisions", message.lower())


class AssemblyTests(unittest.TestCase):

    def test_one_failing_section_does_not_take_the_page_down(self):

        class Repository:
            def stage_funnel(self, _minutes):
                return []

            def blocking_rules(self, _minutes):
                raise RuntimeError("connection reset")

            def near_misses(self, _minutes):
                return []

            def entry_decisions(self, _minutes):
                return []

            def freshness(self):
                return {}

        report = live_funnel.build_live_funnel(60, repository=Repository())

        self.assertIsNone(report["blocking_rules"])
        self.assertEqual(report["stages"], [])


class RepositoryTests(unittest.TestCase):

    def test_a_failed_read_returns_none_from_every_method(self):

        with patch.object(DecisionFunnelRepository, "_fetch_optional",
                          return_value=None):

            repository = DecisionFunnelRepository()

            self.assertIsNone(repository.stage_funnel(60))
            self.assertIsNone(repository.blocking_rules(60))
            self.assertIsNone(repository.near_misses(60))
            self.assertIsNone(repository.entry_decisions(60))
            self.assertIsNone(repository.evaluated_symbols(60))
            self.assertIsNone(repository.symbol_waterfall("NVDA", 60))
            self.assertIsNone(repository.freshness())

    def test_no_scan_ever_recorded_is_an_empty_dict_not_none(self):
        """None means unreadable and {} means never scanned, and the narrative
        branches differently on each."""

        with patch.object(DecisionFunnelRepository, "_fetch_optional", return_value=[]):

            self.assertEqual(DecisionFunnelRepository().freshness(), {})


if __name__ == "__main__":
    unittest.main()
