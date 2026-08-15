"""The P/L a subscriber is shown must be one their account can reproduce.

`build_trade_exit_alert_message` computed premium as
`(option_current_mid - option_entry_mid)`, which crosses no spread at either end.
The position bought the ask and sold the bid, so the reported figure was a price
nobody could have traded at.

Measured across the 19 closed trades carrying both legs on 2026-08-15: the alerts
reported **$399** of profit against **$119** the fills actually produced -- $14.74
a trade -- and announced 8 winners of 19 where the fills made 5. CRWD on
2026-08-14 went out as **+$55.00** on a trade whose fills made **+$20.00**.

This is the same defect the WIN/LOSS logic beneath it was written to fix. That
change moved the verdict off R and onto premium; the premium it moved onto was
still mid-to-mid. A dollar figure that cannot be reproduced is worse than R was,
because R never looked like dollars.

Mid-to-mid survives as the fallback for trades with no recorded quote, and is
labelled so it cannot be mistaken for a fill.
"""

import unittest

from app.alerts.telegram_alerts import build_trade_exit_alert_message


def trade(**overrides):
    base = {
        "symbol": "CRWD",
        "direction": "PUT",
        "entry_price": 220.56,
        "option_entry_mid": 3.45,
        "option_entry_ask": 3.50,
        "option_close_bid": 3.70,
        "option_contracts": 1,
    }
    base.update(overrides)
    return base


def message(t, current_mid=3.80):
    return build_trade_exit_alert_message(
        "CRWD",
        t,
        exit_reason="MACD bullish crossover (short)",
        option_current_mid=current_mid,
        r_multiple=0.79,
    )


class FillBasisTests(unittest.TestCase):

    def test_it_reports_the_fill_not_the_mid(self):
        """bought 3.50, sold 3.70 -> $20, not the $55 that went out."""

        body = message(trade())

        self.assertIn("+$20.00", body)
        self.assertNotIn("+$55.00", body)
        self.assertNotIn("+$35.00", body)

    def test_a_fill_figure_is_not_labelled_an_estimate(self):

        self.assertNotIn("mid estimate", message(trade()))

    def test_contracts_multiply_the_fill(self):

        body = message(trade(option_contracts=3))

        self.assertIn("+$60.00", body)


class LabelTests(unittest.TestCase):
    """The verdict must follow the fills too, not just the number beside it."""

    def test_a_mid_to_mid_winner_that_lost_on_fills_is_not_a_win(self):
        """entry ask 3.50, exit bid 3.45 -> -$5, while mids show +$0.10."""

        body = message(
            trade(option_entry_mid=3.40, option_entry_ask=3.50,
                  option_close_bid=3.45),
            current_mid=3.50,
        )

        # `_signed_money` renders negatives as "$-5.00"; the sign placement is
        # the formatter's, not this rule's.
        self.assertIn("$-5.00", body)
        self.assertIn("Loss", body)
        self.assertNotIn("✅", body)


class FallbackTests(unittest.TestCase):

    def test_a_missing_exit_quote_falls_back_to_the_mid(self):

        body = message(trade(option_close_bid=None))

        self.assertIn("+$35.00", body)

    def test_the_fallback_says_it_is_an_estimate(self):

        self.assertIn("mid estimate", message(trade(option_close_bid=None)))

    def test_a_missing_entry_quote_also_falls_back(self):

        body = message(trade(option_entry_ask=None))

        self.assertIn("mid estimate", body)

    def test_no_quotes_at_all_reports_no_premium_line_value(self):
        """Neither basis available: the message must still build."""

        body = build_trade_exit_alert_message(
            "CRWD",
            trade(option_entry_ask=None, option_close_bid=None,
                  option_entry_mid=None),
            exit_reason="Hard stop hit (short)",
            option_current_mid=None,
            r_multiple=-1.0,
        )

        self.assertIsInstance(body, str)
        self.assertIn("CRWD", body)


if __name__ == "__main__":
    unittest.main()
