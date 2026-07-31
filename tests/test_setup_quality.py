"""Setup % must measure the setup, and must not go flat where it matters.

Measured against the 1,633-row scanner_snapshot archive, the old composition
`|score|*40 + RR*25 + entry*15 + action*20` had rank correlation 0.675 against RR
and 0.473 against |15m Score| -- it tracked the thing the gate already checks
separately more closely than the thing it was named for. And `min(|score|/10, 1)`
was saturated for 54% of gate-eligible rows.
"""

import unittest

from app.gates import setup_quality as sq
from app.gates.setup_quality import compute_setup_percent, setup_percent_from_row


def _score(score, alignment=8.0, entry="EMA_PULLBACK", valid=True, action="ENTER_PAPER"):
    return compute_setup_percent(
        score=score, alignment=alignment, entry=entry,
        setup_valid=valid, action_status=action,
    )


class ConvictionTests(unittest.TestCase):

    def test_discriminates_above_the_old_saturation_point(self):
        """The regression: 10 and 21 used to be identical."""

        self.assertLess(_score(10), _score(14))
        self.assertLess(_score(14), _score(16))

    def test_conviction_is_monotonic_across_the_observed_range(self):

        values = [_score(s) for s in (4, 6, 8, 10, 12, 14, 16)]

        self.assertEqual(values, sorted(values))
        self.assertEqual(len(set(values)), len(values))

    def test_direction_does_not_matter(self):
        """A -12 short and a +12 long are equally convicted."""

        self.assertEqual(_score(12), _score(-12))

    def test_saturates_only_beyond_the_observed_ceiling(self):

        self.assertEqual(_score(16), _score(24))


class DoubleCountingTests(unittest.TestCase):
    """RR and Action Status are hard gates; scoring them again distorts."""

    def test_rr_is_not_an_input(self):

        self.assertEqual(
            setup_percent_from_row({
                "15m Score": 12, "Alignment Score": 8, "Entry": "EMA_PULLBACK",
                "Setup Valid": True, "Action Status": "ENTER_PAPER", "Risk Reward": 1.0,
            }),
            setup_percent_from_row({
                "15m Score": 12, "Alignment Score": 8, "Entry": "EMA_PULLBACK",
                "Setup Valid": True, "Action Status": "ENTER_PAPER", "Risk Reward": 9.9,
            }),
        )

    def test_actionable_status_grants_no_conviction_credit(self):
        """ENTER_PAPER vs WATCH: both tradeable-shaped, same setup, same score."""

        self.assertEqual(_score(12, action="ENTER_PAPER"), _score(12, action="WATCH"))


class AlignmentTests(unittest.TestCase):

    def test_alignment_contributes(self):

        self.assertLess(_score(12, alignment=0), _score(12, alignment=12))

    def test_missing_alignment_is_not_an_error(self):

        self.assertGreater(
            compute_setup_percent(score=12, alignment=None, entry="BREAKOUT",
                                  setup_valid=True, action_status="ENTER_PAPER"),
            0,
        )


class UnusableRowTests(unittest.TestCase):
    """Caps keep untradeable rows below tradeable ones for ranking and analytics."""

    def test_unvalidated_setup_is_capped(self):

        self.assertLessEqual(
            _score(24, valid=False, action="WAIT"), sq.UNVALIDATED_SETUP_CEILING
        )

    def test_review_tv_chart_is_exempt_from_the_validity_cap(self):

        self.assertGreater(
            _score(24, valid=False, action="REVIEW_TV_CHART"),
            sq.UNVALIDATED_SETUP_CEILING,
        )

    def test_untradeable_actions_are_capped_hardest(self):

        for action in ("AVOID", "NO_BID_ASK", "RATE_LIMITED"):
            self.assertLessEqual(
                _score(24, action=action), sq.UNTRADEABLE_ACTION_CEILING, action
            )

    def test_no_entry_scores_below_a_real_setup(self):

        self.assertLess(_score(12, entry="NO_ENTRY"), _score(12, entry="EMA_PULLBACK"))


class RangeTests(unittest.TestCase):

    def test_stays_within_zero_and_one_hundred(self):

        for score in (-99, -16, 0, 16, 99):
            value = _score(score)
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, 100)

    def test_garbage_inputs_do_not_raise(self):

        self.assertGreaterEqual(
            setup_percent_from_row({"15m Score": "n/a", "Entry": None}), 0
        )
        self.assertGreaterEqual(setup_percent_from_row({}), 0)


class ThresholdOrderingTests(unittest.TestCase):
    """The tiers must stay ordered; the old scale had collapsed them together."""

    def test_tiers_are_strictly_increasing(self):

        tiers = [
            sq.MIN_SETUP_BASE, sq.MIN_SETUP_MULTIDAY, sq.MIN_SETUP_ELEVATED,
            sq.MIN_SETUP_WEAK_BREADTH, sq.MIN_SETUP_RANGE_BOUND,
        ]

        self.assertEqual(tiers, sorted(tiers))
        self.assertEqual(len(set(tiers)), len(tiers))

    def test_grade_bands_are_ordered_and_reachable(self):

        floors = [floor for floor, _ in sq.GRADE_BANDS]

        self.assertEqual(floors, sorted(floors, reverse=True))
        self.assertTrue(sq.setup_grade(100).startswith("A+"))
        self.assertTrue(sq.setup_grade(0).startswith("D"))


class SingleImplementationTests(unittest.TestCase):
    """The scanner and the dashboard had byte-identical copies of this rule."""

    def test_scanner_and_dashboard_agree(self):
        from app.dashboard import _compute_setup_percent
        from app.main import _compute_setup_percent_for_gate

        row = {
            "15m Score": 13.5, "Alignment Score": 9.0, "Entry": "EMA_PULLBACK",
            "Setup Valid": True, "Action Status": "ENTER_PAPER", "Risk Reward": 2.1,
        }

        self.assertEqual(_compute_setup_percent(row), _compute_setup_percent_for_gate(row))


if __name__ == "__main__":
    unittest.main()
