"""What does moving the breakeven trigger actually cost, and buy?

    python tools/breakeven_arming_ab.py
    python tools/breakeven_arming_ab.py --days 30

Eight trades over 2026-08-17/18 lost every time while six of them held a real,
after-spread profit at their peak. Nothing protected any of them:

    breakeven move   needs 1.0R judged on the *scan* price -- best was 0.75R
    option floors    arm at +10% -- peaks ran 0.8% to 5.3%

Every protective threshold sits above the range these trades occupy. This prices
moving them down.

## The cost this exists to measure

Arming earlier cuts trades at breakeven that would have recovered. That is the
objection in ``exit_engine._giveback_floor`` and it is a real one: a 0.25R
trigger was tried once and lost -- 222 trades against 191, worse total -- because
closing early frees the symbol to re-enter. So every arm reports **cut at
breakeven then recovered**, not just its total.

## Method

Each closed trade is walked forward over its own archived 5-minute price series
**to the end of that session**, not to its real exit. A trade cut early needs a
future to be judged against.

    arm A   the shipped config: trigger 1.0R, judged on the scan price
    arm B   a candidate: trigger T, optionally judged on the running peak

Both arms use the trade's own recorded entry, stop and direction. R is on the
underlying; the cash column converts through the recorded delta and subtracts the
entry spread **once**, which is the full round trip -- ``spread_pct`` is
(ask - bid) / ask, per ``app/risk/stop_viability.py:112``. Doubling it, as an
earlier analysis did, makes every trade look unprofitable at its peak and argues
against the very change this tool exists to test.

## What it cannot see

Intrabar highs and lows: the series is scan prints, so a level touched and
reversed between scans is invisible to both arms equally. ``mfe_r`` is the
recorded intrabar peak and is printed beside the scan peak so the gap between
them is visible rather than assumed away.

Re-entry. This replays one trade per slot, so it cannot reproduce the effect that
killed the 0.25R arm live. Every gain here is therefore an upper bound.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import statistics
import sys

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env", override=True)

from sqlalchemy import text  # noqa: E402

from app.db.connection import get_engine  # noqa: E402

BOOTSTRAP_SEED = 20260818
BOOTSTRAP_DRAWS = 10000


def _f(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result


def _load(days):
    with get_engine().connect() as connection:
        trades = connection.execute(
            text(
                """
                SELECT id, symbol, direction, entry_price, option_entry_mid,
                       r_multiple, payload,
                       opened_at AT TIME ZONE 'America/New_York' AS opened_et
                FROM paper_trades
                WHERE status = 'CLOSED'
                  AND opened_at > now() - make_interval(days => :days)
                ORDER BY opened_at
                """
            ),
            {"days": days},
        ).mappings().all()

        loaded = []

        for trade in trades:
            payload = trade["payload"]

            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    continue

            entry = _f(trade["entry_price"])
            stop = _f(payload.get("initial_stop_loss"))
            delta = _f(payload.get("option_delta"))
            spread = _f(payload.get("option_entry_spread_pct"))

            if spread is None:
                spread = _f(payload.get("option_spread_pct"))

            if None in (entry, stop) or entry == stop:
                continue

            series = connection.execute(
                text(
                    """
                    SELECT payload,
                           created_at AT TIME ZONE 'America/New_York' AS et
                    FROM scanner_snapshot
                    WHERE symbol = :symbol
                      AND (created_at AT TIME ZONE 'America/New_York')::date = :day
                    ORDER BY created_at
                    """
                ),
                {"symbol": trade["symbol"], "day": trade["opened_et"].date()},
            ).mappings().all()

            prices = []

            for row in series:
                if row["et"] < trade["opened_et"]:
                    continue

                snap = row["payload"]

                if isinstance(snap, str):
                    try:
                        snap = json.loads(snap)
                    except ValueError:
                        continue

                price = _f(snap.get("Price"))

                if price is not None:
                    prices.append(price)

            if len(prices) < 2:
                continue

            loaded.append({
                "id": trade["id"],
                "symbol": trade["symbol"],
                "short": trade["direction"] == "PUT",
                "entry": entry,
                "stop": stop,
                "risk": abs(entry - stop),
                "mid": _f(trade["option_entry_mid"]),
                "delta": abs(delta) if delta else None,
                "spread": spread,
                "mfe_r": _f(payload.get("mfe_r")),
                "actual_r": _f(trade["r_multiple"]),
                "prices": prices,
            })

    return loaded


def _simulate(trade, trigger_r, on_peak, option_arm_pct=None):
    """Walk one trade to the end of its session under one configuration."""

    entry, stop, risk = trade["entry"], trade["stop"], trade["risk"]
    current_stop = stop
    armed = False
    peak_r = 0.0

    def progress(price):
        return (entry - price) / risk if trade["short"] else (price - entry) / risk

    for index, price in enumerate(trade["prices"]):
        prog = progress(price)
        peak_r = max(peak_r, prog)

        judged = peak_r if on_peak else prog

        # The option floor arms on premium, not on R. Converted through the
        # recorded delta because the archive carries no option price series.
        option_armed = False

        if option_arm_pct is not None and trade["delta"] and trade["mid"]:
            option_gain = judged * risk * trade["delta"] / trade["mid"] * 100
            option_armed = option_gain >= option_arm_pct

        if not armed and (judged >= trigger_r or option_armed):
            armed = True
            current_stop = entry

        hit = price >= current_stop if trade["short"] else price <= current_stop

        if hit:
            realised = progress(price)
            # Everything after this exit, so a trade cut at breakeven can be
            # asked whether it would have come back.
            rest = trade["prices"][index + 1:]
            best_after = max((progress(p) for p in rest), default=None)
            return {
                "r": realised,
                "armed": armed,
                "peak_r": peak_r,
                "cut_early": armed and abs(realised) < 0.05,
                "best_after": best_after,
            }

    final = progress(trade["prices"][-1])
    return {
        "r": final,
        "armed": armed,
        "peak_r": peak_r,
        "cut_early": False,
        "best_after": None,
    }


def _cash(trade, r):
    """R converted to premium, net of one round trip. None when unpriceable."""

    if not (trade["delta"] and trade["mid"] and trade["spread"] is not None):
        return None

    gross_pct = r * trade["risk"] * trade["delta"] / trade["mid"] * 100
    return (gross_pct - trade["spread"]) / 100 * trade["mid"] * 100


def _bootstrap(values):
    if len(values) < 2:
        return None, None

    rng = random.Random(BOOTSTRAP_SEED)
    means = []

    for _ in range(BOOTSTRAP_DRAWS):
        sample = [values[rng.randrange(len(values))] for _ in values]
        means.append(statistics.mean(sample))

    means.sort()
    return means[int(0.025 * BOOTSTRAP_DRAWS)], means[int(0.975 * BOOTSTRAP_DRAWS)]


def _report(name, trades, results, baseline=None):
    rs = [r["r"] for r in results]
    cash = [c for c in (_cash(t, r["r"]) for t, r in zip(trades, results))
            if c is not None]
    armed = sum(1 for r in results if r["armed"])

    # The objection: cut at breakeven, then the move came back without you.
    regret = [
        r for r in results
        if r["cut_early"] and r["best_after"] is not None and r["best_after"] >= 1.0
    ]

    line = (f"  {name:36} total {sum(rs):+7.2f}R  mean {statistics.mean(rs):+6.3f}R"
            f"  armed {armed:>2}/{len(rs)}")

    if cash:
        line += f"  ${sum(cash):+7.0f}"

    print(line)
    print(f"  {'':36} cut at breakeven then recovered >=1R: {len(regret)}")

    if baseline is not None:
        deltas = [b - a for a, b in zip(baseline, rs)]
        low, high = _bootstrap(deltas)
        note = f"  {'':36} vs shipped {statistics.mean(deltas):+.3f}R/trade"

        if low is not None:
            note += f"  95% CI [{low:+.3f}, {high:+.3f}]"

        print(note)

    return rs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    trades = _load(args.days)
    print(f"closed trades replayable from the archive: {len(trades)}\n")

    if not trades:
        return

    scan_peaks = [max(0.0, _simulate(t, 99, False)["peak_r"]) for t in trades]
    mfes = [t["mfe_r"] for t in trades if t["mfe_r"] is not None]

    print(f"peak reached AT A SCAN  median {statistics.median(scan_peaks):.2f}R"
          f"   >=1.0R on {sum(1 for p in scan_peaks if p >= 1.0)}/{len(scan_peaks)}")

    if mfes:
        print(f"peak reached INTRABAR   median {statistics.median(mfes):.2f}R"
              f"   >=1.0R on {sum(1 for m in mfes if m >= 1.0)}/{len(mfes)}")

    print("\nSHIPPED CONFIG")
    base_results = [_simulate(t, 1.0, False) for t in trades]
    baseline = _report("trigger 1.0R, scan price", trades, base_results)

    print("\nCANDIDATES")

    for trigger, on_peak, opt in (
        (1.0, True, None),
        (0.75, True, None),
        (0.5, False, None),
        (0.5, True, None),
        (0.25, True, None),
        (0.5, True, 3.0),
    ):
        label = f"trigger {trigger}R, {'peak' if on_peak else 'scan'}"

        if opt:
            label += f", option floor {opt:.0f}%"

        _report(label, trades,
                [_simulate(t, trigger, on_peak, opt) for t in trades], baseline)

    print("\nREAD THIS BEFORE ACTING")
    print("  A 0.25R trigger was already tried live and LOST: 222 trades against")
    print("  191 and a worse total, because closing early frees the symbol to")
    print("  re-enter. That effect is NOT modelled here -- one trade per slot. So")
    print("  treat every gain below as an upper bound, and prefer the arm whose")
    print("  interval excludes zero over the arm with the biggest total.")


if __name__ == "__main__":
    main()
