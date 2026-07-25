from __future__ import annotations

from copy import deepcopy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.alerts.telegram_alerts import (
    maybe_send_multiday_position_continue_alert,
    maybe_send_trade_open_alert,
    maybe_send_trade_cancelled_alert,
    maybe_send_paper_trade_update_alert,
    maybe_send_trade_exit_alert,
)

trade = {
    'symbol': 'SPCX',
    'direction': 'CALL',
    'status': 'OPEN',
    'trade_mode': 'PAPER',
    'entry_price': 325.0,
    'stop_loss': 319.0,
    'take_profit': 335.0,
    'option_ticker': 'SPCX 24AUG26 325C',
    'option_entry_mid': 3.25,
    'option_mid': 3.25,
    'option_contracts': 1,
    'trade_key': 'TEST-SPCX-LIFECYCLE-003',
    'opened_at': '2026-07-24 13:15:00',
    'holding_profile': 'MULTIDAY',
    'days_held': 2,
    'overnight_count': 1,
    'overnight_transition': True,
    'session_id_current': 'paper_validation_2026-07-24_lifecycle_test_003',
}
scanner_context = {
    'Action Status': 'ENTER_PAPER',
    'Realtime Ready': 'true',
    'Final Signal': 'CONFIRMED',
    'V2 Trend Health Status': 'STRONG',
    'V2 Trend Health Score': 88,
    'Option Strike': 325,
    'Option Expiration': '2026-08-24',
    'Option Mid Price': 3.25,
    'Option Spread %': 1.8,
    'Option Quality Score': 85,
    'Expected Remaining Trend': 92,
    'Candidate RR': 2.0,
    'Entry': 'EMA_PULLBACK',
    'Candidate Direction': 'CALL',
    'V2 Pullback Number': 1,
    'Relative Volume': 1.2,
    'RS Rank Score': 3,
    'Option Contract Cost': 325,
    'Option Risk At Stop': 125,
}

print('--- SEND NEW TRADE ---')
print(maybe_send_trade_open_alert(trade, scanner_context))

print('\n--- SEND TRADE UPDATE ---')
print(maybe_send_paper_trade_update_alert(
    trade,
    current_price=327.0,
    scanner_context={**scanner_context, 'V2 Trend Health Status': 'HEALTHY'},
    updated_stop=321.0,
    partial_profit_taken=False,
    confidence_score=82,
))

print('\n--- SEND POSITION CONTINUES ---')
print(maybe_send_multiday_position_continue_alert(
    trade,
    current_price=328.5,
    scanner_context={**scanner_context, 'V2 Trend Health Status': 'HEALTHY'},
))

print('\n--- SEND PARTIAL PROFIT ---')
print(maybe_send_trade_exit_alert(
    'SPCX',
    trade,
    exit_reason='PARTIAL_PROFIT',
    current_price=330.0,
    option_current_mid=4.20,
    pnl_pct=29.2,
    r_multiple=1.2,
    outcome='PARTIAL_WIN',
    event_type='PARTIAL_EXIT',
    mfe_r=2.1,
))

closed_categories = [
    ('TARGET_HIT', 335.0, 5.90, 81.5, 2.0, 'WIN'),
    ('STOP_HIT', 319.0, 2.45, -24.6, -1.0, 'LOSS'),
    ('EMA_EXIT', 329.0, 4.00, 23.1, 0.67, 'WIN'),
    ('VWAP_EXIT', 326.0, 3.45, 6.2, 0.17, 'WIN'),
    ('TIME_EXIT', 327.0, 3.60, 10.8, 0.33, 'WIN'),
    ('FAILED_BREAKOUT', 321.0, 2.75, -15.4, -0.67, 'LOSS'),
    ('MANUAL_EXIT', 331.0, 4.50, 38.5, 1.0, 'WIN'),
]

for index, (reason, price, option_mid, pnl_pct, r_multiple, outcome) in enumerate(closed_categories, start=1):
    exit_trade = deepcopy(trade)
    exit_trade['trade_key'] = f"TEST-SPCX-CLOSED-CATEGORY-{index:02d}"
    exit_trade['option_ticker'] = f"SPCX 24AUG26 {325 + index}C"
    exit_trade['opened_at'] = f"2026-07-24 13:{15 + index:02d}:00"
    exit_trade['exit_alert_sent'] = False
    print(f'\n--- SEND TRADE CLOSED: {reason} ---')
    print(maybe_send_trade_exit_alert(
        'SPCX',
        exit_trade,
        exit_reason=reason,
        current_price=price,
        option_current_mid=option_mid,
        pnl_pct=pnl_pct,
        r_multiple=r_multiple,
        outcome=outcome,
        event_type='EXIT',
        mfe_r=max(abs(r_multiple), 1.0),
    ))

print('\n--- SEND TRADE CANCELLED ---')
print(maybe_send_trade_cancelled_alert(
    {
        'suggestion_id': 'TEST-SPCX-CANCELLED-003',
        'symbol': 'SPCX',
        'direction': 'PUT',
        'option_ticker': 'SPCX 24AUG26 320P',
    },
    reason='Entry conditions never confirmed.',
    event_timestamp='2026-07-24 14:45:00',
))
