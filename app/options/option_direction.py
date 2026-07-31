from app.strategies.setup_registry import (
    LEGACY_SETUP_ALIASES,
    LONG_SETUPS,
    SHORT_SETUPS,
)


# Derived from the setup registry rather than restated. These previously listed
# four setups the entry engine cannot emit.
SHORT_ENTRY_TYPES = SHORT_SETUPS
LONG_ENTRY_TYPES = LONG_SETUPS | {
    alias
    for alias, target in LEGACY_SETUP_ALIASES.items()
    if target in LONG_SETUPS
}


def resolve_option_direction(final_signal, entry_type=None):

    """
    Resolve the intended option direction from both signal and entry type.

    Entry type wins when it explicitly implies long/short. This prevents
    bearish entries from accidentally selecting calls or bullish entries from
    accidentally selecting puts when signal labels are broad.
    """

    if entry_type in SHORT_ENTRY_TYPES:

        return "PUT"

    if entry_type in LONG_ENTRY_TYPES:

        return "CALL"

    if final_signal in [
        "BEARISH",
        "HIGH CONVICTION BEARISH"
    ]:

        return "PUT"

    if final_signal in [
        "BULLISH",
        "HIGH CONVICTION BULLISH"
    ]:

        return "CALL"

    return "NONE"


def expected_contract_type(option_direction):

    if option_direction == "CALL":

        return "call"

    if option_direction == "PUT":

        return "put"

    return None


def contract_matches_direction(option_data, option_direction):

    expected_type = expected_contract_type(option_direction)

    if expected_type is None:

        return False

    if not option_data:

        return False

    return option_data.get("type") == expected_type
