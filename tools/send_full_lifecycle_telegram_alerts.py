from __future__ import annotations

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
    'trade_key': 'TEST-SPCX-LIFECYCLE-002',
    'opened_at': '2026-07-24 11:15:00',
    'holding_profile': 'MULTIDAY',
    'days_held': 2,
    'overnight_count': 1,
    'overnight_transition': True,
    'session_id_current': 'paper_validation_2026-07-24_lifecycle_test',
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

print('\n--- SEND TRADE CLOSED ---')
print(maybe_send_trade_exit_alert(
    'SPCX',
    trade,
    exit_reason='TARGET_HIT',
    current_price=335.0,
    option_current_mid=5.90,
    pnl_pct=81.5,
    r_multiple=2.0,
    outcome='WIN',
    event_type='EXIT',
    mfe_r=2.8,
))

print('\n--- SEND TRADE CANCELLED ---')
print(maybe_send_trade_cancelled_alert(
    {
        'suggestion_id': 'TEST-SPCX-CANCELLED-002',
        'symbol': 'SPCX',
        'direction': 'PUT',
        'option_ticker': 'SPCX 24AUG26 320P',
    },
    reason='Entry conditions never confirmed.',
    event_timestamp='2026-07-24 14:45:00',
))
