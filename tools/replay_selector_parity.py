"""Measure how often the replay's contract selector picks what live picked.

The number for step 1b. The replay now reproduces the selection live actually
performs: ``recommend_live_option_bundle`` returns primary/active/affordable/
short_dte/longer_dte, and ``_select_liquid_option_from_bundle`` in
``app/main.py`` walks those slots and then the ranked list, taking the first
contract that passes ``evaluate_option_liquidity``. The `from` column reports
which slot won, so a miss can be attributed to ranking or to liquidity rather
than guessed at.

Parity stayed at 4/9 across that change, which localises the remaining gap
rather than closing it. What the misses are *not*: live's pick is present and
priced in the replay's chain every time, so the prefilter is not dropping it,
and the reconstructed delta agrees with live's own recorded value to ~0.01 on
these same contracts, so the Greeks are not moving it either.

**Measured 2026-08-03. Affordability is the term, not ranked order.** Three
candidate causes were tested and the first two are eliminated:

* **Chain width -- not the cause.** ``--max-priced 250`` grows the priced chain
  from 72 to 109-121 contracts and every pick, score and rank comes out
  byte-identical. Mechanically that had to be so: ``rank_option_contracts``
  scores each contract on absolute terms -- volume and OI bands, strike
  distance, delta target, DTE bucket -- with nothing chain-relative, so adding
  contracts cannot reorder two that were already priced. This was previously
  recorded here as "the first thing to test next"; it is now answered.
* **The spread cap -- not the cause.** ``option_max_spread_pct`` was tightened
  from 10 to 6 in 9d7ef0c at 2026-07-30 19:24, *between* the fixture's two
  days, so ORCL 142304 (8.00%) and CRWD (10.07%) were bought by a live that
  allowed 10. Restoring it with ``OPTION_MAX_SPREAD_PCT=10`` leaves parity at
  4/9. It changes only how CRWD is reached -- ``ranked #34`` becomes
  ``affordable`` -- so the fixture still straddles two configurations and any
  claim about those two trades must say so, but it is not what costs the hits.
* **Affordability -- the dominant term.** ``--no-affordability`` takes parity to
  **0/9** and changes every pick, which is the point: the unconstrained ranker
  moves *towards* live, not away. It picks 260814 and 260821 where the
  constrained run picks 260807, so seven of the nine then agree with live on
  expiry and only the strike differs. NVDA 142304 goes to 260814P00195000
  against live's 260814P00190000 -- same expiry, adjacent strike.

So the replay's ranked order does not disagree with live's about expiry. Under
the account's cost cap the ranked-best contract is unaffordable, the bundle walk
falls past ``active`` to ``affordable``, and what it lands on is shorter-dated
and further out of the money. That is why every miss reads as an expiry miss.

**What to test next**, in order: whether live's own bundle walk rejected its
``active`` slot the same way on these trades -- ``evaluate_option_liquidity``
rejects on ``OPTION_TOO_EXPENSIVE`` under ``OPTION_AFFORDABILITY_MODE=HARD``,
and if live took ``active`` where the replay cannot, the gap is the affordability
inputs (capital, ``OPTION_MAX_CONTRACT_COST``) and not selection at all. The
fixture's live trades carry the contract cost they paid, so this is answerable
from the fixture without another Polygon run.

So the fixture cannot currently reach 9/9, and chasing the number by tuning
the selector would be fitting to a target measured under different settings.

Run before and after any change to ``app/backtesting/contract_selector.py``:

    python tools/replay_selector_parity.py
    python tools/replay_selector_parity.py --no-affordability
    python tools/replay_selector_parity.py --max-priced 24

Needs POLYGON_API_KEY and hits the network -- roughly 150 requests per trade
with the default prefilter, so expect several minutes. Underlying bars come
from the committed fixture cache; option bars and quotes do not, and are cached
to ``data/backtest_cache`` after the first run.

Reads the frozen fixtures, not the database, so it stays runnable when the
archive or DB is unavailable.
"""

import argparse
import json
import pathlib
import sys
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

import app.backtesting.historical_market_data as hmd
from app.backtesting.contract_selector import SelectionConfig, select_contract
from app.backtesting.historical_greeks import parse_occ_ticker

REPO = pathlib.Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "live_trades_2026_07_30_31.json"
FIXTURE_CACHE = REPO / "tests" / "fixtures" / "market_cache"


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-priced", type=int, default=None)
    parser.add_argument("--no-affordability", action="store_true")
    parser.add_argument("--min-dte", type=int, default=None)
    parser.add_argument("--max-dte", type=int, default=None)
    parser.add_argument(
        "--fixture-cache",
        action="store_true",
        help="serve underlying bars from tests/fixtures/market_cache",
    )
    args = parser.parse_args()

    if args.fixture_cache:

        hmd._CACHE_ROOT = FIXTURE_CACHE

    config = SelectionConfig()

    if args.max_priced is not None:

        config.max_priced_contracts = args.max_priced

    if args.no_affordability:

        config.apply_affordability = False

    if args.min_dte is not None:

        config.min_dte = args.min_dte

    if args.max_dte is not None:

        config.max_dte = args.max_dte

    with open(FIXTURE) as handle:

        trades = json.load(handle)

    print(
        f"{'sym':6}{'scan':8}{'dir':5}{'live':24}{'replay':24}"
        f"{'ok':>4}{'chain':>6}{'score':>8}  {'from':<12}{'tried':>6}"
    )

    matched = 0
    expiry_only = 0
    slots = {}

    for trade in trades:

        moment = datetime.strptime(trade["scan_id"], "%Y-%m-%d_%H%M%S")
        spot = float(trade["entry_price"])

        ticker, best, diagnostics = select_contract(
            trade["symbol"], trade["direction"], moment, spot, config
        )

        live_ticker = trade["option_ticker"]
        hit = ticker == live_ticker
        matched += hit

        if ticker and not hit:

            live_spec = parse_occ_ticker(live_ticker)
            replay_spec = parse_occ_ticker(ticker)

            if live_spec["strike"] == replay_spec["strike"]:

                expiry_only += 1

        # Which bundle slot the contract came from, and how many candidates the
        # liquidity walk rejected before it. A pick from `active` on the first
        # try means the ranker agreed with live outright; anything deeper means
        # liquidity, not ranking, decided the trade.
        selected_from = diagnostics.get("selected_from") or "-"
        tried = len(diagnostics.get("liquidity_attempts") or [])
        slots[selected_from] = slots.get(selected_from, 0) + 1

        print(
            f"{trade['symbol']:6}{trade['scan_id'][-6:]:8}{trade['direction']:5}"
            f"{live_ticker:24}{str(ticker):24}"
            f"{('YES' if hit else 'no'):>4}"
            f"{diagnostics.get('chain_size', 0):>6}"
            f"{(diagnostics.get('ranking_score') or 0):8.1f}"
            f"  {selected_from:<12}{tried:>6}"
        )

    print(f"\ncontract parity: {matched}/{len(trades)}")

    if expiry_only:

        print(f"  of the misses, {expiry_only} differ only in expiry")

    print(
        "  picked from: "
        + ", ".join(
            f"{slot} x{count}"
            for slot, count in sorted(
                slots.items(), key=lambda item: -item[1]
            )
        )
    )

    print(
        "\nreference: 2/9 with a 24-contract prefilter, 4/9 at 72, and 4/9 "
        "again once the bundle walk replaced the rank-and-affordability pick. "
        "The remaining five misses are not selection logic -- see the module "
        "docstring."
    )


if __name__ == "__main__":

    main()
