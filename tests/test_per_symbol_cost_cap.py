"""The per-symbol contract cost cap, and the three places it has to reach.

AVGO, SMH and GOOGL carry the best entry signals in the universe and were
unreachable because the tightest qualifying contract runs a $1,430 median
against a $1,000 cap. The exception raises the cap for those symbols only --
raising it globally would push every other name's contract up with it, which is
the opposite of what the subscriber bands exist for.

The cap is read in three independent places, and an exception that reaches only
some of them is worse than none: the contract passes one gate and is refused by
the next, with a rejection code naming a cap that is not what refused it.
"""

from unittest import mock

import pytest

from app.options import options_filter
from app.options.affordability_config import (
    get_affordability_config,
    per_symbol_cost_caps,
)
from app.options.contract_ranker import prefer_tightest_qualified


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Every test states its own override, including 'none'."""

    monkeypatch.delenv("OPTION_MAX_CONTRACT_COST_BY_SYMBOL", raising=False)
    monkeypatch.setenv("OPTION_MAX_CONTRACT_COST", "1000")
    monkeypatch.setenv("OPTION_PREFERRED_MAX_CONTRACT_COST", "800")
    monkeypatch.setenv("OPTION_AGGRESSIVE_MAX_CONTRACT_COST", "1500")


class TestParsing:

    def test_absent_means_no_overrides(self):
        assert per_symbol_cost_caps() == {}

    def test_pairs_are_parsed_and_upcased(self, monkeypatch):
        monkeypatch.setenv(
            "OPTION_MAX_CONTRACT_COST_BY_SYMBOL", "avgo:2500, SMH:2500"
        )
        assert per_symbol_cost_caps() == {"AVGO": 2500.0, "SMH": 2500.0}

    def test_malformed_entries_are_skipped_not_raised(self, monkeypatch):
        """A bad override must not take the option path down mid-session."""

        monkeypatch.setenv(
            "OPTION_MAX_CONTRACT_COST_BY_SYMBOL",
            "AVGO:2500,GARBAGE,SMH:notanumber,:900,GOOGL:-5",
        )
        assert per_symbol_cost_caps() == {"AVGO": 2500.0}


class TestConfig:

    def test_no_symbol_is_the_global_config(self):
        config = get_affordability_config()
        assert config["max_contract_cost"] == 1000.0
        assert "cost_cap_symbol" not in config

    def test_unlisted_symbol_is_untouched(self, monkeypatch):
        monkeypatch.setenv("OPTION_MAX_CONTRACT_COST_BY_SYMBOL", "AVGO:2500")
        assert get_affordability_config("NVDA")["max_contract_cost"] == 1000.0

    def test_listed_symbol_is_raised_with_its_companions(self, monkeypatch):
        monkeypatch.setenv("OPTION_MAX_CONTRACT_COST_BY_SYMBOL", "AVGO:2500")
        config = get_affordability_config("AVGO")

        assert config["max_contract_cost"] == 2500.0
        # Preference must move with the cap or it still steers to the cheap,
        # decayed corner of the chain.
        assert config["preferred_max_contract_cost"] == 2000.0
        assert config["aggressive_max_contract_cost"] == 3750.0
        assert config["cost_cap_symbol"] == "AVGO"

    def test_an_override_below_the_global_cap_is_ignored(self, monkeypatch):
        """Only raises. Tightening one name silently is not asked for."""

        monkeypatch.setenv("OPTION_MAX_CONTRACT_COST_BY_SYMBOL", "AVGO:400")
        assert get_affordability_config("AVGO")["max_contract_cost"] == 1000.0


def _contract(ticker, cost, spread=2.0):
    return {
        "ticker": ticker,
        "contract_cost": cost,
        "spread_pct": spread,
        "open_interest": 5000,
        "volume": 500,
    }


class TestTightestQualifiedHonoursTheException:
    """The promotion step is what actually picks the contract.

    Reading the global cap here would let an exempt contract through scoring and
    refuse it at promotion -- the exception would look configured and do
    nothing.
    """

    def setup_method(self):
        self.cheap = _contract("O:AVGO260821C00300000", 900.0, spread=2.8)
        self.dear = _contract("O:AVGO260821C00310000", 1430.0, spread=1.2)

    def test_without_the_exception_the_dear_contract_is_not_promoted(
        self, monkeypatch
    ):
        monkeypatch.setenv("OPTION_MAX_SPREAD_PCT", "3")
        ranked = prefer_tightest_qualified([self.cheap, self.dear], "AVGO")
        assert ranked[0] is self.cheap

    def test_with_the_exception_the_tighter_contract_wins(self, monkeypatch):
        monkeypatch.setenv("OPTION_MAX_SPREAD_PCT", "3")
        monkeypatch.setenv("OPTION_MAX_CONTRACT_COST_BY_SYMBOL", "AVGO:2500")
        ranked = prefer_tightest_qualified([self.cheap, self.dear], "AVGO")
        assert ranked[0] is self.dear

    def test_the_exception_does_not_leak_to_another_symbol(self, monkeypatch):
        monkeypatch.setenv("OPTION_MAX_SPREAD_PCT", "3")
        monkeypatch.setenv("OPTION_MAX_CONTRACT_COST_BY_SYMBOL", "AVGO:2500")
        cheap = _contract("O:NVDA260821C00300000", 900.0, spread=2.8)
        dear = _contract("O:NVDA260821C00310000", 1430.0, spread=1.2)
        ranked = prefer_tightest_qualified([cheap, dear], "NVDA")
        assert ranked[0] is cheap


class TestUnderlyingParsedFromTheTicker:
    """The liquidity filter has no symbol argument and must read the OCC root."""

    @pytest.mark.parametrize(
        "ticker,expected",
        [
            ("O:AVGO260821C00300000", "AVGO"),
            ("O:SMH260821P00250000", "SMH"),
            ("o:googl260821c00200000", "GOOGL"),
            ("AVGO", None),
            ("", None),
            (None, None),
        ],
    )
    def test_roots(self, ticker, expected):
        assert options_filter._underlying_of({"ticker": ticker}) == expected

    def test_missing_option_data_is_not_an_error(self):
        assert options_filter._underlying_of(None) is None
        assert options_filter._underlying_of({}) is None

    def test_the_filter_asks_for_the_config_of_that_underlying(
        self, monkeypatch
    ):
        """Without this the contract passes the ranker and is then refused here
        as OPTION_TOO_EXPENSIVE, naming a cap that is not what refused it."""

        monkeypatch.setenv("OPTION_MAX_CONTRACT_COST_BY_SYMBOL", "AVGO:2500")
        seen = []
        real = options_filter.get_affordability_config

        def spy(symbol=None):
            seen.append(symbol)
            return real(symbol)

        with mock.patch.object(options_filter, "get_affordability_config", spy):
            options_filter.evaluate_option_liquidity(
                _contract("O:AVGO260821C00310000", 1430.0)
            )

        assert seen and seen[0] == "AVGO"


class TestWatchlist:

    def test_the_three_removed_names_are_gone(self):
        from app.config.watchlist import WATCHLIST

        for symbol in ("MU", "META", "ARM"):
            assert symbol not in WATCHLIST, (
                f"{symbol} produced a 0% win rate across 122 trades on the best "
                "contract its chain offers; see TRADE_QUALITY_PLAN 7.3c"
            )

    def test_the_three_kept_names_are_present(self):
        from app.config.watchlist import WATCHLIST

        for symbol in ("AVGO", "SMH", "GOOGL"):
            assert symbol in WATCHLIST
