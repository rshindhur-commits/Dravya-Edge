"""The setups the entry engine can emit, and what direction each one means.

`detect_entry` emits exactly five setup types. Five *other* names were carried
alongside them in membership lists across the codebase -- VWAP_RECLAIM,
COILED_BREAKOUT, COILED_BREAKDOWN, HIGHER_LOW_CONTINUATION and
BREAKOUT_CONTINUATION -- because their detectors were commented out in
`entry_engine.py` while every list that referenced them was left as it was. None
of them can occur. They made the system look like it supported nine setups when
it supported five, and made the regime block look more comprehensive than it was.

That drift was not harmless. Five independent lists had grown apart:

* `option_direction` mapped nine names to a direction.
* `risk_manager` had two more lists, again including dead names.
* `paper_automation_support.REVIEW_VALIDATION_ENTRY_TYPES` allowed both
  directions; `dashboard.py` had a copy of the same constant allowing only longs,
  so the dashboard would show a SPY BREAKDOWN_SHORT as ineligible while the
  scanner would open it.
* the scanner's entry-timing filter named "BREAKOUT_LONG", which `detect_entry`
  never emits -- so for as long as it had existed, only shorts were timing
  filtered and every breakout long went through unchecked.

That last one was a live trade-quality defect caused purely by two lists
disagreeing about a name. One definition, imported everywhere, removes the class.

Re-enabling a commented-out detector is a separate decision that needs evidence;
this module deliberately does not make it easier or harder, it only stops the
codebase claiming the detectors are already there.
"""

from __future__ import annotations


# The complete set of setups `detect_entry` can return, and the option direction
# each implies. Adding a detector means adding it here, and every consumer picks
# it up.
SETUP_DIRECTIONS = {
    "BREAKOUT": "CALL",
    "EMA_PULLBACK": "CALL",
    "BREAKDOWN_SHORT": "PUT",
    "EMA_REJECTION_SHORT": "PUT",
    "VWAP_REJECTION": "PUT",
}

# Not emitted by the entry engine, but synthesised by the scanner when it has to
# infer a held position's direction from its entry stop (app/main.py). Real, and
# must keep resolving to a direction.
LEGACY_SETUP_ALIASES = {
    "BREAKOUT_LONG": "BREAKOUT",
}

# Placeholders that occupy the same field but are not setups.
NON_SETUP_MARKERS = frozenset({
    "NO_ENTRY",
    "NO_SETUP",
    "ACTIVE_TRADE",
    "PAPER_TRADE",
    "OPEN_TRADE",
})

ACTIVE_SETUPS = frozenset(SETUP_DIRECTIONS)
LONG_SETUPS = frozenset(
    name for name, direction in SETUP_DIRECTIONS.items() if direction == "CALL"
)
SHORT_SETUPS = frozenset(
    name for name, direction in SETUP_DIRECTIONS.items() if direction == "PUT"
)

# Everything a consumer may legitimately see in an entry_type field.
KNOWN_SETUPS = ACTIVE_SETUPS | frozenset(LEGACY_SETUP_ALIASES)


def canonical_setup(entry_type):
    """Resolve legacy aliases to the setup they stand for."""

    name = str(entry_type or "").strip().upper()

    return LEGACY_SETUP_ALIASES.get(name, name)


def setup_direction(entry_type):
    """"CALL", "PUT", or None when the name is not a setup."""

    return SETUP_DIRECTIONS.get(canonical_setup(entry_type))


def is_short_setup(entry_type):

    return setup_direction(entry_type) == "PUT"


def is_long_setup(entry_type):

    return setup_direction(entry_type) == "CALL"


def is_tradeable_setup(entry_type):
    """A real setup rather than a placeholder or an unrecognised name."""

    return canonical_setup(entry_type) in KNOWN_SETUPS
