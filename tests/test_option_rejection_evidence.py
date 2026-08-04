"""A rejection has to say which contract, and against what number.

MSFT produced 29 option rejections on 2026-08-03 -- "Low open interest" and
"Wide bid/ask spread" -- and every one of them recorded the reason with the
ticker, the open interest and the threshold all absent. The question that
decides what to do about it, *is the 500 floor too high or is the selector
reaching for an illiquid strike*, could not be answered from the record.

The contract cost cap blocked exactly one decision that whole day, so the
rejection mix is where the trades actually go and it was the least legible part
of the ledger.
"""

import unittest
from unittest.mock import patch

from app.options.options_filter import evaluate_option_liquidity


def _contract(**overrides):
    """An MSFT call that is fine except where a test says otherwise."""

    contract = {
        "ticker": "O:MSFT260821C00490000",
        "type": "call",
        "strike": 490.0,
        "expiration": "2026-08-21",
        "dte": 18,
        "open_interest": 5000,
        "volume": 900,
        "bid": 9.70,
        "ask": 9.90,
        "mid_price": 9.80,
        "delta": 0.52,
        "iv": 0.28,
        "quote_status": "OK",
        "quote_freshness": "LIVE_QUOTE",
        "quote_timeframe": "REALTIME",
        "expiration_bucket": "PREFERRED_14_30",
        "option_quality_score": 95,
    }
    contract.update(overrides)
    return contract


def _evaluate(contract):
    """Affordability is a separate gate; stub it so it cannot mask the verdict."""

    with patch("app.options.options_filter.add_affordability_metrics",
               side_effect=lambda data, config=None: data), \
         patch("app.options.options_filter.get_affordability_config",
               return_value={"mode": "OFF"}):

        return evaluate_option_liquidity(contract)


class RejectionEvidenceTests(unittest.TestCase):

    def test_a_low_open_interest_rejection_names_the_contract_and_the_floor(self):
        """The MSFT case. 'Low open interest' on its own is unactionable."""

        with patch("app.options.options_filter.settings") as settings:
            settings.option_require_bid_ask = False
            settings.option_require_fresh_quote = False
            settings.option_min_open_interest = 500
            settings.option_min_volume = 100
            settings.option_max_spread_pct = 6.0
            settings.option_min_quality_score = 65

            result = _evaluate(_contract(open_interest=120))

        self.assertFalse(result["liquid"])
        self.assertEqual(result["code"], "LOW_OPEN_INTEREST")
        self.assertEqual(result["ticker"], "O:MSFT260821C00490000")
        self.assertEqual(result["open_interest"], 120)
        self.assertEqual(result["required_value"], 500)
        self.assertEqual(result["strike"], 490.0)
        self.assertEqual(result["dte"], 18)

    def test_a_wide_spread_rejection_carries_its_own_ceiling(self):

        with patch("app.options.options_filter.settings") as settings:
            settings.option_require_bid_ask = False
            settings.option_require_fresh_quote = False
            settings.option_min_open_interest = 500
            settings.option_min_volume = 100
            settings.option_max_spread_pct = 6.0
            settings.option_min_quality_score = 65

            result = _evaluate(_contract(bid=9.00, ask=10.60))

        self.assertFalse(result["liquid"])
        self.assertEqual(result["code"], "WIDE_SPREAD")
        self.assertEqual(result["required_value"], 6.0)
        self.assertEqual(result["bid"], 9.00)
        self.assertEqual(result["ask"], 10.60)

    def test_evidence_never_overwrites_a_verdict_field(self):
        """Branches round spread_pct deliberately; the raw quote must not win."""

        with patch("app.options.options_filter.settings") as settings:
            settings.option_require_bid_ask = False
            settings.option_require_fresh_quote = False
            settings.option_min_open_interest = 500
            settings.option_min_volume = 100
            settings.option_max_spread_pct = 6.0
            settings.option_min_quality_score = 65

            contract = _contract(open_interest=120)
            contract["spread_pct"] = 999.0

            result = _evaluate(contract)

        self.assertNotEqual(result["spread_pct"], 999.0)

    def test_a_passing_contract_is_left_alone(self):
        """Evidence is for rejections; a healthy verdict keeps its old shape."""

        with patch("app.options.options_filter.settings") as settings:
            settings.option_require_bid_ask = False
            settings.option_require_fresh_quote = False
            settings.option_min_open_interest = 500
            settings.option_min_volume = 100
            settings.option_max_spread_pct = 6.0
            settings.option_min_quality_score = 65

            result = _evaluate(_contract())

        self.assertTrue(result["liquid"])
        self.assertNotIn("required_value", result)
        self.assertNotIn("open_interest", result)

    def test_absent_fields_are_omitted_rather_than_blanked(self):
        """A missing OI and an OI of zero are different facts."""

        with patch("app.options.options_filter.settings") as settings:
            settings.option_require_bid_ask = False
            settings.option_require_fresh_quote = False
            settings.option_min_open_interest = 500
            settings.option_min_volume = 100
            settings.option_max_spread_pct = 6.0
            settings.option_min_quality_score = 65

            contract = _contract(open_interest=0)
            contract["ticker"] = None
            contract["iv"] = None

            result = _evaluate(contract)

        self.assertFalse(result["liquid"])
        self.assertNotIn("ticker", result)
        self.assertNotIn("iv", result)


class AttemptEvidenceTests(unittest.TestCase):
    """The verdict carried the evidence; the attempt record threw it away.

    `_select_liquid_option_from_bundle` built each attempt from a fixed seven
    keys, so 18,026 attempts were logged on 2026-08-03 and not one of them
    recorded an open interest, a cost or a delta -- the fields that decide
    whether a threshold is wrong or the selector is reaching.
    """

    def _select(self, bundle):

        from app.main import _select_liquid_option_from_bundle

        with patch("app.options.options_filter.add_affordability_metrics",
                   side_effect=lambda data, config=None: data), \
             patch("app.options.options_filter.get_affordability_config",
                   return_value={"mode": "OFF"}), \
             patch("app.main.get_affordability_config",
                   return_value={"mode": "OFF"}), \
             patch("app.main.add_affordability_metrics",
                   side_effect=lambda data, config=None: data), \
             patch("app.main.refresh_contract_quote",
                   side_effect=lambda data: data), \
             patch("app.options.options_filter.settings") as settings:

            settings.option_require_bid_ask = False
            settings.option_require_fresh_quote = False
            settings.option_min_open_interest = 500
            settings.option_min_volume = 100
            settings.option_max_spread_pct = 6.0
            settings.option_min_quality_score = 65

            return _select_liquid_option_from_bundle(bundle, "CALL")

    def test_a_rejected_attempt_records_the_contract_behind_it(self):

        contract, liquidity, attempts = self._select(
            {"active": _contract(open_interest=120)}
        )

        self.assertIsNone(contract)
        self.assertEqual(len(attempts), 1)

        attempt = attempts[0]
        self.assertEqual(attempt["code"], "LOW_OPEN_INTEREST")
        self.assertEqual(attempt["open_interest"], 120)
        self.assertEqual(attempt["required_value"], 500)
        self.assertEqual(attempt["delta"], 0.52)
        self.assertEqual(attempt["dte"], 18)

    def test_the_attempts_own_fields_are_not_overwritten_by_evidence(self):
        """`ticker` and `spread_pct` are set from the candidate, not the quote."""

        _contract_out, _liquidity, attempts = self._select(
            {"active": _contract(open_interest=120)}
        )

        self.assertEqual(attempts[0]["ticker"], "O:MSFT260821C00490000")
        self.assertEqual(attempts[0]["source"], "active")
        self.assertIs(attempts[0]["accepted"], False)

    def test_an_accepted_attempt_carries_no_rejection_evidence(self):
        """A passing verdict keeps its shape, so the audit stays readable."""

        contract, _liquidity, attempts = self._select({"active": _contract()})

        self.assertIsNotNone(contract)
        self.assertEqual(len(attempts), 1)
        self.assertTrue(attempts[0]["accepted"])
        self.assertNotIn("required_value", attempts[0])
        self.assertNotIn("open_interest", attempts[0])


if __name__ == "__main__":
    unittest.main()
