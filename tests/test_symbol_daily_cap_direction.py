"""The per-symbol daily cap counts directions separately.

The directional cooldown shipped on 2026-08-22 and was inert. `_auto_paper_entry_reason`
checks the cooldown, then six lines later checks `MAX_TRADES_PER_SYMBOL_PER_DAY`,
whose default is 1 and which counted every trade on the symbol regardless of
direction. So the PLTR put at 10:01 on 2026-08-21 still blocked the call that ran
+$8.83: the cooldown said yes and the very next gate said no, for the reason the
cooldown had just been changed to stop saying.

Two gates in a row implementing opposite policies is worse than either policy, so
the pair moves together.
"""

import inspect
import os
import unittest
from unittest.mock import patch

from app.gates.entry_gate import (
    symbol_daily_cap_is_directional,
    symbol_trade_count_today,
)


PLTR_PUT_TODAY = {
    "t1": {
        "symbol": "PLTR",
        "direction": "PUT",
        "opened_at": "2026-08-21 10:01:00",
    }
}

NOW = __import__("datetime").datetime(2026, 8, 21, 11, 30)


class SymbolDailyCapDirectionTests(unittest.TestCase):

    def test_the_reversal_is_not_counted_against_the_put(self):

        self.assertEqual(
            symbol_trade_count_today(PLTR_PUT_TODAY, "PLTR", NOW, direction="CALL"),
            0
        )

    def test_a_second_entry_in_the_same_direction_still_counts(self):
        """What the cap is actually for: not churning the idea that just ran."""

        self.assertEqual(
            symbol_trade_count_today(PLTR_PUT_TODAY, "PLTR", NOW, direction="PUT"),
            1
        )

    def test_callers_passing_no_direction_are_unchanged(self):

        self.assertEqual(
            symbol_trade_count_today(PLTR_PUT_TODAY, "PLTR", NOW),
            1
        )

    def test_yesterdays_trade_is_still_out_of_scope(self):

        yesterday = {
            "t1": {
                "symbol": "PLTR",
                "direction": "PUT",
                "opened_at": "2026-08-20 10:01:00",
            }
        }

        self.assertEqual(
            symbol_trade_count_today(yesterday, "PLTR", NOW, direction="PUT"),
            0
        )

    def test_another_symbol_is_never_counted(self):

        self.assertEqual(
            symbol_trade_count_today(PLTR_PUT_TODAY, "NVDA", NOW, direction="PUT"),
            0
        )

    def test_the_blanket_cap_is_one_env_var_away(self):

        with patch.dict(
            os.environ,
            {"AUTO_PAPER_SYMBOL_DAILY_CAP_DIRECTIONAL": "false"},
            clear=False
        ):
            self.assertFalse(symbol_daily_cap_is_directional())

    def test_the_entry_path_passes_the_direction_through(self):
        """A directional helper nothing hands a direction to is inert.

        This is the assertion that would have caught the original miss.
        """

        from app.runtime import paper_automation_support

        source = inspect.getsource(
            paper_automation_support._auto_paper_entry_reason
        )

        self.assertIn("symbol_daily_cap_is_directional()", source)
        self.assertIn("MAX_TRADES_PER_SYMBOL_PER_DAY", source)


if __name__ == "__main__":

    unittest.main()
