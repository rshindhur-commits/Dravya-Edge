"""A closing alert must not call a losing trade a win.

R is measured against the underlying's stop distance and is blind to the option
spread, which is crossed twice and costs 3.40 points of premium across 601
archived trades. So the two disagree in sign routinely, and on 2026-08-05 three
alerts in one session said the opposite of what the account did:

    TSLA  +0.46R  -$32.50  went out as "WIN"
    SMCI  +0.40R   -$2.50  went out as "WIN"
    AVGO  -0.70R  +$12.50  went out as a loss
"""

import pytest

from app.alerts import telegram_alerts


def verdict(entry_mid, current_mid, r_multiple, contracts=1):
    """The result label a closing alert would carry."""

    message = telegram_alerts.build_trade_exit_alert_message(
        "TSLA",
        {
            "symbol": "TSLA",
            "direction": "PUT",
            "option_entry_mid": entry_mid,
            "option_contracts": contracts,
            "opened_at": "2026-08-05T10:55:00-04:00",
        },
        "VWAP Exit",
        option_current_mid=current_mid,
        r_multiple=r_multiple,
        event_type="EXIT",
    )

    # The label sits on its own line under RESULT. Matching the substring
    # instead picks up the exit reason -- "Stop Loss" contains "Loss".
    labels = {"✅ WIN": "WIN", "Loss": "LOSS", "Closed": "CLOSED"}

    for line in message.splitlines():

        stripped = line.strip()

        if stripped in labels:

            return labels[stripped]

    return None


@pytest.mark.parametrize(
    "entry,exit_mid,r,expected",
    [
        # The three real disagreements from 2026-08-05.
        (10.43, 10.10, 0.46, "LOSS"),   # TSLA: R positive, account down
        (2.025, 2.00, 0.40, "LOSS"),    # SMCI: R positive, account down
        (9.22, 9.35, -0.70, "WIN"),     # AVGO: R negative, account up
        # And the ordinary cases, which must not regress.
        (5.00, 6.00, 1.20, "WIN"),
        (5.00, 4.00, -0.90, "LOSS"),
    ],
)
def test_the_verdict_follows_the_money(entry, exit_mid, r, expected):

    assert verdict(entry, exit_mid, r) == expected


def test_r_still_decides_when_no_premium_was_recorded():
    """Not every close carries a fill; R is the fallback, not the default."""

    assert verdict(None, None, 0.8) == "WIN"
    assert verdict(None, None, -0.8) == "LOSS"


def test_an_exactly_flat_trade_is_neither():

    assert verdict(5.00, 5.00, 0.0) == "CLOSED"
