"""The test suite must not be able to write to the production database.

There were already two guards: tests/__init__ blanks DATABASE_URL and
DB_WRITE_ENABLED before any app import, and BestEffortRepository checks
db_writes_enabled() before executing. On 2026-08-03 four full-suite runs still
put four `failure_reason='boom'` rows into the production telegram_dispatch
table, one per run, and the route was never identified.

What both existing guards have in common is that they answer from os.environ,
which stays mutable for the whole process and -- because the repository checks
at execution time, not submit time -- is read inside RuntimeScheduler worker
threads long after the caller queued the job. These tests pin the property that
closes that class: once armed, nothing restores the ability to write.
"""

import os
import threading
import unittest

from app.db import persistence


class WriteKillSwitchTests(unittest.TestCase):

    def test_it_is_already_armed_by_the_test_package(self):
        """If this fails, every other test in the suite can reach production."""

        self.assertTrue(persistence._WRITES_HARD_DISABLED)
        self.assertFalse(persistence.db_writes_enabled())

    def test_restoring_the_environment_does_not_re_enable_writes(self):
        """The exact shape of the failure: env comes back, writes must not."""

        previous = {
            "DB_WRITE_ENABLED": os.environ.get("DB_WRITE_ENABLED"),
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
        }

        os.environ["DB_WRITE_ENABLED"] = "true"
        os.environ["DATABASE_URL"] = "postgresql+psycopg2://production/db"

        try:

            self.assertFalse(
                persistence.db_writes_enabled(),
                "a restored environment re-enabled production writes",
            )

        finally:

            for key, value in previous.items():

                if value is None:

                    os.environ.pop(key, None)

                else:

                    os.environ[key] = value

    def test_a_deferred_write_on_another_thread_is_still_refused(self):
        """RuntimeScheduler runs the write later, on its own thread."""

        os.environ["DB_WRITE_ENABLED"] = "true"
        os.environ["DATABASE_URL"] = "postgresql+psycopg2://production/db"

        answers = []

        def worker():

            answers.append(persistence.db_writes_enabled())

        try:

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=5)

            self.assertEqual(answers, [False])

        finally:

            os.environ["DB_WRITE_ENABLED"] = "false"
            os.environ["DATABASE_URL"] = ""

    def test_the_repository_write_path_refuses(self):
        """The guard has to hold where rows are actually inserted."""

        from app.db.repository_base import BestEffortRepository

        written = BestEffortRepository()._execute(
            "INSERT INTO telegram_dispatch (scan_id) VALUES (:scan_id)",
            {"scan_id": "SHOULD_NEVER_BE_WRITTEN"},
        )

        self.assertEqual(written, 0)


if __name__ == "__main__":

    unittest.main()
