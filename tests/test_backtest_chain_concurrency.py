"""Pricing a chain concurrently must not be observable in the chain.

A replay day is network-bound, not CPU-bound: `build_frames` measured at 273s of
a ~35 minute day, about 13%, while a single selection prices up to 72 contracts
and every one needs its own NBBO fetched one at a time. Roughly 0.8s each,
almost all of it spent waiting.

The pool is therefore an I/O optimisation and nothing else. If it can reorder a
chain, change a skip count, or drop a contract under contention then the parity
fixtures stop being a valid check on it, so those properties are pinned here
directly rather than inferred from a faster wall clock.

Hermetic; no Polygon access.
"""

import unittest
from unittest.mock import patch

from app.backtesting import contract_selector
from app.backtesting.contract_selector import build_historical_chain, chain_workers


def _candidate(index):
    """(moneyness, dte, ticker, spec) as `_candidate_tickers` yields it."""

    return (
        float(index),
        7,
        f"O:NVDA260807C{index:08d}",
        {"strike": float(index), "contract_type": "call",
         "expiry": __import__("datetime").date(2026, 8, 7)},
    )


class ChainOrderTests(unittest.TestCase):
    """The pool must produce exactly what the sequential loop produced."""

    def _chain(self, candidates, priced, workers):
        """Drive build_historical_chain over a scripted pricing function."""

        def price(underlying, moment, spot, config, candidate):
            ticker = candidate[2]
            return priced(ticker)

        with patch.object(contract_selector, "_candidate_tickers",
                          return_value=candidates), \
             patch.object(contract_selector, "_price_candidate", price), \
             patch.object(contract_selector, "chain_workers", return_value=workers):

            return build_historical_chain("NVDA", "2026-08-03", "CALL", 200.0)

    def test_the_chain_keeps_candidate_order_under_concurrency(self):
        """Nearest-the-money first is the prefilter's contract with the ranker."""

        candidates = [_candidate(index) for index in range(20)]

        def priced(ticker):
            return {"ticker": ticker}, None

        concurrent, _ = self._chain(candidates, priced, workers=8)
        sequential, _ = self._chain(candidates, priced, workers=1)

        self.assertEqual(concurrent, sequential)
        self.assertEqual(
            [contract["ticker"] for contract in concurrent],
            [candidate[2] for candidate in candidates],
        )

    def test_skip_counts_are_identical_either_way(self):

        candidates = [_candidate(index) for index in range(30)]

        def priced(ticker):
            tail = int(ticker[-8:])

            if tail % 3 == 0:
                return None, "no_quote"

            if tail % 3 == 1:
                return None, "no_greeks"

            return {"ticker": ticker}, None

        concurrent_chain, concurrent_skips = self._chain(candidates, priced, 8)
        sequential_chain, sequential_skips = self._chain(candidates, priced, 1)

        self.assertEqual(concurrent_skips, sequential_skips)
        self.assertEqual(concurrent_chain, sequential_chain)
        self.assertEqual(concurrent_skips["no_quote"], 10)
        self.assertEqual(concurrent_skips["no_greeks"], 10)
        self.assertEqual(len(concurrent_chain), 10)

    def test_an_empty_candidate_list_short_circuits(self):

        chain, skipped = self._chain([], lambda ticker: (None, None), workers=8)

        self.assertEqual(chain, [])
        self.assertEqual(skipped, {"no_price": 0, "no_quote": 0, "no_greeks": 0})

    def test_a_failure_in_one_contract_is_not_swallowed(self):
        """A pool that eats exceptions would turn a fault into "no trade"."""

        candidates = [_candidate(index) for index in range(8)]

        def priced(ticker):
            if ticker.endswith("00000003"):
                raise RuntimeError("polygon down")
            return {"ticker": ticker}, None

        with self.assertRaises(RuntimeError):
            self._chain(candidates, priced, workers=4)


class WorkerCountTests(unittest.TestCase):

    def test_the_default_stays_well_under_the_rate_limit(self):
        """1,200/min is 20/s; 8 in flight cannot be what trips it."""

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(chain_workers(), 8)

    def test_one_worker_restores_sequential_pricing(self):

        with patch.dict("os.environ", {"BACKTEST_CHAIN_WORKERS": "1"}):
            self.assertEqual(chain_workers(), 1)

    def test_the_count_is_clamped_and_never_zero(self):

        for value, expected in (("0", 1), ("-4", 1), ("999", 32), ("nonsense", 8)):
            with patch.dict("os.environ", {"BACKTEST_CHAIN_WORKERS": value}):
                self.assertEqual(chain_workers(), expected, value)


if __name__ == "__main__":
    unittest.main()
