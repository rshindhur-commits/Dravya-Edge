"""Which settings must exist in Render, and which are safe to leave unset?

`.env` is this laptop. Render keeps its own environment, and any setting whose
**code default** differs from the intended value must be set there explicitly or
production quietly runs something else. That failure is silent by construction:
nothing errors, the app just trades to different rules than the ones measured.

    python tools/render_env_check.py

Compares `.env` against the value the code uses when the variable is absent, and
splits the settings into the ones that must be carried to Render and the ones
that are already correct without being set.

## The trap this tool exists to avoid

`app/config/settings.py` calls `load_dotenv()` at import. So popping variables
and *then* importing the app does nothing -- dotenv refills them, every setting
compares equal to "its default", and the report says nothing needs setting. The
first version of this check did exactly that and reported 1 variable instead of
16. `dotenv.load_dotenv` is stubbed out below before any app import; do not
remove that.

## What it cannot tell you

**What is actually set in Render right now.** There is no API call here. This
produces the list that *must* be there; verifying it is a manual comparison
against the Render dashboard. See CONFIG_CHANGELOG.md for why that matters --
an unrecorded Render change cost a full session on 2026-08-10.
"""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values

ROOT = pathlib.Path(__file__).resolve().parents[1]
WANTED = dotenv_values(ROOT / ".env")

# Everything that changes trading behaviour. Secrets are deliberately absent:
# this output is meant to be pasteable into a chat or a ticket.
KEYS = [
    "OPTION_MAX_SPREAD_PCT",
    "OPTION_MAX_CONTRACT_COST",
    "OPTION_PREFERRED_MAX_CONTRACT_COST",
    "OPTION_AGGRESSIVE_MAX_CONTRACT_COST",
    "OPTION_MIN_CONTRACT_COST",
    "OPTION_MAX_CONTRACT_COST_BY_SYMBOL",
    "OPTION_MIN_OPEN_INTEREST",
    "OPTION_MIN_VOLUME",
    "OPTION_MIN_DTE",
    "OPTION_MAX_DTE",
    "OPTION_PREFERRED_MIN_DTE",
    "OPTION_PREFERRED_MAX_DTE",
    "OPTION_MIN_QUALITY_SCORE",
    "OPTION_PREFER_TIGHTEST_QUALIFIED",
    "OPTION_CAPITAL_PROFILE",
    "ENTRY_LATE_SESSION_CUTOFF_ET",
    "ENTRY_MIN_TRADE_QUALITY",
    "ENTRY_TIMING_MAX_SCORE",
    "EXIT_MOMENTUM_ENABLED",
    "EXIT_VOLUME_FLUSH_ENABLED",
    "EXIT_FLUSH_ARM_PCT",
    "EXIT_FLUSH_VOLUME_MULT",
    "EXIT_OPTION_BREAKEVEN_ARM_PCT",
    "EXIT_OPTION_GIVEBACK_ARM_PCT",
    "EXIT_OPTION_GIVEBACK_KEEP",
    "IV_RICHNESS_ENFORCE",
    "MAX_ACTIVE_PAPER_TRADES",
    "MAX_ACTIVE_PER_DIRECTION",
    "MAX_DAILY_ENTRIES",
    "MAX_TRADES_PER_SYMBOL_PER_DAY",
    "AUTO_PAPER_SYMBOL_COOLDOWN_MINUTES",
    "DAILY_START_CAPITAL",
    "DAILY_CONTEXT_ENABLED",
]

# Must happen before any app import. See the docstring.
import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *a, **k: False

import dotenv.main  # noqa: E402

dotenv.main.load_dotenv = lambda *a, **k: False

for _key in KEYS:
    os.environ.pop(_key, None)

from app.config.settings import (  # noqa: E402
    get_bool_env,
    get_float_env,
    get_int_env,
    settings,
)
from app.exit import exit_engine as ex  # noqa: E402
from app.gates.entry_gate import (  # noqa: E402
    late_session_cutoff_et,
    min_trade_quality_score,
)
from app.options.affordability_config import (  # noqa: E402
    get_affordability_config,
    per_symbol_cost_caps,
)


def defaults():
    config = get_affordability_config()

    return {
        "OPTION_MAX_SPREAD_PCT": get_float_env("OPTION_MAX_SPREAD_PCT", 6.0),
        "OPTION_MAX_CONTRACT_COST": config["max_contract_cost"],
        "OPTION_PREFERRED_MAX_CONTRACT_COST": config["preferred_max_contract_cost"],
        "OPTION_AGGRESSIVE_MAX_CONTRACT_COST": config["aggressive_max_contract_cost"],
        "OPTION_MIN_CONTRACT_COST": config["min_contract_cost"],
        "OPTION_MAX_CONTRACT_COST_BY_SYMBOL": per_symbol_cost_caps() or "(none)",
        "OPTION_MIN_OPEN_INTEREST": settings.option_min_open_interest,
        "OPTION_MIN_VOLUME": settings.option_min_volume,
        "OPTION_MIN_DTE": settings.option_min_dte,
        "OPTION_MAX_DTE": settings.option_max_dte,
        "OPTION_PREFERRED_MIN_DTE": settings.option_preferred_min_dte,
        "OPTION_PREFERRED_MAX_DTE": settings.option_preferred_max_dte,
        "OPTION_MIN_QUALITY_SCORE": settings.option_min_quality_score,
        "OPTION_PREFER_TIGHTEST_QUALIFIED": get_bool_env(
            "OPTION_PREFER_TIGHTEST_QUALIFIED", True
        ),
        "OPTION_CAPITAL_PROFILE": config["profile_name"],
        "ENTRY_LATE_SESSION_CUTOFF_ET": late_session_cutoff_et(),
        "ENTRY_MIN_TRADE_QUALITY": min_trade_quality_score(),
        "ENTRY_TIMING_MAX_SCORE": get_float_env("ENTRY_TIMING_MAX_SCORE", 70.0),
        "EXIT_MOMENTUM_ENABLED": ex._momentum_exits_allowed("INTRADAY", 0),
        "EXIT_VOLUME_FLUSH_ENABLED": ex.volume_flush_enabled(),
        "EXIT_FLUSH_ARM_PCT": ex.volume_flush_arm_pct(),
        "EXIT_FLUSH_VOLUME_MULT": get_float_env("EXIT_FLUSH_VOLUME_MULT", 1.5),
        "EXIT_OPTION_BREAKEVEN_ARM_PCT": ex.option_breakeven_arm_pct(),
        "EXIT_OPTION_GIVEBACK_ARM_PCT": ex.option_giveback_arm_pct(),
        "EXIT_OPTION_GIVEBACK_KEEP": ex.option_giveback_keep(),
        "IV_RICHNESS_ENFORCE": get_bool_env("IV_RICHNESS_ENFORCE", False),
        "MAX_ACTIVE_PAPER_TRADES": config["max_active_trades"],
        "MAX_ACTIVE_PER_DIRECTION": get_int_env("MAX_ACTIVE_PER_DIRECTION", 1),
        "MAX_DAILY_ENTRIES": config["max_daily_entries"],
        "MAX_TRADES_PER_SYMBOL_PER_DAY": get_int_env(
            "MAX_TRADES_PER_SYMBOL_PER_DAY", 1
        ),
        "AUTO_PAPER_SYMBOL_COOLDOWN_MINUTES": get_int_env(
            "AUTO_PAPER_SYMBOL_COOLDOWN_MINUTES", 60
        ),
        "DAILY_START_CAPITAL": config["daily_start_capital"],
        "DAILY_CONTEXT_ENABLED": get_bool_env("DAILY_CONTEXT_ENABLED", True),
    }


def same(a, b):
    a, b = str(a).strip().lower(), str(b).strip().lower()

    if a == b:
        return True

    try:
        return abs(float(a) - float(b)) < 1e-9
    except ValueError:
        pass

    truthy, falsy = {"true", "1", "yes", "on"}, {"false", "0", "no", "off"}

    return (a in truthy and b in truthy) or (a in falsy and b in falsy)


def main():

    code = defaults()
    must, safe, unset = [], [], []

    for key in KEYS:
        wanted = WANTED.get(key)

        if wanted is None:
            unset.append((key, code[key]))
        elif same(wanted, code[key]):
            safe.append((key, wanted))
        else:
            must.append((key, wanted, code[key]))

    print(f"\n  MUST BE SET IN RENDER -- the code default is different ({len(must)})")
    print("  Missing any of these means production trades to other rules.\n")

    for key, wanted, _ in must:
        print(f"    {key}={wanted}")

    print(f"\n  {'key':38}{'must be':>30}{'or you get':>18}")
    print(f"  {'':-<86}")

    for key, wanted, default in must:
        print(f"  {key:38}{str(wanted):>30}{str(default):>18}")

    print(f"\n\n  ALREADY CORRECT WITHOUT BEING SET ({len(unset)})")
    print("  Not in .env, and the code default is the intended value.\n")

    for key, default in unset:
        print(f"    {key:40} {default}")

    if safe:
        print(f"\n\n  IN .env BUT REDUNDANT ({len(safe)})")
        print("  Same as the code default; setting them in Render is harmless.\n")
        for key, wanted in safe:
            print(f"    {key:40} {wanted}")

    print("\n  This cannot see Render. It produces the list that must be there;")
    print("  comparing it to the dashboard is manual, and CONFIG_CHANGELOG.md")
    print("  records what a missed one costs.\n")


if __name__ == "__main__":
    main()
