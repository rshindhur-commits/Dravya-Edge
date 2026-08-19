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
import os
from unittest import mock
from unittest.mock import patch

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


def test_mfe_comes_from_the_live_engine_not_the_v2_shadow():
    """Two engines emit mfe_r against independent risk denominators.

    `execution_metrics` is `shadow_exit_v2`, handed the *moved* stop, so its R
    denominator shifts every time the stop trails. SMCI on 2026-08-03 recorded
    mfe_r 1.39 while its own highest_price of 28.91 against the 0.14 entry risk
    was a 2.07R peak. The trade's realised r_multiple is measured against entry
    risk, so its MFE has to be as well or the pair is not comparable.
    """

    trade = {}

    paper_trade_manager._ratchet_excursions(
        trade,
        {"mfe_r": 2.07, "mae_r": 0.4},          # live engine, entry-frozen risk
        {"mfe_r": 1.39, "mae_r": 0.9},          # V2 shadow, moved-stop risk
    )

    assert trade["mfe_r"] == 2.07
    assert trade["mae_r"] == 0.4


def test_mae_survives_a_live_engine_that_only_reports_mfe():
    """The shape that actually occurs, and that cost a day of MAE.

    `evaluate_exit` returns `mfe_r` and never `mae_r`. Precedence was applied
    per *source* rather than per field, so the live engine supplying mfe_r
    ended the walk and the shadow's mae_r was discarded unread. Every trade on
    2026-08-04 stored mae_r None where 2026-08-03 had 0.18, 0.73 and 0.14.

    The test above passes mae_r in both sources, which is why it stayed green
    through the regression.
    """

    trade = {}

    paper_trade_manager._ratchet_excursions(
        trade,
        {"mfe_r": 2.07},                        # live engine: no mae_r, ever
        {"mfe_r": 1.39, "mae_r": 0.9},          # shadow has one
    )

    assert trade["mfe_r"] == 2.07, "the live engine still wins where it spoke"
    assert trade["mae_r"] == 0.9, "a field it never sets must fall through"


def test_the_shadow_is_still_used_when_no_live_reading_exists():
    """Callers that pass no exit_state keep the behaviour they had."""

    trade = {}

    paper_trade_manager._ratchet_excursions(trade, None, {"mfe_r": 1.39})

    assert trade["mfe_r"] == 1.39


def test_excursions_ratchet_across_scans_whichever_source_spoke():

    trade = {}

    paper_trade_manager._ratchet_excursions(trade, {"mfe_r": 2.07}, None)
    paper_trade_manager._ratchet_excursions(trade, {"mfe_r": 0.5}, None)

    assert trade["mfe_r"] == 2.07, "a retrace must not erase the peak"


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


def _close(trade, close_price):
    """Drive the real close path against an in-memory trade book."""

    key = trade["trade_key"]
    state = {key: dict(trade)}
    saved = {}

    with patch.object(paper_trade_manager, "load_paper_trades", return_value=state), \
         patch.object(paper_trade_manager, "save_paper_trades",
                      side_effect=lambda s: saved.update(s)), \
         patch.object(paper_trade_manager, "_queue_paper_trade_upsert"), \
         patch.object(paper_trade_manager, "_append_trend_capture_for_closed_trade",
                      return_value=None), \
         patch.object(paper_trade_manager, "record_trade_event", create=True), \
         patch.object(paper_trade_manager, "dispatch_exit_alert", create=True):

        return paper_trade_manager.close_paper_trade(
            trade["symbol"], close_price=close_price,
            exit_reason="Profit target reached (long)", notify_exit=False,
        )


def _orcl(**overrides):
    """ORCL on 2026-08-03: entry 137.38, risk 1.01, closed 139.81 for +2.41R.

    Its recorded `mfe_r` was 1.43 -- a peak *below* the outcome, which cannot
    happen. `update_paper_trade` ratchets MFE but only runs on holding scans; the
    scan that closes a trade takes a different path and never folded in the final,
    highest excursion.
    """

    trade = {
        "trade_key": "ORCL|O:ORCL260814C00145000|2026-08-03 11:58:12",
        "symbol": "ORCL",
        "direction": "CALL",
        "status": "OPEN",
        "entry_price": 137.38,
        "stop_loss": 138.33,
        "initial_stop_loss": 136.37,
        "take_profit": 139.81,
        "opened_at": "2026-08-03 11:58:12",
        "opened_at_et": "2026-08-03T11:58:12-04:00",
        "mfe_r": 1.43,
    }
    trade.update(overrides)
    return trade


def test_mfe_is_ratcheted_against_the_realised_outcome_at_close():
    """A trade cannot close above its own high-water mark."""

    closed = _close(_orcl(), close_price=139.81)

    assert closed["r_multiple"] == 2.41
    assert closed["mfe_r"] >= closed["r_multiple"], (
        "MFE below realised R makes trend capture exceed 100% and leaves the "
        "profit-lock watch comparing an exit against a peak lower than itself"
    )
    assert closed["mfe_r"] == 2.41


def test_a_genuine_giveback_keeps_its_peak():
    """Ratcheted, not assigned: SMCI locked at 28.76 after a 2.07R peak."""

    closed = _close(
        _orcl(mfe_r=2.07, take_profit=999.0), close_price=137.38 + 1.01,
    )

    assert closed["r_multiple"] == 1.0
    assert closed["mfe_r"] == 2.07, "the peak must survive a smaller outcome"


def test_an_unknown_r_leaves_mfe_alone():
    """No close price means no R, and no basis to ratchet against."""

    closed = _close(_orcl(), close_price=None)

    assert closed["r_multiple"] is None
    assert closed["mfe_r"] == 1.43


# --------------------------------------------------------------------------
# The option peak, which the give-back floor is measured against
# --------------------------------------------------------------------------

def test_the_option_peak_is_carried_between_scans():
    """Without this the two-tier give-back floor cannot fire at all.

    `evaluate_exit` emits `option_peak_mid` into its verdict and nothing wrote
    it back: 0 of 54 recorded trades carried the field. So
    `_option_giveback_exit` fell to `peak = current_mid` on every scan
    (`exit_engine.py:522`), the give-back was structurally zero, and the floor
    that `_giveback_floor` measured best of four candidates (+143.4% against
    +52.4% for the book) had never once run.

    Same defect the stop already has a test for -- a peak the engine re-derives
    every pass is a peak that never existed.
    """

    trade = {}

    paper_trade_manager._ratchet_excursions(trade, {"option_peak_mid": 9.95}, None)

    assert trade["option_peak_mid"] == 9.95


def test_the_option_peak_survives_a_retrace():
    """TSLA #340 on 2026-08-19: peaked at 9.95 against a 9.625 entry and closed
    at 9.125. Re-deriving the peak from the current mid on the closing scan is
    what let a +3.4% winner book -5.2%."""

    trade = {}

    paper_trade_manager._ratchet_excursions(trade, {"option_peak_mid": 9.95}, None)
    paper_trade_manager._ratchet_excursions(trade, {"option_peak_mid": 9.125}, None)

    assert trade["option_peak_mid"] == 9.95, "a retrace must not erase the peak"


def test_the_carried_peak_arms_the_breakeven_floor():
    """The point of carrying it: with the peak the floor fires, without it the
    give-back is zero and nothing fires. Uses the deployed 3% arm."""

    from app.exit.exit_engine import _option_giveback_exit

    state = {"option_entry_mid": 9.625, "option_current_mid": 9.125}

    with mock.patch.dict(os.environ, {"EXIT_OPTION_BREAKEVEN_ARM_PCT": "3"},
                         clear=False):

        carried, _peak, _floor = _option_giveback_exit(state, 9.95)
        rederived, _p2, _f2 = _option_giveback_exit(state, None)

    assert carried is True, "a +3.4% peak fallen to -5.2% must breach the floor"
    assert rederived is False, "without the peak the give-back is structurally zero"
