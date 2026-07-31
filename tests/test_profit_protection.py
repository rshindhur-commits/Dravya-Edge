"""Keeping what a trade earned.

2026-07-31 produced three trades, all CALL/EMA_PULLBACK/INTRADAY, all closed by
a soft rule at an exit confidence under 20. The third ran to +1.66R, printed
"Partial profit threshold reached" three times over ten minutes, then closed at
+0.60R on an EMA9 touch with trend health 95 and exit confidence 11.5. That
1.06R giveback was the difference between the day being roughly flat and -0.47R.

Breakeven protection was already active and did nothing, because the exit came
from a soft rule rather than from the stop.

Two defects made it possible. MFE was overwritten with the latest scan's reading
instead of kept as a running maximum, so protection could never see the peak it
exists to defend. And nothing protected banked profit against a low-confidence
soft exit.
"""

import inspect

from app.exit.exit_engine import resolve_profit_lock
from app.state import paper_trade_manager


def _nvda_args(**overrides):
    """NVDA #3 on 2026-07-31, as the engine saw it at 14:30."""

    args = {
        "exit_code": "EMA",
        "exit_signal": True,
        "mfe_r": 1.66,
        "trend_health_score": 95,
        "exit_confidence_score": 11.5,
        "entry_price": 197.96,
        "current_stop": 197.96,
        "risk_per_share": 0.99,
        "is_short": False,
    }
    args.update(overrides)
    return args


def test_mfe_keeps_the_peak_not_the_latest_reading():
    """update_paper_trade must take a running maximum, as state_trade_manager does."""

    source = inspect.getsource(paper_trade_manager)

    assert 'max(_safe_float(trade.get(field)) or 0.0, value)' in source, (
        "mfe_r/mae_r must ratchet; overwriting loses the peak that profit "
        "protection and grace-zone eligibility both read"
    )


def test_profit_lock_holds_the_nvda_trade_and_ratchets_the_stop():
    """+1.66R peak, EMA9 breaks, trend 95, confidence 11.5: keep it."""

    locked_stop, locked_r = resolve_profit_lock(**_nvda_args())

    assert locked_r is not None
    # Peak 1.66R less the 1.0R giveback allowance leaves a floor at +0.66R.
    assert round(locked_r, 2) == 0.66
    assert round(locked_stop - 197.96, 4) == round(0.66 * 0.99, 4)
    assert locked_stop > 197.96, "the lock must sit in profit"


def test_profit_lock_ignores_hard_exits():
    """A stop or target is not a judgement call and must always be honoured."""

    for code in ("HARD_STOP", "HARD_TARGET", "TIME_EXIT", "NEAR_CLOSE"):
        assert resolve_profit_lock(**_nvda_args(exit_code=code)) == (None, None)


def test_profit_lock_never_engages_without_a_banked_peak():
    """No peak, nothing to protect: a loss can never be widened."""

    for mfe in (-0.5, 0.0, 0.4, 0.99):
        assert resolve_profit_lock(**_nvda_args(mfe_r=mfe)) == (None, None)


def test_profit_lock_honours_a_confident_exit():
    """The engine believing its own exit is enough to let it through."""

    assert resolve_profit_lock(**_nvda_args(exit_confidence_score=60)) == (None, None)


def test_profit_lock_honours_an_exit_once_the_trend_has_decayed():

    assert resolve_profit_lock(**_nvda_args(trend_health_score=40)) == (None, None)


def test_profit_lock_only_ever_ratchets():
    """It must not be able to widen risk, long or short."""

    already_higher = 199.00
    locked_stop, _ = resolve_profit_lock(**_nvda_args(current_stop=already_higher))
    assert locked_stop >= already_higher

    short_stop = 197.00
    locked_short, _ = resolve_profit_lock(
        **_nvda_args(is_short=True, current_stop=short_stop)
    )
    assert locked_short <= short_stop


def test_profit_lock_is_symmetric_for_shorts():

    locked_stop, locked_r = resolve_profit_lock(
        **_nvda_args(is_short=True, current_stop=197.96)
    )

    assert round(locked_r, 2) == 0.66
    assert locked_stop < 197.96, "a short locks below entry"


def test_closed_trades_are_not_reverted_to_open():
    """The upsert must refuse a stale OPEN snapshot over a CLOSED row.

    Upserts are queued jobs carrying a snapshot taken when queued, and nothing
    orders them. On 2026-07-31 CRWD sat OPEN for 39 minutes after exiting and
    NVDA for 71, the realised R living only inside an alert payload.
    """

    from app.db import persistence

    source = inspect.getsource(persistence.upsert_paper_trade)

    assert "IS DISTINCT FROM 'CLOSED'" in source
    assert "EXCLUDED.status = 'CLOSED'" in source


def test_entry_side_option_ask_is_frozen_at_open():
    """Net premium P&L needs the ask paid at entry, not the live one."""

    source = inspect.getsource(paper_trade_manager)

    assert '"option_entry_ask": option_ask' in source
    assert 'trade.get("option_entry_ask")' in source
