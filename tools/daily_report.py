"""The post-close read on a trading day, from the live archive.

This is the monitoring half of the daily loop, and it is deliberately not the
tuning half. A 14-trade day carries a standard error of about 0.20R on its mean,
so across the 21-day economics run every single day landed inside the band that
is consistent with nothing having changed, and 55% of consecutive day-pairs
flipped sign -- a coin toss. Detecting a genuine +0.10R improvement needs ~905
trades, roughly 65 sessions. A day therefore cannot tell you whether a rule is
better; it can only tell you whether something *broke*, and breakage is large
enough to see immediately.

So this report answers "did anything change that I should look at", and it
prints the noise band beside every headline number so a good Tuesday cannot be
mistaken for progress. Rule changes belong to a separate, slower loop: batch ten
or more days, move one variable, re-run the window, compare.

Three counting rules are baked in, each of which was got wrong once on real
data:

* **Symbols, not rows.** 2,990 rows is 26 symbols across 115 scans; a symbol
  blocked all day counts 115 times and looks like a catastrophe. Both are shown,
  and the symbol column is the honest one.
* **Per attempt, not per row.** `Option Rejection Reason` records only the reason
  the *last* candidate failed. On 2026-08-03 it reported "low open interest, 162"
  while 209 of 242 rows had hit OPTION_TOO_EXPENSIVE somewhere in the walk.
* **Cash beside R.** R is measured against the underlying's stop and never sees
  the option spread crossed twice. Over 291 trades the book was +0.01R and
  -3.1% in premium, with 59 trades green in R and red in cash.

    python tools/daily_report.py
    python tools/daily_report.py --day 2026-08-03
    python tools/daily_report.py --day 2026-08-03 --baseline 15

Reads the database only. Safe to run while the market is open, though the funnel
will be partial until the close.
"""

import argparse
import collections
import json
import math
import pathlib
import statistics
import sys
from datetime import date, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.db.connection import get_engine

ACTIONABLE = {
    "ACTIONABLE", "TRIGGER_MET", "READY", "ENTER", "ENTER_NOW",
    "ENTER_PAPER", "ENTER_REAL",
}
GATE_PASS = {"PASS", "TRUE", "ELIGIBLE"}
EMPTY = {"", "None", "-", "nan"}


# --------------------------------------------------------------------- helpers


def number(value, default=None):

    try:

        if value is None or str(value).strip().lower() in {"", "nan", "none"}:

            return default

        return float(value)

    except (TypeError, ValueError):

        return default


def as_dict(value):

    if isinstance(value, str):

        try:

            value = json.loads(value)

        except json.JSONDecodeError:

            return {}

    return value if isinstance(value, dict) else {}


def as_list(value):

    if isinstance(value, str):

        try:

            value = json.loads(value)

        except json.JSONDecodeError:

            return []

    return value if isinstance(value, list) else []


def priced(payload):
    """Did option selection actually return a quoted contract for this row?"""

    return str(payload.get("Option Quote Freshness") or "").strip() not in EMPTY


def reached_selection(payload):

    return priced(payload) or bool(payload.get("Option Rejection Reason"))


def option_return(trade):
    """Premium return, net of the spread, as a percentage of what was paid."""

    payload = as_dict(trade.get("payload"))

    for field in ("option_pnl_pct_net", "option_pnl_pct", "option_pl_pct"):

        value = number(payload.get(field))

        if value is not None:

            return value

    entry = number(trade.get("option_entry_mid"))
    close = number(trade.get("option_close_mid"))

    if entry and close is not None and entry > 0:

        return (close - entry) / entry * 100.0

    return None


# ------------------------------------------------------------------ collection


def load_day(connection, day):

    rows = [
        row["decision_payload"] or {}
        for row in connection.execute(text("""
            SELECT decision_payload FROM scanner_snapshot
            WHERE trading_day = CAST(:day AS DATE)
            ORDER BY scan_id, symbol
        """), {"day": str(day)}).mappings()
    ]

    trades = [
        dict(row)
        for row in connection.execute(text("""
            SELECT symbol, direction, option_ticker, status, holding_profile,
                   entry_price, option_entry_mid, option_close_mid,
                   r_multiple, payload, opened_at, closed_at
            FROM paper_trades
            WHERE DATE(opened_at) = CAST(:day AS DATE)
            ORDER BY opened_at
        """), {"day": str(day)}).mappings()
    ]

    # The ledger says why each trade was admitted, which is the only exact
    # record of the paper trader and the alert layer disagreeing. Inferring it
    # from the snapshot does not work: a symbol can pass the gate on one scan
    # and be taken off a different, failing one.
    ledger = [
        dict(row)
        for row in connection.execute(text("""
            SELECT symbol, decision, reason, scan_timestamp
            FROM auto_paper_decision
            WHERE DATE(scan_timestamp) = CAST(:day AS DATE)
            ORDER BY id
        """), {"day": str(day)}).mappings()
    ]

    return rows, trades, ledger


def day_metrics(rows, trades):
    """The handful of numbers worth trending. Everything else is detail."""

    symbols = {str(row.get("Symbol")) for row in rows}
    selected = {str(row.get("Symbol")) for row in rows if reached_selection(row)}
    got = {str(row.get("Symbol")) for row in rows if priced(row)}
    passed = [
        row for row in rows
        if str(row.get("ENTRY_GATE_RESULT") or "").upper() in GATE_PASS
    ]

    r_values = [
        v for v in (number(t.get("r_multiple")) for t in trades)
        if v is not None
    ]
    returns = [v for v in (option_return(t) for t in trades) if v is not None]

    return {
        "rows": len(rows),
        "symbols": len(symbols),
        "symbols_selected": len(selected),
        "symbols_priced": len(got),
        "rows_priced": sum(1 for row in rows if priced(row)),
        "rows_gate_pass": len(passed),
        "trades": len(trades),
        "mean_r": statistics.mean(r_values) if r_values else None,
        "total_r": sum(r_values) if r_values else None,
        "mean_return": statistics.mean(returns) if returns else None,
        "r_win": (
            sum(1 for v in r_values if v > 0) / len(r_values)
            if r_values else None
        ),
        "cash_win": (
            sum(1 for v in returns if v > 0) / len(returns)
            if returns else None
        ),
    }


def baseline_days(connection, day, count):
    """The trading days actually present in the archive before `day`."""

    return [
        row[0] for row in connection.execute(text("""
            SELECT DISTINCT trading_day FROM scanner_snapshot
            WHERE trading_day < CAST(:day AS DATE)
            ORDER BY trading_day DESC LIMIT :count
        """), {"day": str(day), "count": count})
    ]


# -------------------------------------------------------------------- sections


def band(values):
    """Mean and 2sd of a trailing series, or None when too thin to mean anything."""

    values = [v for v in values if v is not None]

    if len(values) < 3:

        return None

    mean = statistics.mean(values)
    spread = statistics.stdev(values) if len(values) > 1 else 0.0

    return mean, mean - 2 * spread, mean + 2 * spread


def show_headline(today, history):

    print("== headline, against the trailing band ==")
    print(f"   {'metric':22}{'today':>10}{'trail mean':>13}"
          f"{'band (2sd)':>22}  flag")

    # `counted` fields cannot go below zero, and a band that says "-3 .. 7"
    # for a count reads as though the metric could be negative.
    fields = [
        ("symbols priced", "symbols_priced", "{:.0f}", True),
        ("rows gate-passed", "rows_gate_pass", "{:.0f}", True),
        ("trades", "trades", "{:.0f}", True),
        ("mean R", "mean_r", "{:+.2f}", False),
        ("mean premium %", "mean_return", "{:+.1f}", False),
        ("R win rate", "r_win", "{:.0%}", True),
        ("cash win rate", "cash_win", "{:.0%}", True),
    ]

    for label, key, fmt, counted in fields:

        value = today.get(key)
        limits = band([day.get(key) for day in history])

        if value is None:

            # A day can have trades and still have no mean R, when nothing has
            # closed yet. Saying "no trades" under a non-zero trade count is
            # the kind of small lie that gets believed.
            missing = (
                "(no trades)" if not today.get("trades")
                else "(none scored)"
            )
            print(f"   {label:22}{'-':>10}{missing:>15}")
            continue

        if limits is None:

            print(f"   {label:22}{fmt.format(value):>10}{'-':>13}"
                  f"{'too few sessions':>22}")
            continue

        mean, low, high = limits

        if counted:

            low = max(low, 0.0)

        outside = value < low or value > high

        print(
            f"   {label:22}{fmt.format(value):>10}{fmt.format(mean):>13}"
            f"{(fmt.format(low) + ' .. ' + fmt.format(high)):>22}"
            f"  {'<-- OUTSIDE' if outside else ''}"
        )


def show_funnel(rows):

    print("\n== funnel ==")
    print(f"   {'stage':36}{'rows':>8}{'symbols':>9}")

    stages = [
        ("in the watchlist", lambda row: True),
        ("reached option selection", reached_selection),
        ("got a priced contract", priced),
        ("passed the entry gate",
         lambda row: str(row.get("ENTRY_GATE_RESULT") or "").upper() in GATE_PASS),
    ]

    for label, predicate in stages:

        kept = [row for row in rows if predicate(row)]
        print(f"   {label:36}{len(kept):>8}"
              f"{len({str(r.get('Symbol')) for r in kept}):>9}")

    shut_out = sorted(
        {str(r.get("Symbol")) for r in rows if reached_selection(r)}
        - {str(r.get("Symbol")) for r in rows if priced(r)}
    )

    if shut_out:

        print(f"\n   reached selection, never priced ({len(shut_out)}): "
              f"{', '.join(shut_out)}")


def show_blockers(rows):

    print("\n== where candidates stopped ==")
    counts = collections.Counter(
        str(row.get("Blocked By")) for row in rows if row.get("Blocked By")
    )
    symbols = collections.defaultdict(set)

    for row in rows:

        if row.get("Blocked By"):

            symbols[str(row["Blocked By"])].add(str(row.get("Symbol")))

    print(f"   {'reason':36}{'rows':>8}{'symbols':>9}")

    for reason, count in counts.most_common(12):

        print(f"   {reason[:34]:36}{count:>8}{len(symbols[reason]):>9}")


def show_rejections(rows):
    """Per attempt, because the row-level reason is only the last failure."""

    per_attempt = collections.Counter()
    rows_hit = collections.Counter()
    symbols_hit = collections.defaultdict(set)
    evidence = 0
    total = 0

    for row in rows:

        symbol = str(row.get("Symbol"))
        codes = []

        for attempt in as_list(row.get("Option Liquidity Attempts")):

            total += 1
            codes.append(str(attempt.get("code") or "?"))

            if attempt.get("open_interest") is not None:

                evidence += 1

        per_attempt.update(codes)

        for code in set(codes):

            rows_hit[code] += 1
            symbols_hit[code].add(symbol)

    if not total:

        return

    accepted = per_attempt.pop("LIQUID", 0)

    print(f"\n== contract selection: {total} attempts, {accepted} accepted "
          f"({evidence / total:.0%} carrying evidence) ==")
    print(f"   {'refused for':34}{'attempts':>10}{'rows':>7}{'symbols':>9}")

    for code, count in per_attempt.most_common(10):

        print(f"   {code[:32]:34}{count:>10}{rows_hit[code]:>7}"
              f"{len(symbols_hit[code]):>9}")

    if evidence:

        costs = []
        interest = []

        for row in rows:

            for attempt in as_list(row.get("Option Liquidity Attempts")):

                code = str(attempt.get("code") or "")

                if code == "OPTION_TOO_EXPENSIVE":

                    costs.append(number(attempt.get("contract_cost")))

                elif code == "LOW_OPEN_INTEREST":

                    interest.append(number(attempt.get("open_interest")))

        costs = [c for c in costs if c is not None]
        interest = [i for i in interest if i is not None]

        if costs:

            print(f"   refused on cost: median ${statistics.median(costs):,.0f}, "
                  f"cheapest ${min(costs):,.0f}")

        if interest:

            print(f"   refused on OI:   median {statistics.median(interest):,.0f}, "
                  f"highest {max(interest):,.0f}")


def show_gate(rows):

    print("\n== entry gate, on rows that had a priced contract ==")
    candidates = [row for row in rows if priced(row)]

    if not candidates:

        print("   none")
        return

    counts = collections.Counter(
        str(row.get("ENTRY_GATE_FAILURE") or "(passed)") for row in candidates
    )

    for reason, count in counts.most_common(10):

        print(f"   {reason[:34]:36}{count:>8}")


def show_trades(trades):

    print(f"\n== trades opened: {len(trades)} ==")

    if not trades:

        print("   none")
        return

    print(f"   {'sym':6}{'dir':5}{'R':>7}{'net%':>8}{'MFE':>7}{'MAE':>7}"
          f"{'cost':>7}{'spr%':>7}{'OI':>8}  {'exit':<22}{'status'}")

    for trade in trades:

        payload = as_dict(trade.get("payload"))
        entry_mid = number(trade.get("option_entry_mid"))
        returns = option_return(trade)

        # A missing field and a zero are different facts, and printing "0" for
        # both invites exactly the wrong conclusion: AAPL on 2026-08-05 read as
        # traded on zero open interest against a 500 floor, when in truth its
        # OI was never recorded. `evaluate_option_liquidity` attaches evidence
        # to rejections only, so the contract actually bought stores none.
        def cell(value, width, spec, scale=1.0):

            if value is None:

                return "-".rjust(width)

            return f"{value * scale:{spec}}".rjust(width)

        print(
            f"   {str(trade['symbol']):6}{str(trade['direction'])[:4]:5}"
            f"{cell(number(trade.get('r_multiple')), 7, '.2f')}"
            f"{cell(returns, 8, '.1f')}"
            f"{cell(number(payload.get('mfe_r')), 7, '.2f')}"
            f"{cell(number(payload.get('mae_r')), 7, '.2f')}"
            f"{cell(entry_mid, 7, '.0f', 100.0)}"
            f"{cell(number(payload.get('option_entry_spread_pct')), 7, '.2f')}"
            f"{cell(number(payload.get('option_open_interest')), 8, '.0f')}"
            f"  {str(payload.get('exit_rule') or payload.get('exit_reason') or '-')[:20]:<22}"
            f"{trade['status']}"
        )

    r_values = [
        v for v in (number(t.get("r_multiple")) for t in trades) if v is not None
    ]
    returns = [v for v in (option_return(t) for t in trades) if v is not None]

    if r_values:

        print(f"\n   R:    mean {statistics.mean(r_values):+.2f}  "
              f"total {sum(r_values):+.2f}  "
              f"win {sum(1 for v in r_values if v > 0)}/{len(r_values)}")

    if returns:

        print(f"   cash: mean {statistics.mean(returns):+.1f}%  "
              f"win {sum(1 for v in returns if v > 0)}/{len(returns)}")

    disagree = sum(
        1 for trade in trades
        if (number(trade.get("r_multiple")) or 0) > 0
        and (option_return(trade) or 0) < 0
    )

    if disagree:

        print(f"   {disagree} trade(s) green in R and red in cash "
              f"-- the spread, crossed twice")


def show_integrity(rows, trades, ledger):
    """Whether the day's own record is trustworthy, before anyone reads it.

    Two defects shipped to production on 2026-08-03 and neither was visible to
    894 passing tests, because both were about what the running system wrote
    rather than what a function returned. `mae_r` silently became None on every
    trade, and the paper trader took three trades the alert layer refused to
    publish -- a divergence that had been live for weeks and surfaced only
    because a subscriber count did not add up.

    Both are one query away. A report that does not check its own inputs is how
    a number nobody trusts gets quoted for a month.
    """

    problems = []

    for field in ("mfe_r", "mae_r"):

        missing = [
            trade for trade in trades
            if number(as_dict(trade.get("payload")).get(field)) is None
        ]

        if missing and trades:

            problems.append(
                f"{len(missing)}/{len(trades)} trades missing {field} "
                f"({', '.join(str(t['symbol']) for t in missing[:6])})"
            )

    # Trades admitted by a path the alert layer will not publish. The ledger
    # names it outright: OPENED / REVIEW_TV_CHART_VALIDATION_ELIGIBLE, followed
    # by TELEGRAM_ENTRY_ALERT / ACTION_NOT_ALERTABLE.
    off_gate = [
        entry for entry in ledger
        if str(entry.get("decision") or "").upper().startswith("OPENED")
        and "REVIEW_TV_CHART" in str(entry.get("reason") or "").upper()
    ]

    if off_gate:

        problems.append(
            f"{len(off_gate)} trade(s) opened via REVIEW_TV_CHART_VALIDATION, "
            f"which the alert layer refuses to publish "
            f"({', '.join(str(e['symbol']) for e in off_gate[:6])}) "
            f"-- the record and the subscriber experience diverge here"
        )

    not_alertable = [
        entry for entry in ledger
        if "ACTION_NOT_ALERTABLE" in str(entry.get("reason") or "").upper()
    ]

    if not_alertable:

        problems.append(
            f"{len(not_alertable)} alert(s) suppressed as ACTION_NOT_ALERTABLE "
            f"({', '.join(str(e['symbol']) for e in not_alertable[:6])})"
        )

    unpriced = [
        trade for trade in trades
        if not number(trade.get("option_entry_mid"))
    ]

    if unpriced:

        problems.append(
            f"{len(unpriced)}/{len(trades)} trades carry no entry premium, "
            f"so their cash P&L cannot be computed"
        )

    print("\n== data integrity ==")

    if not problems:

        print("   nothing to flag")
        return

    for problem in problems:

        print(f"   !! {problem}")


def show_noise_note(today, history):
    """The whole point: say out loud when a day proves nothing."""

    r_history = [day["mean_r"] for day in history if day.get("mean_r") is not None]
    trades = today.get("trades") or 0

    if not r_history or not trades:

        return

    spread = statistics.stdev(r_history) if len(r_history) > 1 else 0.0
    standard_error = spread / math.sqrt(max(len(r_history), 1))
    mean = today.get("mean_r")

    print("\n== how much this day proves ==")
    print(f"   {trades} trades. Across the baseline, daily mean R has an "
          f"sd of {spread:.2f}.")

    # With a handful of trades the day's own mean is so noisy that comparing it
    # to anything is theatre, however far from the baseline it lands. Say that
    # before quoting the comparison, not after.
    if trades < 10:

        print(f"   At {trades} trades this day cannot separate skill from "
              f"luck at any effect size worth acting on.")

    elif mean is not None:

        inside = abs(mean - statistics.mean(r_history)) <= 2 * spread
        print(f"   Today's {mean:+.2f}R is "
              f"{'inside' if inside else 'OUTSIDE'} two sd of the "
              f"{statistics.mean(r_history):+.2f}R baseline.")

    if spread > 0:

        needed = 15.7 * (spread ** 2) / (0.25 ** 2)
        print(f"   Detecting a real +0.25R improvement would take about "
              f"{needed:.0f} trades an arm "
              f"({needed / max(trades, 1):.0f} sessions at today's rate).")

    print("   Treat this as monitoring. Rule changes need a batched A/B, "
          "not one session.")


# ------------------------------------------------------------------------ main


def main():

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", default=None, help="YYYY-MM-DD, default today")
    parser.add_argument("--baseline", type=int, default=10,
                        help="trailing sessions used for the bands")
    args = parser.parse_args()

    day = args.day or str(date.today())

    with get_engine().begin() as connection:

        rows, trades, ledger = load_day(connection, day)

        if not rows:

            print(f"no scanner_snapshot rows for {day} -- "
                  f"either not a trading day, or the archive has not landed yet")
            return

        history = []

        for previous in baseline_days(connection, day, args.baseline):

            past_rows, past_trades, _past_ledger = load_day(connection, previous)
            history.append(day_metrics(past_rows, past_trades))

    today = day_metrics(rows, trades)

    print(f"\n{'=' * 78}")
    print(f"  {day}   {today['rows']} rows / {today['symbols']} symbols   "
          f"baseline: {len(history)} prior sessions")
    print(f"{'=' * 78}\n")

    show_headline(today, history)
    show_funnel(rows)
    show_blockers(rows)
    show_rejections(rows)
    show_gate(rows)
    show_trades(trades)
    show_integrity(rows, trades, ledger)
    show_noise_note(today, history)
    print()


if __name__ == "__main__":

    main()
