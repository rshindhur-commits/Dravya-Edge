"""What the option chain offered a candidate, in fields you can query.

S-A3 of docs/OPTIONS_QUALITY_PLAN.md.

The evidence itself is not missing. Since `6af092e` and `65361cb` every contract
attempt carries twenty-one fields -- strike, dte, open interest, volume, bid,
ask, delta, iv, cost, quote status, and the threshold it was measured against --
and the whole list is durable in `scanner_snapshot.decision_payload`.

It is stored there as a **JSON string inside a JSONB column**, which is the
problem. Postgres cannot look inside it, so "what was the best contract
available for NVDA at 10:15, and why was it not taken?" needs the whole of a
233MB table pulled across the wire and parsed in Python, per question. That is
why the answer to "is 500 too high, or is the selector reaching for the wrong
strike?" has been re-derived by hand every time it has been asked.

This summarises the attempts a candidate actually made into flat fields, so the
questions that get asked repeatedly become one query. The raw list stays exactly
as it is -- this adds a reading of it, it does not replace it.

Nothing here re-derives a verdict. Every number is read off attempts the filter
already produced, so the summary cannot disagree with the decision it describes.
"""

from __future__ import annotations

import json


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if result != result or result in (float("inf"), float("-inf")):
        return None

    return result


def _parse(attempts):
    """Attempts as a list, from a list or from the JSON string on the row."""

    if isinstance(attempts, str):
        try:
            attempts = json.loads(attempts)
        except (ValueError, TypeError):
            return []

    return [a for a in attempts if isinstance(a, dict)] if isinstance(attempts, list) else []


def summarize_chain(attempts):
    """Flat summary of what the chain offered, prefixed CHAIN_.

    `CHAIN_NEAR_MISS_*` describes the **tightest-spread contract that was
    refused** -- the one closest to worth owning that the app declined. Its code
    is the answer to "why did we not take the best thing on offer", which is a
    different question from "what refused the most contracts" (`CHAIN_BINDING_CODE`)
    and usually has a different answer.
    """

    attempts = _parse(attempts)

    summary = {
        "CHAIN_EXAMINED": len(attempts),
        "CHAIN_ACCEPTED": False,
        "CHAIN_BEST_SPREAD_PCT": None,
        "CHAIN_BEST_OPEN_INTEREST": None,
        "CHAIN_CHEAPEST_COST": None,
        "CHAIN_BINDING_CODE": None,
        "CHAIN_NEAR_MISS_CODE": None,
        "CHAIN_NEAR_MISS_SPREAD_PCT": None,
        "CHAIN_NEAR_MISS_OPEN_INTEREST": None,
        "CHAIN_NEAR_MISS_COST": None,
        "CHAIN_NEAR_MISS_DELTA": None,
    }

    if not attempts:
        return summary

    summary["CHAIN_ACCEPTED"] = any(a.get("accepted") for a in attempts)

    spreads = [s for s in (_number(a.get("spread_pct")) for a in attempts) if s is not None]
    interest = [o for o in (_number(a.get("open_interest")) for a in attempts) if o is not None]
    costs = [c for c in (_number(a.get("contract_cost")) for a in attempts) if c is not None]

    # The best of each dimension across everything examined, whether or not any
    # single contract had all three. A chain whose tightest spread is 12% is a
    # different problem from one where a 1% spread existed and something else
    # refused it, and that distinction is invisible from rejection counts.
    summary["CHAIN_BEST_SPREAD_PCT"] = round(min(spreads), 2) if spreads else None
    summary["CHAIN_BEST_OPEN_INTEREST"] = int(max(interest)) if interest else None
    summary["CHAIN_CHEAPEST_COST"] = round(min(costs), 2) if costs else None

    refused = [a for a in attempts if not a.get("accepted")]

    if refused:
        codes = {}
        for attempt in refused:
            code = attempt.get("code")
            if code:
                codes[code] = codes.get(code, 0) + 1

        if codes:
            summary["CHAIN_BINDING_CODE"] = max(codes, key=codes.get)

        priced = [a for a in refused if _number(a.get("spread_pct")) is not None]

        if priced:
            best = min(priced, key=lambda a: _number(a.get("spread_pct")))
            summary["CHAIN_NEAR_MISS_CODE"] = best.get("code")
            summary["CHAIN_NEAR_MISS_SPREAD_PCT"] = round(_number(best.get("spread_pct")), 2)
            summary["CHAIN_NEAR_MISS_OPEN_INTEREST"] = (
                int(_number(best.get("open_interest")))
                if _number(best.get("open_interest")) is not None else None
            )
            summary["CHAIN_NEAR_MISS_COST"] = (
                round(_number(best.get("contract_cost")), 2)
                if _number(best.get("contract_cost")) is not None else None
            )
            summary["CHAIN_NEAR_MISS_DELTA"] = (
                round(_number(best.get("delta")), 4)
                if _number(best.get("delta")) is not None else None
            )

    return summary
