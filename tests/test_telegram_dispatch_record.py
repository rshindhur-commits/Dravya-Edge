"""A delivered alert has to leave a durable record that it was delivered.

`telegram_response` binds into `CAST(:telegram_response AS JSONB)`, which needs a
JSON string. A successful send hands back Telegram's reply as a nested dict,
psycopg2 raises "can't adapt type 'dict'", and `BestEffortRepository` swallows
it -- so from 2026-07-23 every DELIVERED row was dropped while ATTEMPTED and
FAILED, whose response is None, wrote fine.

The table ended up holding 255 ATTEMPTED against 2 DELIVERED, which reads as a
total delivery failure on days when every alert landed. The delivery was fine;
the record was not, and the Postmortem tab's alert section was built on it.
"""

import json
import unittest
from unittest.mock import patch

from app.db.telegram_dispatch_repository import TelegramDispatchRepository


class ResponseSerialisationTests(unittest.TestCase):

    def _captured(self, row):
        seen = {}

        def capture(_statement, params):
            seen.update(params)
            return 1

        with patch.object(TelegramDispatchRepository, "_execute", side_effect=capture):
            TelegramDispatchRepository().insert(row)

        return seen

    def test_a_nested_response_is_serialised_rather_than_bound_as_a_dict(self):
        """The exact shape a real send returns."""

        params = self._captured({
            "status": "DELIVERED",
            "telegram_response": {"ok": True, "result": {"message_id": 7}},
        })

        self.assertIsInstance(params["telegram_response"], str)
        self.assertEqual(json.loads(params["telegram_response"])["result"]["message_id"], 7)

    def test_none_stays_none_so_attempted_rows_are_unchanged(self):
        """ATTEMPTED and FAILED rows always worked, and must keep working --
        a JSON "null" string is not the same as SQL NULL."""

        params = self._captured({"status": "ATTEMPTED", "telegram_response": None})

        self.assertIsNone(params["telegram_response"])

    def test_a_string_response_is_passed_through_unchanged(self):
        """Already-encoded JSON must not be double-encoded into a quoted string."""

        params = self._captured({
            "status": "DELIVERED",
            "telegram_response": '{"ok": true}',
        })

        self.assertEqual(params["telegram_response"], '{"ok": true}')

    def test_an_unserialisable_value_does_not_lose_the_row(self):
        """`default=str` matters: a datetime in the reply must not put the row
        back on the path that dropped it."""

        from datetime import datetime

        params = self._captured({
            "status": "DELIVERED",
            "telegram_response": {"at": datetime(2026, 8, 2, 10, 0)},
        })

        self.assertIn("2026-08-02", params["telegram_response"])

    def test_the_caller_s_row_is_not_mutated(self):
        """The dispatcher reuses its metadata across retries."""

        row = {"status": "DELIVERED", "telegram_response": {"ok": True}}
        self._captured(row)

        self.assertIsInstance(row["telegram_response"], dict)


if __name__ == "__main__":
    unittest.main()
