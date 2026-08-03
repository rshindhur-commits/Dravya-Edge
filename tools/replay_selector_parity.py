"""Measure how often the replay's contract selector picks what live picked.

The number to move for step 1b. Currently 4/9; the five misses are all expiry
choice, because live does not select from the ranked list at all --
``recommend_live_option_bundle`` returns primary/active/affordable/short_dte/
longer_dte and ``app/main.py`` picks among them with
``_select_liquid_option_from_bundle``, by liquidity.

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

        config.preferred_min_dte = args.min_dte

    if args.max_dte is not None:

        config.preferred_max_dte = args.max_dte

    with open(FIXTURE) as handle:

        trades = json.load(handle)

    print(
        f"{'sym':6}{'scan':8}{'dir':5}{'live':24}{'replay':24}"
        f"{'ok':>4}{'chain':>6}{'score':>8}"
    )

    matched = 0
    expiry_only = 0

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

        print(
            f"{trade['symbol']:6}{trade['scan_id'][-6:]:8}{trade['direction']:5}"
            f"{live_ticker:24}{str(ticker):24}"
            f"{('YES' if hit else 'no'):>4}"
            f"{diagnostics.get('chain_size', 0):>6}"
            f"{(diagnostics.get('ranking_score') or 0):8.1f}"
        )

    print(f"\ncontract parity: {matched}/{len(trades)}")

    if expiry_only:

        print(f"  of the misses, {expiry_only} differ only in expiry")

    print(
        "\nreference: 2/9 before the prefilter widened to 72, 4/9 after. "
        "The open work is reproducing _select_liquid_option_from_bundle."
    )


if __name__ == "__main__":

    main()
