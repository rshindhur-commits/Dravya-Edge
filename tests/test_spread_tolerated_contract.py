"""A signal refused only on spread keeps its contract.

The selector returned `None, None, attempts` when nothing passed liquidity, so a
spread-blocked candidate reached the row with no `Option Ticker`, no bid and no
ask. `build_action_decision` would stamp it REVIEW_TV_CHART -- an alert naming no
contract, and with nothing in the paper book, no exit would ever be sent for it.
A subscriber getting an entry with no exit is worse than getting nothing.

What is tested here is the boundary, not the feature: the fallback must reach for
a contract that failed *only* the spread, and must not reach for one that would
also have failed the cost cap, the quality floor, or the expiry policy -- none of
which ever ran, because spread short-circuited them.
"""

import unittest
from unittest.mock import patch

from app.main import _select_liquid_option_from_bundle
from app.options.options_filter import evaluate_option_liquidity


def _contract(**overrides):
    """A contract that passes every gate, before overrides."""

    base = {
        "ticker": "O:AMD260821C00170000",
        "type": "call",
        "strike": 170.0,
        "expiration": "2026-08-21",
        "open_interest": 5000,
        "volume": 2000,
        "bid": 1.00,
        "ask": 1.02,
        "quote_status": "OK",
        "quote_freshness": "LIVE_QUOTE",
        "quote_timeframe": "REALTIME",
        "expiration_bucket": "WEEKLY",
        "option_quality_score": 90,
        "contract_cost": 101.0,
        "delta": 0.45,
        "affordable": True,
    }
    base.update(overrides)

    return base


def _verdict(contract, ignore_spread=False, mode="SOFT"):
    """The liquidity verdict with affordability held still.

    `add_affordability_metrics` recomputes `affordable` from live capital, which
    would decide these tests instead of the gate under test.
    """

    with patch("app.options.options_filter.add_affordability_metrics",
               side_effect=lambda data, config=None: data), \
        patch("app.options.options_filter.get_affordability_config",
              return_value={"mode": mode}):

        return evaluate_option_liquidity(contract, ignore_spread=ignore_spread)


class SpreadToleratedVerdictTests(unittest.TestCase):

    def test_wide_spread_still_rejected_by_default(self):

        verdict = _verdict(_contract(bid=1.00, ask=1.40))

        self.assertFalse(verdict["liquid"])
        self.assertEqual(verdict["code"], "WIDE_SPREAD")
        # Absent, not False -- the default path never considers tolerating it.
        self.assertNotIn("spread_tolerated", verdict)

    def test_ignore_spread_marks_it_tolerated_but_never_liquid(self):

        verdict = _verdict(_contract(bid=1.00, ask=1.40), ignore_spread=True)

        # The contract is usable for a directional alert. It is still not
        # liquid, and anything asking that question gets the same answer as
        # before.
        self.assertFalse(verdict["liquid"])
        self.assertEqual(verdict["code"], "WIDE_SPREAD")
        self.assertTrue(verdict["spread_tolerated"])
        self.assertGreater(verdict["spread_pct"], 0)

    def test_ignore_spread_does_not_skip_the_cost_cap(self):
        """The gate spread was short-circuiting.

        `option_max_spread_pct` is checked before affordability, so a contract
        refused on spread was never measured against the cost cap at all.
        Accepting the stored WIDE_SPREAD verdict would admit a contract over its
        cap while reporting spread as the only thing wrong with it.
        """

        verdict = _verdict(
            _contract(bid=1.00, ask=1.40, affordable=False,
                      affordability_status="OPTION_TOO_EXPENSIVE"),
            ignore_spread=True,
            mode="HARD"
        )

        self.assertFalse(verdict["liquid"])
        self.assertEqual(verdict["code"], "OPTION_TOO_EXPENSIVE")
        self.assertNotIn("spread_tolerated", verdict)

    def test_ignore_spread_does_not_skip_the_quality_floor(self):

        # Short DTE (-25, below the configured minimum) and high theta (-15)
        # put it at 60 with no help from the spread charge. Open interest and
        # volume stay healthy, because both are checked *before* quality and
        # would otherwise decide this test.
        verdict = _verdict(
            _contract(bid=1.00, ask=1.40, dte=3,
                      expiration_bucket="SHORT_DTE_2_6", theta=0.35),
            ignore_spread=True
        )

        self.assertEqual(verdict["code"], "LOW_OPTION_QUALITY")
        self.assertNotIn("spread_tolerated", verdict)

    def test_the_spread_is_not_charged_twice(self):
        """The gate that would have made this feature do nothing.

        `score_option_quality` docks 35 points for a spread over the same ceiling
        `ignore_spread` just decided to tolerate, and the floor is 65 -- so a
        wide contract lands exactly on the floor and any second deduction puts it
        under. Measured over the 21 days to 2026-08-22: a wide spread and a failed
        quality score co-occurred 1,939 times; a wide spread with quality passing,
        3 times. Without this the fallback rescues almost nothing and blames
        quality for it.
        """

        wide = _contract(
            bid=1.00,
            ask=1.40,
            spread_pct=33.3,
            dte=21,
            theta=0.05,
            # What the enricher stored, computed with the spread charge included.
            option_quality_score=55,
            option_quality_reasons="wide spread; low volume",
        )

        # Strict, the spread short-circuits before quality is ever consulted --
        # which is why the double charge stayed invisible.
        self.assertEqual(_verdict(dict(wide))["code"], "WIDE_SPREAD")

        # Tolerated, the stored 55 must not be what decides it.
        tolerated = _verdict(dict(wide), ignore_spread=True)

        self.assertTrue(tolerated["spread_tolerated"])
        self.assertGreaterEqual(tolerated["quality_score"], 65)

    def test_a_contract_that_fails_quality_for_other_reasons_is_still_refused(self):
        """Only the spread charge is credited back, not the whole gate."""

        tolerated = _verdict(
            _contract(bid=1.00, ask=1.40, dte=3,
                      expiration_bucket="SHORT_DTE_2_6", theta=0.35),
            ignore_spread=True
        )

        self.assertEqual(tolerated["code"], "LOW_OPTION_QUALITY")
        self.assertNotIn("spread_tolerated", tolerated)

    def test_a_liquid_contract_is_untouched_by_the_flag(self):

        verdict = _verdict(_contract(), ignore_spread=True)

        self.assertTrue(verdict["liquid"])
        self.assertEqual(verdict["code"], "LIQUID")
        self.assertNotIn("spread_tolerated", verdict)


class SelectorSpreadFallbackTests(unittest.TestCase):

    def _select(self, bundle, liquidity_fn, alerting=True):

        with patch("app.main.refresh_contract_quote", side_effect=lambda c: c), \
            patch("app.main.add_affordability_metrics",
                  side_effect=lambda c, config=None: c), \
            patch("app.main.get_affordability_config", return_value={}), \
            patch("app.main.contract_matches_direction", return_value=True), \
            patch("app.main._alert_spread_blocked_signals", return_value=alerting), \
            patch("app.main.evaluate_option_liquidity", side_effect=liquidity_fn):

            return _select_liquid_option_from_bundle(bundle, "CALL")

    def test_returns_the_spread_rejected_contract_when_nothing_is_liquid(self):

        bundle = {"ranked": [{"ticker": "WIDE", "type": "call"}]}

        def liquidity(candidate, ignore_spread=False):

            if ignore_spread:

                return {"liquid": False, "code": "WIDE_SPREAD",
                        "reason": "Wide bid/ask spread",
                        "spread_tolerated": True, "spread_pct": 9.4}

            return {"liquid": False, "code": "WIDE_SPREAD",
                    "reason": "Wide bid/ask spread", "spread_pct": 9.4}

        selected, verdict, attempts = self._select(bundle, liquidity)

        # The contract reaches the caller, so `Option Ticker`, `Option Bid` and
        # `Option Ask` are on the row and the alert can name a strike.
        self.assertIsNotNone(selected)
        self.assertEqual(selected["ticker"], "WIDE")
        self.assertFalse(verdict["liquid"])
        self.assertTrue(verdict["spread_tolerated"])
        self.assertTrue(attempts[-1]["spread_tolerated"])
        self.assertFalse(attempts[-1]["accepted"])

    def test_a_liquid_contract_still_wins(self):
        """The fallback must not change a single trade taken today."""

        bundle = {
            "ranked": [
                {"ticker": "WIDE", "type": "call"},
                {"ticker": "TIGHT", "type": "call"},
            ]
        }

        def liquidity(candidate, ignore_spread=False):

            if candidate["ticker"] == "WIDE":

                return {"liquid": False, "code": "WIDE_SPREAD",
                        "reason": "Wide bid/ask spread", "spread_pct": 9.4}

            return {"liquid": True, "code": "LIQUID",
                    "reason": "Healthy liquidity", "spread_pct": 1.1}

        selected, verdict, _ = self._select(bundle, liquidity)

        self.assertEqual(selected["ticker"], "TIGHT")
        self.assertTrue(verdict["liquid"])
        self.assertNotIn("spread_tolerated", verdict)

    def test_non_spread_rejections_are_never_retried(self):
        """An alert naming a contract nobody can buy is a promise not kept."""

        bundle = {
            "ranked": [
                {"ticker": "THIN", "type": "call"},
                {"ticker": "NOQUOTE", "type": "call"},
            ]
        }

        def liquidity(candidate, ignore_spread=False):

            if candidate["ticker"] == "THIN":

                return {"liquid": False, "code": "LOW_OPEN_INTEREST",
                        "reason": "Low open interest", "spread_pct": 2.0}

            return {"liquid": False, "code": "MISSING_BID_ASK",
                    "reason": "Missing live bid/ask quote"}

        selected, verdict, attempts = self._select(bundle, liquidity)

        self.assertIsNone(selected)
        self.assertIsNone(verdict)
        self.assertEqual(len(attempts), 2)

    def test_the_switch_turns_the_fallback_off(self):

        bundle = {"ranked": [{"ticker": "WIDE", "type": "call"}]}

        def liquidity(candidate, ignore_spread=False):

            if ignore_spread:

                return {"liquid": False, "code": "WIDE_SPREAD",
                        "reason": "Wide bid/ask spread",
                        "spread_tolerated": True, "spread_pct": 9.4}

            return {"liquid": False, "code": "WIDE_SPREAD",
                    "reason": "Wide bid/ask spread", "spread_pct": 9.4}

        selected, verdict, _ = self._select(bundle, liquidity, alerting=False)

        self.assertIsNone(selected)
        self.assertIsNone(verdict)

    def test_the_best_spread_rejected_contract_wins_not_the_last(self):
        """Bundle order is preference order.

        The old rejection path reported `attempts[-1]`, so a spread rejection was
        described by whichever contract happened to be tried last rather than the
        one the ranker preferred.
        """

        bundle = {
            "ranked": [
                {"ticker": "PREFERRED", "type": "call"},
                {"ticker": "LAST", "type": "call"},
            ]
        }

        def liquidity(candidate, ignore_spread=False):

            if ignore_spread:

                return {"liquid": False, "code": "WIDE_SPREAD",
                        "reason": "Wide bid/ask spread",
                        "spread_tolerated": True, "spread_pct": 9.4}

            return {"liquid": False, "code": "WIDE_SPREAD",
                    "reason": "Wide bid/ask spread", "spread_pct": 9.4}

        selected, _, _ = self._select(bundle, liquidity)

        self.assertEqual(selected["ticker"], "PREFERRED")


if __name__ == "__main__":

    unittest.main()
