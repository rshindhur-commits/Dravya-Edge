"""The thresholds this scan actually enforced, recorded with the scan.

On 2026-08-10 a session was spent establishing why the app had stopped taking
trades. The answer was that `OPTION_MAX_SPREAD_PCT` had been changed from 6 to 2
in Render five days earlier. The database held the *effect* -- every accepted
contract capped at exactly 2.00% spread -- and nothing held the cause, because
the value lives in Render and Render is not in git. Recovering it needed the
environment exported by hand.

`scanner_runs` already takes one row per scan and is kept 21 days, so the
enforced configuration goes in its payload and "what was the ceiling on the 4th"
becomes a query instead of an investigation.

Two rules decide what belongs here.

**Resolved, not raw.** Every value is read through the same accessor the scanner
uses, so what lands is what the gate applied -- not what the environment said.
Those differ: the entry gate's spread ceiling fell back to a code default of 10
for weeks while configuration said 6, and no audit could show it because the
audit recorded its own defaults too.

**Never a secret.** Only thresholds. Anything that could carry a key, a
connection string or a token is excluded by construction: this module names the
fields it wants one at a time and never iterates os.environ.
"""

from __future__ import annotations


def _safe(fn, default=None):
    """A setting that cannot be read must not be the reason a scan fails.

    This is telemetry attached to a completed scan. Every accessor below reaches
    configuration, and configuration can be malformed; a bad value is recorded
    as None rather than raised.
    """

    try:
        return fn()

    except Exception:
        return default


def config_snapshot():
    """Enforced trading thresholds, flat and JSON-safe.

    Flat rather than nested so a change is one SQL comparison:

        SELECT DISTINCT payload->'config'->>'option_max_spread_pct'
        FROM scanner_runs WHERE started_at >= now() - interval '30 days';
    """

    import os

    from app.config.settings import get_bool_env, get_float_env, settings
    from app.gates.entry_gate import scanner_entry_gate_config
    from app.risk.option_leverage import enforce_option_leverage, min_option_leverage
    from app.risk.stop_viability import enforce_stop_viability, min_stop_spread_multiple

    gate = _safe(scanner_entry_gate_config)

    snapshot = {
        # Contract selection -- the filter that decides what may be bought.
        "option_max_spread_pct": _safe(lambda: settings.option_max_spread_pct),
        "option_min_open_interest": _safe(lambda: settings.option_min_open_interest),
        "option_min_volume": _safe(lambda: settings.option_min_volume),
        "option_min_quality_score": _safe(lambda: settings.option_min_quality_score),
        "option_min_dte": _safe(lambda: settings.option_min_dte),
        "option_max_dte": _safe(lambda: settings.option_max_dte),
        "option_allow_0dte": _safe(lambda: settings.option_allow_0dte),
        "option_allow_1dte": _safe(lambda: settings.option_allow_1dte),
        "option_max_contract_cost": _safe(
            lambda: get_float_env("OPTION_MAX_CONTRACT_COST", 500.0)
        ),
        "option_min_contract_cost": _safe(
            lambda: get_float_env("OPTION_MIN_CONTRACT_COST", 100.0)
        ),

        # The entry gate, read through the scanner's own config rather than the
        # dataclass defaults -- the distinction this module exists to preserve.
        "gate_min_rr": _safe(lambda: gate.min_rr),
        "gate_min_setup_percent": _safe(lambda: gate.min_setup_percent),
        "gate_min_option_quality": _safe(lambda: gate.min_option_quality),
        "gate_max_spread_pct": _safe(lambda: gate.max_spread_pct),

        # Risk geometry.
        "min_stop_spread_multiple": _safe(min_stop_spread_multiple),
        "stop_viability_enforced": _safe(enforce_stop_viability),
        "min_option_leverage": _safe(min_option_leverage),
        "option_leverage_enforced": _safe(enforce_option_leverage),
        "swing_structure_enabled": _safe(
            lambda: get_bool_env("SWING_STRUCTURE_ENABLED", False)
        ),

        # Auto-paper, which decides whether a passing candidate is acted on.
        "auto_paper_enabled": _safe(lambda: get_bool_env("AUTO_PAPER_ENABLED", False)),
        "auto_paper_min_rr": _safe(lambda: get_float_env("AUTO_PAPER_MIN_RR", 1.8)),
        "auto_paper_min_setup": _safe(lambda: get_float_env("AUTO_PAPER_MIN_SETUP", 62.0)),
        "max_daily_entries": _safe(lambda: get_float_env("MAX_DAILY_ENTRIES", 5.0)),
        # The three levers that decide whether a second signal on a symbol, or a
        # signal on a contract nobody quoted tightly, ever reaches the book. All
        # added 2026-08-22; without them an archived day cannot say whether a
        # reversal was refused by policy or never detected.
        "max_trades_per_symbol_per_day": _safe(
            lambda: get_float_env("MAX_TRADES_PER_SYMBOL_PER_DAY", 1.0)
        ),
        "symbol_daily_cap_directional": _safe(
            lambda: get_bool_env("AUTO_PAPER_SYMBOL_DAILY_CAP_DIRECTIONAL", True)
        ),
        "cooldown_directional": _safe(
            lambda: get_bool_env("AUTO_PAPER_COOLDOWN_DIRECTIONAL", True)
        ),
        "alert_spread_blocked_signals": _safe(
            lambda: get_bool_env("ALERT_SPREAD_BLOCKED_SIGNALS", True)
        ),
        "allow_review_tv_chart_auto_paper": _safe(
            lambda: get_bool_env("ALLOW_REVIEW_TV_CHART_AUTO_PAPER", False)
        ),
        "max_daily_review_validation_entries": _safe(
            lambda: get_float_env("MAX_DAILY_REVIEW_VALIDATION_ENTRIES", 5.0)
        ),

        # The exit levers. Every key above decides what may be *bought*; before
        # this, nothing recorded what governed the sell, so no archived day could
        # answer "which exit rules were live?".
        #
        # It cost an hour on 2026-08-22. NVDA on 08-21 peaked at 1.87R and booked
        # 0.20R, and settling whether the profit ladder had been on at the time
        # meant reading a comment in an untracked .env rather than the run's own
        # record. The ladder was in fact switched on later that same day, using
        # that trade as part of its evidence.
        #
        # Four of these default ON in code and were deliberately shipped off by
        # env on 2026-08-19, which is exactly the state a snapshot must capture:
        # reading the code tells you the default, not what ran.
        "exit_profit_ladder": _safe(lambda: os.getenv("EXIT_PROFIT_LADDER", "")),
        "exit_trail_arm_r": _safe(lambda: get_float_env("EXIT_TRAIL_ARM_R", 2.0)),
        "exit_breakeven_trigger_r": _safe(
            lambda: get_float_env("EXIT_BREAKEVEN_TRIGGER_R", 1.0)
        ),
        "exit_breakeven_on_peak": _safe(
            lambda: get_bool_env("EXIT_BREAKEVEN_ON_PEAK", False)
        ),
        "exit_option_giveback_arm_pct": _safe(
            lambda: get_float_env("EXIT_OPTION_GIVEBACK_ARM_PCT", 0.0)
        ),
        "soft_exit_hold_enabled": _safe(
            lambda: get_bool_env("SOFT_EXIT_HOLD_ENABLED", False)
        ),
        "exit_structure_trail_enabled": _safe(
            lambda: get_bool_env("EXIT_STRUCTURE_TRAIL_ENABLED", False)
        ),
        "exit_target_extend_enabled": _safe(
            lambda: get_bool_env("EXIT_TARGET_EXTEND_ENABLED", False)
        ),
        "exit_momentum_enabled": _safe(
            lambda: get_bool_env("EXIT_MOMENTUM_ENABLED", True)
        ),
        # Which process owns the 20-second exit pass, and what it may fire on.
        "position_monitor_enabled": _safe(
            lambda: get_bool_env("POSITION_MONITOR_ENABLED", False)
        ),
        "position_monitor_momentum_enabled": _safe(
            lambda: get_bool_env("POSITION_MONITOR_MOMENTUM_ENABLED", False)
        ),
        # Target geometry, which decides the reward half of every RR the gates
        # above judge. On at 2 in production while the impact map said otherwise.
        "target_min_rr": _safe(lambda: get_float_env("TARGET_MIN_RR", 0.0)),
        "target_max_reward_atr": _safe(
            lambda: get_float_env("TARGET_MAX_REWARD_ATR", 2.5)
        ),
    }

    return {"config": snapshot}
