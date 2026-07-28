"""S2.5 -- the CI code gate for I1 ("no V1 decision logic changes").

Computes the current strategy_version (a hash of exactly the files I1 names
as V1 decision logic, plus the one piece of decision-relevant config outside
them -- see app/versioning/strategy_version.py) and fails if that hash is not
in the checked-in approval list.

This converts "the validation freeze is documentation discipline, not a code
gate" (METRICS.md §5, prior to S2.5) into an actual CI failure: a diff that
changes V1 decision logic changes the hash, and an unreviewed hash fails the
build. The only way past it is to add the new hash to
app/versioning/approved_strategy_versions.json in the same PR -- an explicit,
visible, reviewable act, not a silent pass.

This gate says nothing about whether a V1 logic change is a *good* idea --
that judgment is the reviewer's, exercised by choosing whether to approve the
new hash. It only guarantees the change cannot land unnoticed.

Usage:
    python tools/check_strategy_version_approved.py
Exit code 0 if the current version is approved, 1 otherwise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from app.versioning.strategy_version import (  # noqa: E402
    strategy_version_manifest,
)


APPROVAL_FILE = ROOT_DIR / "app" / "versioning" / "approved_strategy_versions.json"


def load_approved_versions():

    if not APPROVAL_FILE.exists():

        return []

    with APPROVAL_FILE.open("r", encoding="utf-8") as handle:

        data = json.load(handle)

    return [entry["strategy_version"] for entry in data.get("approved", [])]


def main():

    manifest = strategy_version_manifest()
    current = manifest["strategy_version"]
    approved = load_approved_versions()

    if current in approved:

        print(f"strategy_version {current} is approved.")
        sys.exit(0)

    print(f"strategy_version {current} is NOT in the approved list.")
    print()
    print("This means a diff touched one of:")

    for path in manifest["logic_files"]:

        print(f"  - {path}")

    print("  - or app.main.SCANNER_ENTRY_GATE_CONFIG")
    print()
    print(
        "If this is an intentional, reviewed V1 decision-logic change, add "
        "an entry to app/versioning/approved_strategy_versions.json:"
    )
    print()
    print(json.dumps({
        "strategy_version": current,
        "approved_at": "<date>",
        "note": "<why this change is approved -- PR link, reviewer, rationale>",
    }, indent=2))
    print()
    print(
        "If this is NOT an intentional V1 change, the diff is out of scope "
        "under invariant I1 (docs/EXECUTION_PLAN.md, section 1) and should be "
        "reverted or moved behind a flag."
    )

    sys.exit(1)


if __name__ == "__main__":

    main()
