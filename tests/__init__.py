"""Test package.

Isolates the suite from real trading artifacts BEFORE any app module is imported.

Storage roots are redirected to a throwaway directory. Without this, test
decisions landed in app/state/auto_paper_decision_log.json and
data/daily/<day>/auto_paper_decisions.csv, which the daily review reads.

Database writes are disabled outright. Filesystem redirection alone was not
enough: `.env` carries a live DATABASE_URL with DB_WRITE_ENABLED=true, so a plain
`pytest` run inserted fixture rows straight into the production Neon database --
`alert_events` and `telegram_dispatch` collected SPCX alert rows and dispatch rows
with `failure_reason='boom'` from tests/test_telegram_dispatcher.py. Tests that
need database behaviour should stub the persistence layer or point at their own
throwaway database explicitly.

app.storage.daily_paths and the settings loader both resolve these once at import,
so they must be set here, at package import, and not inside a test.
"""

import atexit
import os
import shutil
import tempfile

import dotenv


_SANDBOX = tempfile.mkdtemp(prefix="dravya-tests-")

os.environ.setdefault("DRAVYA_DATA_DIR", os.path.join(_SANDBOX, "data"))
os.environ.setdefault("DRAVYA_STATE_DIR", os.path.join(_SANDBOX, "state"))


def _disable_db_writes():
    """Assigned, not setdefault: the point is to override what .env supplies."""

    os.environ["DB_WRITE_ENABLED"] = "false"
    os.environ["DATABASE_URL"] = ""
    os.environ["DATABASE_DIRECT_URL"] = ""


_disable_db_writes()

_real_load_dotenv = dotenv.load_dotenv


def _load_dotenv_keeping_db_disabled(*args, **kwargs):
    """Let .env load, then put the database kill switch back.

    Clearing the environment once is not enough. app/config/settings.py and
    app/utils/polygon_client.py both call `load_dotenv(override=True)` at import,
    which copies the real DATABASE_URL and DB_WRITE_ENABLED=true back over
    anything set here. `db_writes_enabled()` reads os.environ at call time, so
    that restored value is what a test's write path saw.

    Wrapping rather than no-oping keeps every other .env value available, so test
    behaviour is unchanged apart from the database being unreachable.
    """

    result = _real_load_dotenv(*args, **kwargs)
    _disable_db_writes()
    return result


dotenv.load_dotenv = _load_dotenv_keeping_db_disabled


@atexit.register
def _cleanup_sandbox():

    shutil.rmtree(_SANDBOX, ignore_errors=True)
