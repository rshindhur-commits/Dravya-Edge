"""Holding profile, forced EOD exits, and carrying a position overnight.

Until now the engine wrote ``holding_profile: "INTRADAY"`` as a literal and
never read it: nothing was force-closed at the bell and nothing was carried, so
whatever was open at the last scan was simply handed back unresolved. That
understates cost in one direction -- an intraday loser contributed no R and no
premium instead of being closed.

The failure mode these guard against is not a wrong classification but a dead
one. Every input to Setup % that the replay cannot supply pushes the score
*down*, and MULTIDAY needs 76 -- against a 59 ceiling when the setup is not
validated, and a 75 ceiling when the alignment term is absent entirely. Either
of those forces INTRADAY on every trade forever, which looks exactly like a
correct implementation and is why the contract and the three timeframe analyses
are all threaded through rather than defaulted.

Not every gap is that decisive, though: with a strong 15m, losing the 5m and 1h
analyses only lowers alignment and MULTIDAY can still clear. So incompleteness
does not announce itself in the profile, and the flag is carried separately.
"""

import unittest
from unittest.mock import patch

import pandas as pd

from app.backtesting.replay_engine import (
    _BIAS_BY_SIGNAL,
    _force_eod_exits,
    _holding_profile,
    _normalise_selection,
    _timeframe_bias,
)
from app.state.holding_policy import holding_policy


def _analysis(signal, score):

    return {"signal": signal, "score": score}


# A contract that clears the MULTIDAY conditions on its own terms.
MULTIDAY_CONTRACT = {
    "expiration_bucket": "PREFERRED_14_30",
    "option_quality_score": 90,
    "dte": 20,
}


class TimeframeBiasParityTests(unittest.TestCase):

    def test_bias_matches_main(self):
        """The copy feeds alignment, which is 25 of Setup %'s 100 points."""

        from app.main import timeframe_bias

        for signal in list(_BIAS_BY_SIGNAL) + ["WEAK/BULLISH", "", "GARBAGE"]:

            self.assertEqual(
                _timeframe_bias({"signal": signal}),
                timeframe_bias({"signal": signal}),
                f"bias diverged for {signal!r}",
            )


class HoldingProfileTests(unittest.TestCase):

    _DEFAULT = object()

    def _derive(self, contract=_DEFAULT, rr=2.5, signal="HIGH CONVICTION BULLISH"):

        return _holding_profile(
            {"entry_type": "BREAKOUT"},
            {"risk_reward": rr},
            dict(MULTIDAY_CONTRACT) if contract is self._DEFAULT else contract,
            (
                _analysis(signal, 14),
                _analysis(signal, 14),
                _analysis(signal, 14),
            ),
        )

    def test_a_strong_setup_on_a_dated_contract_is_multiday(self):
        """If this fails, the MULTIDAY branch is unreachable and untested."""

        profile, complete = self._derive()

        self.assertEqual(profile, "MULTIDAY")
        self.assertTrue(complete)
        self.assertFalse(holding_policy(profile).force_eod_exit)

    def test_a_near_dated_contract_is_intraday(self):
        """SHORT_SWING_7_13 is deliberately excluded from MULTIDAY."""

        profile, _ = self._derive(
            contract={
                "expiration_bucket": "SHORT_SWING_7_13",
                "option_quality_score": 90,
                "dte": 9,
            }
        )

        self.assertEqual(profile, "INTRADAY")
        self.assertTrue(holding_policy(profile).force_eod_exit)

    def test_weak_rr_is_intraday(self):

        profile, _ = self._derive(rr=1.2)

        self.assertEqual(profile, "INTRADAY")

    def test_a_missing_contract_is_reported_incomplete(self):
        """Forced INTRADAY, but never silently."""

        profile, complete = self._derive(contract=None)

        self.assertEqual(profile, "INTRADAY")
        self.assertFalse(complete)

    def test_missing_analyses_are_reported_incomplete(self):
        """Missing 5m/1h lowers alignment; it does not force a profile.

        Asserted deliberately as "still classified, but flagged". A first
        version of this test expected INTRADAY and failed: with a strong 15m
        the remaining alignment term is enough to clear 76 on its own. So the
        incompleteness is not self-announcing in the profile, which is the
        whole reason the flag is carried separately.
        """

        profile, complete = _holding_profile(
            {"entry_type": "BREAKOUT"},
            {"risk_reward": 2.5},
            dict(MULTIDAY_CONTRACT),
            (None, _analysis("HIGH CONVICTION BULLISH", 14), None),
        )

        self.assertIn(profile, {"INTRADAY", "MULTIDAY"})
        self.assertFalse(complete)


class SelectionShapeTests(unittest.TestCase):

    def test_accepts_a_bare_ticker(self):

        self.assertEqual(_normalise_selection("O:X"), ("O:X", None))

    def test_accepts_a_ticker_and_contract(self):

        self.assertEqual(
            _normalise_selection(("O:X", {"dte": 20})), ("O:X", {"dte": 20})
        )

    def test_accepts_nothing(self):

        self.assertEqual(_normalise_selection(None), (None, None))


class ForcedEodExitTests(unittest.TestCase):

    def setUp(self):

        index = pd.date_range(
            "2026-07-29 09:30", periods=80, freq="5min", tz="America/New_York"
        )
        self.frames = {
            "NVDA": pd.DataFrame(
                {
                    "Open": 100.0,
                    "High": 101.0,
                    "Low": 99.0,
                    "Close": 105.0,
                    "Volume": 1000.0,
                },
                index=index,
            )
        }

    def _trade(self, profile):

        from app.backtesting.replay_engine import ReplayTrade

        trade = ReplayTrade(
            symbol="NVDA",
            direction="CALL",
            entry_type="BREAKOUT",
            scan_id="2026-07-29_100000",
            entry_time=pd.Timestamp("2026-07-29 10:00", tz="America/New_York"),
            entry_price=100.0,
            stop_loss=99.0,
            initial_stop_loss=99.0,
            take_profit=103.0,
        )
        trade.holding_profile = profile

        return trade

    def _run(self, profile):

        from app.backtesting.replay_engine import ReplayConfig

        trade = self._trade(profile)
        open_trades = {"NVDA": trade}

        forced = _force_eod_exits(
            open_trades, "2026-07-29", self.frames, ReplayConfig()
        )

        return trade, open_trades, forced

    def test_intraday_is_closed_at_the_bell(self):

        trade, open_trades, forced = self._run("INTRADAY")

        self.assertEqual(len(forced), 1)
        self.assertEqual(open_trades, {})
        self.assertEqual(trade.exit_rule, "FORCE_EOD_EXIT")
        self.assertIsNotNone(trade.r_multiple)

    def test_multiday_is_left_open_to_carry(self):

        trade, open_trades, forced = self._run("MULTIDAY")

        self.assertEqual(forced, [])
        self.assertIn("NVDA", open_trades)
        self.assertTrue(trade.is_open)

    def test_no_bar_means_left_open_not_filled_at_an_invented_price(self):

        trade, open_trades, forced = (None, None, None)

        with patch(
            "app.backtesting.replay_engine.build_frames",
            return_value=(None,) * 6,
        ):

            trade, open_trades, forced = self._run("INTRADAY")

        self.assertEqual(forced, [])
        self.assertTrue(trade.is_open)
        self.assertIsNone(trade.exit_price)


if __name__ == "__main__":

    unittest.main()
