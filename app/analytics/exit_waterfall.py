from __future__ import annotations


EXIT_WATERFALL_ORDER = [
    "HARD_STOP",
    "HARD_TARGET",
    "VWAP",
    "EMA",
    "MACD",
    "FAILED_BREAKOUT",
    "TIME_EXIT",
    "NEAR_CLOSE",
]


def build_exit_waterfall(exit_diagnostics, selected_rule=None):
    """Describe every exit stage without changing the live exit decision."""

    diagnostics_by_code = {
        str(item.get("code") or "").upper(): item
        for item in (exit_diagnostics or [])
        if isinstance(item, dict)
    }
    records = []

    for position, code in enumerate(EXIT_WATERFALL_ORDER, start=1):

        diagnostic = diagnostics_by_code.get(code)
        triggered = diagnostic is not None
        records.append({
            "stage": position,
            "rule": code,
            "status": (
                "SELECTED"
                if code == selected_rule
                else "TRIGGERED"
                if triggered
                else "PASSED"
            ),
            "reason": diagnostic.get("reason") if diagnostic else None,
            "priority": diagnostic.get("priority") if diagnostic else None,
        })

    return records