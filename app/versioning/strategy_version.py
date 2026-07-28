"""Strategy version stamping (S2.5).

`compute_strategy_version()` fingerprints exactly what invariant I1 calls "V1
decision logic" -- so that this version changes if and only if a diff would
have been out of scope under I1 -- plus the one piece of decision-relevant
config that lives outside those files.

See docs/specs/S2.5-strategy-version.md for the full rationale, what is
deliberately excluded, and the CI gate this feeds.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]

# Exactly I1's list (momentum_strategy, entry_engine, risk_manager,
# exit_engine) plus entry_gate.py -- the shared price-geometry invariant and
# gate every entry decision passes through (ARCHITECTURE.md §2, §6). Any
# change to these files is, by I1's own definition, a V1 decision-logic
# change. This list IS the scope of "V1 decision logic" for versioning
# purposes -- extend it only if I1's definition itself is amended.
V1_DECISION_LOGIC_FILES = [
    "app/strategies/momentum_strategy.py",
    "app/strategies/entry_engine.py",
    "app/risk/risk_manager.py",
    "app/exit/exit_engine.py",
    "app/gates/entry_gate.py",
]

UNVERSIONED = "v0-unversioned"


def _file_bytes_hash(files=None):

    files = files if files is not None else V1_DECISION_LOGIC_FILES
    hasher = hashlib.sha256()

    for rel_path in files:

        path = ROOT_DIR / rel_path
        hasher.update(rel_path.encode("utf-8"))
        hasher.update(path.read_bytes())

    return hasher.hexdigest()


def _decision_relevant_config():

    """The one decision-relevant config value that lives outside
    V1_DECISION_LOGIC_FILES: main.py's SCANNER_ENTRY_GATE_CONFIG, which gates
    ENTER/ENTER_PAPER action status. Every other decision threshold found
    during S2.5's audit (RR_MIN_THRESHOLD, ATR multipliers, the EMA_PULLBACK
    stop floor, entry_gate.py's own EntryGateConfig defaults) is hardcoded
    inside a file already in V1_DECISION_LOGIC_FILES, so the file-bytes hash
    already captures it -- duplicating it here would be redundant, not safer.
    """

    from app.main import SCANNER_ENTRY_GATE_CONFIG

    config = SCANNER_ENTRY_GATE_CONFIG

    return {
        "scanner_entry_gate.min_rr": config.min_rr,
        "scanner_entry_gate.min_setup_percent": config.min_setup_percent,
        "scanner_entry_gate.min_option_quality": config.min_option_quality,
        "scanner_entry_gate.max_spread_pct": config.max_spread_pct,
    }


_cache = {}


def compute_strategy_version(use_cache=True):

    """A short (12 hex char) fingerprint of V1 decision logic + config.

    Cached per-process by default: these files and this config do not change
    while a process runs (config loads at import with `override=True` --
    changing it already requires a restart, per CLAUDE.md conventions), so
    re-hashing 5 files and importing app.main on every trade/alert/evidence
    row would be pure waste. Pass `use_cache=False` to force recomputation
    (tests; a long-running process that wants to detect an on-disk change
    without restarting, though nothing in this app currently needs that).
    """

    if use_cache and "version" in _cache:

        return _cache["version"]

    logic_hash = _file_bytes_hash()
    config_snapshot = _decision_relevant_config()
    config_json = json.dumps(config_snapshot, sort_keys=True, separators=(",", ":"))

    combined = hashlib.sha256(
        (logic_hash + config_json).encode("utf-8")
    ).hexdigest()

    version = combined[:12]

    if use_cache:

        _cache["version"] = version

    return version


def strategy_version_manifest():

    """Full detail behind the short version, for audit/debugging and for the
    CI gate -- not for stamping records with (use compute_strategy_version()
    for that; this recomputes without the cache so it always reflects the
    files on disk right now).
    """

    logic_hash = _file_bytes_hash()
    config_snapshot = _decision_relevant_config()

    return {
        "strategy_version": compute_strategy_version(use_cache=False),
        "logic_files": list(V1_DECISION_LOGIC_FILES),
        "logic_hash": logic_hash,
        "decision_relevant_config": config_snapshot,
    }
