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
# telemetry/ resolved as a bare relative path and so was never covered by the two
# roots above: any test reaching close_paper_trade appended real rows to the
# tracked telemetry/trade_telemetry.csv.
os.environ.setdefault("DRAVYA_TELEMETRY_DIR", os.path.join(_SANDBOX, "telemetry"))


def _disable_db_writes():
    """Assigned, not setdefault: the point is to override what .env supplies."""

    os.environ["DB_WRITE_ENABLED"] = "false"
    os.environ["DATABASE_URL"] = ""
    os.environ["DATABASE_DIRECT_URL"] = ""


_disable_db_writes()

_real_load_dotenv = dotenv.load_dotenv


def _load_dotenv_keeping_db_disabled(*args, **kwargs):
    """Let .env load, then put the database kill switch back.

    `app/config/settings.py` and `app/utils/polygon_client.py` used to call
    `load_dotenv(override=True)` at import, which copied the real DATABASE_URL
    and DB_WRITE_ENABLED=true back over anything set here; `db_writes_enabled()`
    reads os.environ at call time, so that restored value was what a test's
    write path saw. Both now load with `override=False`, so the kill switch set
    above already survives.

    Kept anyway. It costs nothing, and it is the only thing standing between a
    stray `load_dotenv(override=True)` somewhere in the tree and a test suite
    writing to the production database. Belt and braces on the one failure whose
    blast radius is real money.

    Wrapping rather than no-oping keeps every other .env value available, so test
    behaviour is unchanged apart from the database being unreachable.
    """

    result = _real_load_dotenv(*args, **kwargs)
    _disable_db_writes()
    return result


dotenv.load_dotenv = _load_dotenv_keeping_db_disabled


# Imported last, so the env above and the patch above are both already in place
# when app.db.connection runs its own load_dotenv at import.
#
# The env kill switch is necessary and was not sufficient. On 2026-08-03 four
# full-suite runs put four `failure_reason='boom'` rows into the production
# telegram_dispatch table with all of the above already in force, and the route
# was never identified -- so this closes the class rather than the instance. It
# is a module-level flag, not an environment variable, which is the point:
# db_writes_enabled() is read at execution time inside RuntimeScheduler threads,
# and no amount of .env reloading can turn this back on.
from app.db.persistence import disable_db_writes_for_process  # noqa: E402

disable_db_writes_for_process()


@atexit.register
def _cleanup_sandbox():

    shutil.rmtree(_SANDBOX, ignore_errors=True)
