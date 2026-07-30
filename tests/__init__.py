"""Test package.

Redirects every storage root to a throwaway directory BEFORE any app module is
imported. Without this, running the suite writes into the real trading
artifacts: test decisions landed in app/state/auto_paper_decision_log.json and
data/daily/<day>/auto_paper_decisions.csv, which the daily review reads.

app.storage.daily_paths resolves DRAVYA_DATA_DIR / DRAVYA_STATE_DIR once at
import, so these must be set here, at package import, and not inside a test.
"""

import atexit
import os
import shutil
import tempfile


_SANDBOX = tempfile.mkdtemp(prefix="dravya-tests-")

os.environ.setdefault("DRAVYA_DATA_DIR", os.path.join(_SANDBOX, "data"))
os.environ.setdefault("DRAVYA_STATE_DIR", os.path.join(_SANDBOX, "state"))


@atexit.register
def _cleanup_sandbox():

    shutil.rmtree(_SANDBOX, ignore_errors=True)
