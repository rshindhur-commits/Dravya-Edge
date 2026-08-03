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

Ranking is not selection. Live ranks, builds a bundle of five named slots from
the ranked list, and walks it in a fixed order taking the first contract that
passes ``evaluate_option_liquidity`` -- see ``_apply_live_pick``, which
reproduces that walk. Everything downstream of the chain is live's own code:
the ranker, the quality scorer, the liquidity gate, the affordability maths.

What this deliberately does *not* reproduce:

* Greeks. Live's chain carries Polygon's own; this reconstructs them from the
  contract's observed price (see ``historical_greeks``). Delta agrees with live
  to ~0.014 on the trades where both exist, inside the ranker's own resolution
  for every term except a contract sitting exactly on the 0.25/0.75 cutoffs.
* Open interest, which has no history to query. See ``ASSUMED_OPEN_INTEREST``.
* The re-quote. Live reads a fresh NBBO before committing; there is one
  historical price per moment, so affordability is checked once (``_priced``).

Each is recorded in the result rather than left as an unexamined assumption, so
a run can report how often the question came up.
"""

import os
from contextlib import contextmanager
from dataclasses import dataclass

import pandas as pd

from app.backtesting.historical_greeks import greeks_for_contract, parse_occ_ticker
from app.backtesting.historical_options import (
    list_contracts_as_of,
    option_bars,
    price_at,
    quote_at,
)
from app.config.settings import settings
from app.options.affordability_config import get_affordability_config
from app.options.contract_ranker import rank_option_contracts
from app.options.option_affordability import add_affordability_metrics
from app.options.option_direction import contract_matches_direction
from app.options.option_metrics import (
    classify_expiration_bucket,
    score_option_quality,
)
from app.options.options_filter import evaluate_option_liquidity
from app.options.options_recommender import _pick_first_by_dte

# The window contracts get priced in. OPTION_MIN_DTE is 5 and the ranker scores
# 14-21 at +40 against 5-6 at -33, so a weekly effectively cannot win -- which
# is the substance of the open QQQ/SPY short-DTE question. Kept as parameters so
# that question can be answered by running it rather than argued.
#
# 30 also truncates the bundle's `longer_dte` slot, which asks for 31-45 DTE:
# live can reach those contracts and this cannot, so that slot is always empty
# here. It is the last slot tried before the ranked list and only matters when
# everything above it is illiquid, but it is a real difference -- `--max-dte 45`
# closes it, at roughly half again the requests per scan.
DEFAULT_MIN_DTE = 5
DEFAULT_MAX_DTE = 30

# Strikes beyond this from spot cannot reach the 0.25-0.75 delta band the
# ranker accepts, so pricing them is wasted work.
DEFAULT_MAX_MONEYNESS_PCT = 12.0

# Open interest has no point-in-time history on this plan, and for an expired
# contract there is nothing left to query. Every OI rule live applies is
# therefore unreproducible, and the choice is which way to fail: at the old
# value of 1 the liquidity gate (`oi < 500`) rejected the entire chain and no
# replay traded at all.
#
# `option_min_open_interest` is the value that makes each OI rule a no-op
# rather than a silent verdict -- it clears the ranker's `oi < 1` gate and the
# liquidity gate, and skips the quality scorer's -20, while landing below the
# ranker's first scoring band (>= 1000) so it adds points to nothing and
# reorders nothing. Raising the setting past 1000 would start awarding every
# contract the same +5, which is still order-preserving.
#
# The gap this leaves is real and one-directional: live rejects a contract
# nobody holds and the replay will not, so a replay can trade an untraded
# strike. What actually catches those is the spread cap -- an untraded strike
# quotes wide -- and `oi_unavailable` in the diagnostics marks every run whose
# selection was made without this gate.
ASSUMED_OPEN_INTEREST = settings.option_min_open_interest


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
    # Live's PAPER_IGNORE_AFFORDABILITY, which defaults on. It decides both the
    # ranker's affordability penalty and which contract fills the bundle's
    # `active` slot -- with it on, that is ranked[0] regardless of cost.
    paper_mode: bool = True
    require_quote: bool = True
    # Whether the account's capital profile constrains the pick. True mirrors
    # OPTION_AFFORDABILITY_MODE=HARD; False answers "what would this strategy
    # do with unlimited capital", which is a different question and should be
    # labelled as such in any result.
    apply_affordability: bool = True


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

        contract = {
            "ticker": ticker,
            "symbol": underlying,
            "strike": spec["strike"],
            # Lowercase: the ranker's direction filter compares against
            # "call"/"put" literals, so an uppercase type silently rejects
            # the entire chain.
            "type": spec["contract_type"].lower(),
            "expiration_date": spec["expiry"].isoformat(),
            "dte": dte,
            # Live's own classifier, not a local copy. A previous local version
            # returned WEEKLY/SHORT_TERM/LONG_TERM, which are not labels any
            # live code emits, so the ranker's bucket block matched none of
            # them and silently withheld the +12 a 14-30 DTE contract earns and
            # the -8 a 7-13 DTE one is docked -- a 20 point swing on the term
            # that decides expiry, which is exactly where parity was failing.
            "expiration_bucket": classify_expiration_bucket(dte),
            "mid_price": quote["mid"] if quote else mark,
            "bid": quote["bid"] if quote else None,
            "ask": quote["ask"] if quote else None,
            "spread_pct": quote["spread_pct"] if quote else None,
            "delta": greeks["delta"],
            "gamma": greeks["gamma"],
            "theta": greeks["theta"],
            "iv": greeks["iv"] * 100.0,
            "volume": volume,
            "open_interest": ASSUMED_OPEN_INTEREST,
            # The historical NBBO is by construction the quote in force at the
            # decision moment, so live's freshness gate is satisfied rather
            # than faked. Nothing here reproduces a genuinely stale live feed;
            # `no_quote` counts the contracts that had no quote at all.
            "quote_freshness": "LIVE_QUOTE",
            "quote_status": "OK",
            "quote_timeframe": "REALTIME",
        }

        # Live scores quality inside the chain fetch, and both the ranker
        # (score += quality/10) and the liquidity gate (>= 65) read it. Leaving
        # it at 0, as this did, put every contract 6.5 points low into ranking
        # and then failed all of them at the gate.
        contract.update(
            score_option_quality(
                contract,
                min_volume=settings.option_min_volume,
                min_open_interest=settings.option_min_open_interest,
                max_spread_pct=settings.option_max_spread_pct,
                allow_0dte=settings.option_allow_0dte,
                allow_1dte=settings.option_allow_1dte,
                min_dte=settings.option_min_dte,
                preferred_min_dte=settings.option_preferred_min_dte,
                preferred_max_dte=settings.option_preferred_max_dte,
                max_dte=settings.option_max_dte,
            )
        )

        chain.append(contract)

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

    diagnostics = {
        "chain_size": len(chain),
        "skipped": skipped,
        # Selection ran without live's open interest gate. See
        # ASSUMED_OPEN_INTEREST -- any result built from these picks carries the
        # assumption, so it is stated per selection rather than in a comment.
        "oi_unavailable": True,
    }

    if not chain:

        return None, None, diagnostics

    direction = str(direction).upper()

    with _affordability_mode(config) as affordability:

        ranked = rank_option_contracts(
            chain,
            underlying_price=spot,
            direction=direction,
            paper_mode=config.paper_mode,
        )

        if not ranked:

            diagnostics["rejected_by_ranker"] = len(chain)

            return None, None, diagnostics

        diagnostics["ranked_size"] = len(ranked)

        best = _apply_live_pick(
            ranked, config, direction, affordability, diagnostics
        )

    if best is None:

        return None, None, diagnostics

    diagnostics["ranking_score"] = best.get("ranking_score")

    return best.get("ticker"), best, diagnostics


# The order ``_iter_option_bundle_candidates`` in ``app/main.py`` yields. Live
# tries these slots in this sequence and takes the first that is liquid, so the
# sequence is part of the selection rule, not a presentation detail.
# ``tests/test_replay_bundle_parity.py`` asserts this still matches main.py.
BUNDLE_ORDER = [
    "active",
    "primary",
    "affordable",
    "short_dte",
    "longer_dte",
]

# ``recommend_live_option_bundle``'s alternate windows, which are not the
# ranker's preferred window and not configurable live.
SHORT_DTE_WINDOW = (2, 13)
LONGER_DTE_WINDOW = (31, 45)


@contextmanager
def _affordability_mode(config):
    """Make ``apply_affordability=False`` reach every consumer.

    ``rank_option_contracts`` and ``evaluate_option_liquidity`` each call
    ``get_affordability_config()`` themselves, so the flag cannot be honoured by
    passing a modified dict down -- the ranker's -1000 penalty and the liquidity
    gate's HARD check would both still fire off the real config, and a run
    labelled "unlimited capital" would quietly still be capital-constrained.
    Overriding the env is what the whole call tree actually reads. Scoped to the
    selection so no other part of the process sees it.
    """

    if config.apply_affordability:

        yield get_affordability_config()

        return

    previous = os.environ.get("OPTION_AFFORDABILITY_MODE")
    os.environ["OPTION_AFFORDABILITY_MODE"] = "OFF"

    try:

        yield get_affordability_config()

    finally:

        if previous is None:

            os.environ.pop("OPTION_AFFORDABILITY_MODE", None)

        else:

            os.environ["OPTION_AFFORDABILITY_MODE"] = previous


def _priced(contract, affordability):
    """Live's per-candidate preparation, minus the step history cannot do.

    Live calls ``add_affordability_metrics(refresh_contract_quote(contract))``.
    The re-quote has no historical equivalent and is skipped: the price here is
    already the one in force at the decision moment, where live's is a few
    hundred milliseconds fresher. The affordability computation is pure and is
    applied unchanged.
    """

    return add_affordability_metrics(dict(contract), config=affordability)


def _pick_best_affordable_historical(ranked, affordability):
    """``_pick_best_affordable`` without the re-quote round trip.

    Live checks affordability, re-quotes, then checks again, so a contract that
    moved past the cap between the two reads is dropped. With one historical
    price there is only one check to make.
    """

    if affordability.get("mode") == "OFF":

        return None

    for contract in ranked[:50]:

        candidate = _priced(contract, affordability)

        if candidate.get("affordable"):

            return candidate

    return None


def _build_bundle(ranked, config, affordability):
    """Rebuild ``recommend_live_option_bundle``'s post-ranking half."""

    primary = _priced(ranked[0], affordability)
    affordable = _pick_best_affordable_historical(ranked, affordability)

    active = (
        primary
        if config.paper_mode
        else affordable
        if (affordability.get("mode") != "OFF" and affordable)
        else primary
    )

    primary_ticker = primary.get("ticker")

    short_dte = _pick_first_by_dte(
        ranked, *SHORT_DTE_WINDOW, exclude_ticker=primary_ticker
    )
    longer_dte = _pick_first_by_dte(
        ranked, *LONGER_DTE_WINDOW, exclude_ticker=primary_ticker
    )

    return {
        "primary": primary,
        "active": active,
        "affordable": affordable,
        "short_dte": _priced(short_dte, affordability) if short_dte else None,
        "longer_dte": _priced(longer_dte, affordability) if longer_dte else None,
        "ranked": ranked,
    }


def _iter_bundle_candidates(bundle):
    """``_iter_option_bundle_candidates`` from ``app/main.py``, same order."""

    for label in BUNDLE_ORDER:

        yield label, bundle.get(label)

    for index, contract in enumerate(bundle.get("ranked") or [], start=1):

        yield f"ranked #{index}", contract


def _apply_live_pick(ranked, config, direction, affordability, diagnostics):
    """Reproduce ``_select_liquid_option_from_bundle``.

    Ranking alone is not selection, and neither is affordability. Live builds a
    bundle of five named slots plus the ranked list, walks them in a fixed
    order, and takes the first contract that both matches the setup's direction
    and passes ``evaluate_option_liquidity`` -- spread, volume, open interest,
    quality score, the 0/1DTE bans, and, under
    ``OPTION_AFFORDABILITY_MODE=HARD``, affordability. Affordability enters
    only through that gate and through the ``affordable`` slot; it is not a
    filter applied to every candidate, and with ``paper_mode`` on (live's
    default, ``PAPER_IGNORE_AFFORDABILITY``) the first contract tried is
    ranked[0] whether or not the account can buy it.

    What this previously did instead -- take the best-ranked contract that was
    both affordable and inside the 7-21 DTE window -- is not a rule live has.
    The DTE window is the *ranker's* preference, already priced into the score;
    re-applying it as a filter discarded contracts live would have bought, and
    skipping the liquidity walk kept contracts live would have rejected.
    """

    bundle = _build_bundle(ranked, config, affordability)

    attempts = []
    seen = set()

    for source, contract in _iter_bundle_candidates(bundle):

        if not contract:

            continue

        ticker = contract.get("ticker")
        dedupe_key = ticker or id(contract)

        if dedupe_key in seen:

            continue

        seen.add(dedupe_key)

        candidate = _priced(contract, affordability)

        if not contract_matches_direction(candidate, direction):

            attempts.append(
                {
                    "source": source,
                    "ticker": ticker,
                    "liquid": False,
                    "code": "DIRECTION_MISMATCH",
                }
            )
            continue

        liquidity = evaluate_option_liquidity(candidate)

        attempts.append(
            {
                "source": source,
                "ticker": ticker,
                "liquid": liquidity.get("liquid"),
                "code": liquidity.get("code"),
            }
        )

        if liquidity.get("liquid"):

            diagnostics["selected_from"] = source
            diagnostics["liquidity_attempts"] = attempts

            return candidate

    diagnostics["liquidity_attempts"] = attempts
    diagnostics["no_liquid_contract"] = True

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

        ticker, contract, _ = select_contract(
            symbol, direction, moment, spot, config
        )

        # The contract travels with the ticker because the holding profile is
        # decided from it: option quality and the expiration bucket are two of
        # the four conditions live requires for MULTIDAY, and neither can be
        # recovered from an OCC symbol alone.
        return ticker, contract

    return _select
