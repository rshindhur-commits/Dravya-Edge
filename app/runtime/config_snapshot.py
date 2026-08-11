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
    }

    return {"config": snapshot}
