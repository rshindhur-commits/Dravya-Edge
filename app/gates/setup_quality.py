"""Setup % -- how good the setup is, measured once, in one place.

Two defects, both measured against the 1,633-row `scanner_snapshot` archive:

**It measured the wrong thing.** The old composition was
`|15m Score|*40 + RR*25 + entry*15 + action_status*20`. RR and Action Status are
already hard gates in `evaluate_entry_gate` -- a row failing either is rejected
outright -- so scoring them again inside the threshold the gate then applies made
the setup check partly a second RR check. It was not a small effect: the old
metric's rank correlation was **0.675 against RR and only 0.473 against
|15m Score|**. A field called "Setup %" tracked risk/reward more closely than it
tracked the setup. Two candidates with identical conviction scored differently
purely on geometry that had already been gated.

**It saturated.** `min(|score|/10, 1)` flattened everything at |score| >= 10,
which is **54% of gate-eligible rows** (median 8.5, max 24.5). Across the top
half of the tradeable population the metric was constant, so it could not rank
what it was being asked to rank.

The composition below drops both double-counted terms, widens the conviction
ceiling to 16 to cover the observed range, and adds the multi-timeframe
Alignment Score -- already computed on every row and previously ignored, despite
being the one independent conviction signal the gate had no other view of.

Result on the same archive: rank correlation vs |15m Score| rises 0.473 -> 0.753
and vs RR falls 0.675 -> 0.298. At matched pass rates the surviving candidates
shift from median |score| 10.7 / RR 2.27 to median |score| 13.5 / RR 1.94 --
the same number of trades, selected on conviction instead of on geometry.

Thresholds live here rather than at each call site because they are only
meaningful against this scale. They were re-derived by matching the old pass
rate row-for-row on the archive, so switching over is volume-neutral by
construction and any later change is a deliberate one:

    old  ->  new     pass rate
     70  ->   62       9.3%
     75  ->   70       6.3%
     80  ->   76       3.6%
     82  ->   81       1.8%
     88  ->   83       1.5%
     90  ->   85       1.4%

What this does *not* establish is that the new metric predicts outcomes better.
That needs resolved trades, and at 13 completed trades there is nothing to test
against yet. The claim here is narrower and fully supported: it measures setup
conviction rather than a quantity already gated elsewhere, and it discriminates
across the range where the old one was flat.
"""

from __future__ import annotations


# Conviction. Ceiling 16 covers the observed |15m Score| range (median 8.5,
# p99 ~20, max 24.5) without flattening the top half the way 10 did.
CONVICTION_CEILING = 16.0
CONVICTION_WEIGHT = 60.0

# Multi-timeframe agreement, |Alignment Score|. Observed median 9.75, p99 13.1.
ALIGNMENT_CEILING = 12.0
ALIGNMENT_WEIGHT = 25.0

# A setup actually fired, and survived its own validation.
ENTRY_WEIGHT = 8.0
SETUP_VALID_WEIGHT = 7.0

# Ceilings for rows that are not tradeable at all. Not conviction credit --
# floors that keep unusable rows below usable ones for ranking and analytics.
UNVALIDATED_SETUP_CEILING = 59
UNTRADEABLE_ACTION_CEILING = 49

UNTRADEABLE_ACTIONS = frozenset({
    "AVOID",
    "NO_TRADE_MARKET_CLOSED",
    "OPTION_MARKET_CLOSED",
    "NO_BID_ASK",
    "NO_QUOTE_SNAPSHOT",
    "RATE_LIMITED",
    "PROVIDER_ERROR",
})

# Re-derived against the archive at matched pass rates. See the table above.
#
# The old 85 / 88 / 90 tiers passed 1.53% / 1.47% / 1.41% of rows -- three
# nominally different strictness levels doing very nearly the same job, because
# the old scale was compressed at the top by the saturating conviction term.
# Matched equivalents are 81 / 83 / 83. RANGE_BOUND is set to 85 rather than the
# matched 83 so the tiers stay meaningfully ordered now that there is room for
# them to be; that is the one deliberate tightening here, and it is small.
MIN_SETUP_BASE = 62.0
MIN_SETUP_ELEVATED = 81.0
MIN_SETUP_WEAK_BREADTH = 83.0
MIN_SETUP_RANGE_BOUND = 85.0
MIN_SETUP_MULTIDAY = 76.0

# Display bands, rescaled from 82/75/68/60 by the same mapping.
GRADE_BANDS = ((81, "A+"), (70, "A"), (62, "B"), (52, "C"))

_INVALID_ENTRIES = {"", "NO_ENTRY", "NAN", "NONE", "-"}


def _number(value, default=0.0):

    try:
        result = float(value)
    except (TypeError, ValueError):
        return default

    return default if result != result else result


def _truthy(value):

    if isinstance(value, bool):
        return value

    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def entry_is_valid(entry):

    return str(entry or "").strip().upper() not in _INVALID_ENTRIES


def compute_setup_percent(
    score,
    alignment=None,
    entry=None,
    setup_valid=False,
    action_status=None,
):
    """Setup conviction on 0-100. See the module docstring for the composition."""

    action = str(action_status or "WAIT").strip().upper()

    points = (
        min(abs(_number(score)) / CONVICTION_CEILING, 1) * CONVICTION_WEIGHT
        + min(abs(_number(alignment)) / ALIGNMENT_CEILING, 1) * ALIGNMENT_WEIGHT
        + (ENTRY_WEIGHT if entry_is_valid(entry) else 0.0)
        + (SETUP_VALID_WEIGHT if _truthy(setup_valid) else 0.0)
    )

    if not _truthy(setup_valid) and action != "REVIEW_TV_CHART":
        points = min(points, UNVALIDATED_SETUP_CEILING)

    if action in UNTRADEABLE_ACTIONS:
        points = min(points, UNTRADEABLE_ACTION_CEILING)

    return round(max(0.0, min(points, 100.0)))


def setup_percent_from_row(row):
    """Row-shaped wrapper. Accepts the scanner's column names or a plain dict."""

    def get(*names):
        for name in names:
            try:
                value = row.get(name)
            except Exception:
                continue
            if value is not None and str(value).strip().lower() not in {"", "nan", "none"}:
                return value
        return None

    return compute_setup_percent(
        score=get("15m Score", "score"),
        alignment=get("Alignment Score", "alignment_score"),
        entry=get("Entry", "setup", "setup_type"),
        setup_valid=get("Setup Valid", "setup_valid"),
        action_status=get("Action Status", "action_status"),
    )


def setup_grade(setup_percent):

    value = _number(setup_percent)

    for floor, label in GRADE_BANDS:
        if value >= floor:
            return f"{label} ({int(round(value))})"

    return f"D ({int(round(value))})"
