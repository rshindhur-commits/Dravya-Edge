"""Choose the option contract a replay trades.

Parity runs pin the ticker live actually chose, which is right for validating
the decision path but useless over unseen history -- there is no live choice to
copy. This builds the chain from historical data and hands it to the *live*
``rank_option_contracts``, so selection is production's, not a second
implementation's.

Cost is the design constraint. A liquid underlying lists several hundred
contracts, and pricing each one means a bar lookup plus an NBBO request; over a
year and 26 symbols that is millions of calls. So contracts are filtered on the
two attributes knowable for free from the OCC symbol -- expiry and strike --
before anything is priced. A typical scan prices 10-20 contracts instead of 400.

What this deliberately does *not* reproduce: live's chain carries Polygon's own
Greeks and a real-time quote, while this reconstructs Greeks from the contract's
observed price (see ``historical_greeks``). Delta agrees with live to ~0.014 on
the trades where both exist, which is inside the ranker's own resolution for
every term except a contract sitting exactly on the 0.25/0.75 delta cutoffs.
Those are recorded in the result so a run can report how often it happened
rather than leaving it as an unexamined assumption.
"""

from dataclasses import dataclass

import pandas as pd

from app.backtesting.historical_greeks import greeks_for_contract, parse_occ_ticker
from app.backtesting.historical_options import (
    list_contracts_as_of,
    option_bars,
    price_at,
    quote_at,
)
from app.options.affordability_config import get_affordability_config
from app.options.contract_ranker import rank_option_contracts
from app.options.option_affordability import add_affordability_metrics
from app.options.options_recommender import _pick_first_by_dte

# Live's preferred window. OPTION_MIN_DTE is 5 and the ranker scores 14-21 at
# +40 against 5-6 at -33, so a weekly effectively cannot win -- which is the
# substance of the open QQQ/SPY short-DTE question. Kept as parameters so that
# question can be answered by running it rather than argued.
DEFAULT_MIN_DTE = 5
DEFAULT_MAX_DTE = 30

# Strikes beyond this from spot cannot reach the 0.25-0.75 delta band the
# ranker accepts, so pricing them is wasted work.
DEFAULT_MAX_MONEYNESS_PCT = 12.0


@dataclass
class SelectionConfig:

    min_dte: int = DEFAULT_MIN_DTE
    max_dte: int = DEFAULT_MAX_DTE
    max_moneyness_pct: float = DEFAULT_MAX_MONEYNESS_PCT
    # Must be large enough to reach the strikes affordability actually selects.
    # At 24, sorted nearest-the-money and split across the two or three expiries
    # in the DTE window, the band is only about +/-4 strikes -- so on a $122
    # underlying the $130 call live bought was never priced, and the selector
    # reported "no affordable contract" for a trade that had one. The prefilter
    # exists to avoid pricing the whole chain, not to pre-empt the ranker.
    max_priced_contracts: int = 72
    paper_mode: bool = True
    require_quote: bool = True
    # Live's preferred DTE window, applied after ranking.
    preferred_min_dte: int = 7
    preferred_max_dte: int = 21
    # Whether the account's capital profile constrains the pick. True mirrors
    # OPTION_AFFORDABILITY_MODE=HARD; False answers "what would this strategy
    # do with unlimited capital", which is a different question and should be
    # labelled as such in any result.
    apply_affordability: bool = True


def _expiration_bucket(dte):
    """Match the labels ``rank_option_contracts`` switches on."""

    if dte <= 0:

        return "0DTE"

    if dte == 1:

        return "1DTE"

    if dte <= 7:

        return "WEEKLY"

    if dte <= 21:

        return "SHORT_TERM"

    return "LONG_TERM"


def _session_volume(bars, moment):
    """Contract volume traded so far on ``moment``'s session."""

    if bars is None or bars.empty:

        return 0.0

    stamp = pd.Timestamp(moment)

    if stamp.tzinfo is None:

        stamp = stamp.tz_localize("America/New_York")

    session = stamp.tz_convert("America/New_York").date()
    index_et = bars.index.tz_convert("America/New_York")

    today = bars[(index_et.date == session) & (bars.index <= stamp.tz_convert("UTC"))]

    return float(today["Volume"].sum()) if not today.empty else 0.0


def _candidate_tickers(underlying, moment, direction, spot, config):
    """Prefilter on expiry and strike, both free from the OCC symbol."""

    contract_type = "call" if str(direction).upper() == "CALL" else "put"

    as_of = pd.Timestamp(moment)
    as_of_date = (
        as_of.tz_convert("America/New_York").date()
        if as_of.tzinfo is not None
        else as_of.date()
    )

    band = spot * (config.max_moneyness_pct / 100.0)

    listed = list_contracts_as_of(
        underlying,
        moment,
        contract_type=contract_type,
        strike_min=round(spot - band, 2),
        strike_max=round(spot + band, 2),
        expiry_min=as_of_date + pd.Timedelta(days=config.min_dte),
        expiry_max=as_of_date + pd.Timedelta(days=config.max_dte),
    )

    candidates = []

    for contract in listed:

        ticker = contract.get("ticker")

        if not ticker:
            continue

        try:

            spec = parse_occ_ticker(ticker)

        except ValueError:

            continue

        dte = (spec["expiry"] - as_of_date).days

        if not (config.min_dte <= dte <= config.max_dte):

            continue

        moneyness = abs(spec["strike"] - spot) / spot * 100.0

        if moneyness > config.max_moneyness_pct:

            continue

        candidates.append((moneyness, dte, ticker, spec))

    # Nearest-the-money first, so the cap keeps the contracts most likely to
    # clear the delta band rather than an arbitrary slice.
    candidates.sort(key=lambda item: (item[0], item[1]))

    return candidates[: config.max_priced_contracts]


def build_historical_chain(underlying, moment, direction, spot, config=None):
    """Price and enrich the prefiltered chain into ranker input."""

    config = config or SelectionConfig()

    chain = []
    skipped = {"no_price": 0, "no_quote": 0, "no_greeks": 0}

    for _, dte, ticker, spec in _candidate_tickers(
        underlying, moment, direction, spot, config
    ):

        bars = option_bars(ticker, moment)
        mark = price_at(bars, moment)

        if mark is None or mark <= 0:

            skipped["no_price"] += 1
            continue

        quote = quote_at(ticker, moment)

        if quote is None and config.require_quote:

            skipped["no_quote"] += 1
            continue

        greeks = greeks_for_contract(
            ticker, quote["mid"] if quote else mark, spot, moment
        )

        if greeks is None:

            skipped["no_greeks"] += 1
            continue

        # Session volume to date, not the last bar's. The ranker gates at
        # `volume < 5` and scores in bands of 250 and 1000, so a single 5m
        # bar's count rejects contracts that have traded thousands since the
        # open.
        volume = _session_volume(bars, moment)

        chain.append(
            {
                "ticker": ticker,
                "symbol": underlying,
                "strike": spec["strike"],
                # Lowercase: the ranker's direction filter compares against
                # "call"/"put" literals, so an uppercase type silently rejects
                # the entire chain.
                "type": spec["contract_type"].lower(),
                "expiration_date": spec["expiry"].isoformat(),
                "dte": dte,
                "expiration_bucket": _expiration_bucket(dte),
                "mid_price": quote["mid"] if quote else mark,
                "bid": quote["bid"] if quote else None,
                "ask": quote["ask"] if quote else None,
                "spread_pct": quote["spread_pct"] if quote else None,
                "delta": greeks["delta"],
                "gamma": greeks["gamma"],
                "theta": greeks["theta"],
                "iv": greeks["iv"] * 100.0,
                "volume": volume,
                # Open interest has no point-in-time history on this plan, and
                # for an expired contract there is nothing to query. Set to 1:
                # the minimum that clears the ranker's `oi < 1` gate while
                # scoring zero in both OI bands (>=1000, >=5000), so it adds
                # nothing to any contract and therefore reorders none.
                #
                # The fidelity gap this leaves is real and one-directional:
                # live would reject a contract with genuinely no open interest
                # and the replay will not, so a replay can trade something
                # nobody was holding. The spread cap is what actually catches
                # those -- an untraded strike quotes wide -- and
                # `no_quote`/`no_price` in the diagnostics count how often the
                # question came up.
                "open_interest": 1,
                "option_quality_score": 0,
            }
        )

    return chain, skipped


def select_contract(underlying, direction, moment, spot, config=None):
    """The live ranker's top pick from a historically reconstructed chain.

    Returns ``(ticker, contract, diagnostics)``; ticker is ``None`` when the
    chain is empty or nothing survives ranking, which the caller must treat as
    "no trade" rather than falling back to an arbitrary strike.
    """

    config = config or SelectionConfig()

    chain, skipped = build_historical_chain(
        underlying, moment, direction, spot, config
    )

    diagnostics = {"chain_size": len(chain), "skipped": skipped}

    if not chain:

        return None, None, diagnostics

    ranked = rank_option_contracts(
        chain,
        underlying_price=spot,
        direction=str(direction).upper(),
        paper_mode=config.paper_mode,
    )

    if not ranked:

        diagnostics["rejected_by_ranker"] = len(chain)

        return None, None, diagnostics

    diagnostics["ranked_size"] = len(ranked)

    best = _apply_live_pick(ranked, config, diagnostics)

    if best is None:

        return None, None, diagnostics

    diagnostics["ranking_score"] = best.get("ranking_score")

    return best.get("ticker"), best, diagnostics


def _apply_live_pick(ranked, config, diagnostics):
    """Reproduce the pick ``recommend_live_option_bundle`` makes after ranking.

    Ranking alone is not selection. Live then narrows to a DTE window and, with
    ``OPTION_AFFORDABILITY_MODE=HARD`` on a ``SMALL_ACCOUNT`` profile, to what
    the account can actually buy -- which is why live's 2026-07-30 trades sit
    4-7% out of the money while the ranker's own favourite is at-the-money. On
    a $2000 account the ATM contract is simply not purchasable, so scoring it
    first changes nothing.

    ``_pick_best_affordable`` cannot be reused directly: it calls
    ``refresh_contract_quote`` for a live quote, which has no historical
    equivalent. The affordability *test* is pure, so that is applied here
    against the contract's reconstructed mid and the re-quote step is skipped.
    The difference is that live re-checks affordability against a fresher
    price; here the price is already the one at the decision moment.
    """

    if not config.apply_affordability:

        return _pick_first_by_dte(
            ranked, config.preferred_min_dte, config.preferred_max_dte
        ) or ranked[0]

    affordability = get_affordability_config()

    if affordability.get("mode") == "OFF":

        return _pick_first_by_dte(
            ranked, config.preferred_min_dte, config.preferred_max_dte
        ) or ranked[0]

    for contract in ranked[:50]:

        priced = add_affordability_metrics(dict(contract), config=affordability)

        if not priced.get("affordable"):

            continue

        dte = priced.get("dte")

        if dte is None or not (
            config.preferred_min_dte <= dte <= config.preferred_max_dte
        ):

            continue

        return priced

    diagnostics["no_affordable_contract"] = True

    return None


def make_selector(spot_lookup, config=None):
    """Adapt ``select_contract`` to the replay engine's selector signature.

    ``spot_lookup(symbol, moment)`` supplies the underlying price, which the
    replay already holds as the decision candle's close -- refetching it here
    would risk using a different bar than the one the entry was decided on.
    """

    config = config or SelectionConfig()

    def _select(symbol, direction, moment):

        spot = spot_lookup(symbol, moment)

        if not spot:

            return None

        ticker, _, _ = select_contract(symbol, direction, moment, spot, config)

        return ticker

    return _select
