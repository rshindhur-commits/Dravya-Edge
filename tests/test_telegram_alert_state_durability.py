"""Alert dedup has to outlive the container.

`telegram_alert_state.json` is gitignored and sits on an ephemeral filesystem, so
every restart emptied it and every dedup key reset — subscribers received the
day's review alerts again, and from 2026-08-01 potentially a second copy of the
weekly results. The always-on worker makes restarts routine, so the state is now
mirrored to Postgres and hydrated back once per process.

The file stays the hot path: `alert_was_sent` runs once per candidate per scan.
"""

import unittest
from unittest.mock import patch

import app.alerts.telegram_alerts as alerts


class _StateHarness(unittest.TestCase):
    """Each test starts as a fresh process with a chosen file state."""

    def setUp(self):
        alerts._alert_state_hydrated = False
        self.saved = []

    def tearDown(self):
        alerts._alert_state_hydrated = False

    def _run(self, file_state, db_state, db_error=None):
        repository = "app.db.telegram_alert_state_repository.TelegramAlertStateRepository"

        with patch.object(
            alerts, "load_json_file", return_value=file_state
        ), patch.object(
            alerts, "_save_alert_state", side_effect=self.saved.append
        ), patch(
            f"{repository}.prune"
        ) as prune, patch(
            f"{repository}.fetch_recent",
            side_effect=db_error or (lambda *_a, **_k: db_state),
        ):
            state = alerts._load_alert_state()
            self.pruned = prune.call_count

            return state


class HydrationTests(_StateHarness):

    def test_a_wiped_file_recovers_its_keys_from_the_database(self):
        """The restart case. Without this the alert goes out twice."""

        state = self._run({"sent": {}}, {"REVIEW_NVDA": {"symbol": "NVDA"}})

        self.assertIn("REVIEW_NVDA", state["sent"])
        self.assertTrue(self.saved, "recovered keys were not written back to the file")

    def test_local_state_wins_for_keys_it_already_has(self):
        """The database copy is written best-effort and can lag. Hydration must
        never overwrite fresher local state -- same rule as
        `restore_open_trades_from_db`."""

        state = self._run(
            {"sent": {"KEY": {"symbol": "LOCAL"}}},
            {"KEY": {"symbol": "STALE_DB"}},
        )

        self.assertEqual(state["sent"]["KEY"]["symbol"], "LOCAL")

    def test_nothing_to_adopt_does_not_rewrite_the_file(self):

        self._run({"sent": {"KEY": {}}}, {"KEY": {}})

        self.assertFalse(self.saved)

    def test_hydration_runs_once_per_process(self):
        """`alert_was_sent` is called per candidate per scan. Hydrating on every
        read would turn the dedup check into a database round trip."""

        repository = "app.db.telegram_alert_state_repository.TelegramAlertStateRepository"

        with patch.object(
            alerts, "load_json_file", return_value={"sent": {}}
        ), patch.object(
            alerts, "_save_alert_state"
        ), patch(f"{repository}.fetch_recent", return_value={}) as fetch:

            for _ in range(5):
                alerts._load_alert_state()

        self.assertEqual(fetch.call_count, 1)

    def test_a_database_outage_leaves_the_local_file_working(self):
        """Degrades to today's behaviour rather than failing the scan."""

        state = self._run(
            {"sent": {"KEY": {}}}, None, db_error=RuntimeError("neon down")
        )

        self.assertIn("KEY", state["sent"])

    def test_a_corrupt_file_still_hydrates(self):

        state = self._run("not a dict", {"KEY": {"symbol": "NVDA"}})

        self.assertIn("KEY", state["sent"])

    def test_old_keys_are_pruned_once_per_process(self):
        """Keys too old to dedup anything would otherwise accumulate forever."""

        self._run({"sent": {}}, {})

        self.assertEqual(self.pruned, 1)

    def test_a_database_outage_does_not_attempt_a_prune(self):

        self._run({"sent": {}}, None, db_error=RuntimeError("neon down"))

        self.assertEqual(self.pruned, 0)


class MirrorTests(unittest.TestCase):

    def setUp(self):
        alerts._alert_state_hydrated = True

    def tearDown(self):
        alerts._alert_state_hydrated = False

    def test_marking_sent_writes_through_to_the_database(self):

        with patch.object(
            alerts, "_load_alert_state", return_value={"sent": {}}
        ), patch.object(alerts, "_save_alert_state"), patch(
            "app.db.telegram_alert_state_repository.TelegramAlertStateRepository.upsert"
        ) as upsert:

            alerts.mark_alert_sent("KEY", {"symbol": "NVDA", "event_type": "REVIEW"})

        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.args[0], "KEY")
        self.assertEqual(upsert.call_args.args[1]["symbol"], "NVDA")
        self.assertIn("sent_at", upsert.call_args.args[1])

    def test_a_failed_mirror_never_fails_the_send(self):
        """The file write has already happened. A database outage costs
        durability across a restart, not correctness inside this container."""

        state = {"sent": {}}

        with patch.object(
            alerts, "_load_alert_state", return_value=state
        ), patch.object(alerts, "_save_alert_state"), patch(
            "app.db.telegram_alert_state_repository.TelegramAlertStateRepository.upsert",
            side_effect=RuntimeError("neon down"),
        ):

            alerts.mark_alert_sent("KEY", {"symbol": "NVDA"})

        self.assertIn("KEY", state["sent"])

    def test_closing_an_alert_mirrors_the_same_match_to_the_database(self):

        state = {
            "sent": {
                "K1": {"event_type": "ENTRY", "symbol": "NVDA", "option_ticker": "O:X"},
            }
        }

        with patch.object(
            alerts, "_load_alert_state", return_value=state
        ), patch.object(alerts, "_save_alert_state"), patch(
            "app.db.telegram_alert_state_repository.TelegramAlertStateRepository.mark_closed"
        ) as mark_closed:

            alerts.mark_alert_closed("NVDA", option_ticker="O:X")

        self.assertTrue(state["sent"]["K1"]["closed"])
        mark_closed.assert_called_once()
        self.assertEqual(mark_closed.call_args.args[0], "NVDA")
        self.assertEqual(mark_closed.call_args.kwargs["option_ticker"], "O:X")
        # The local record and the database row must agree on the timestamp,
        # not drift by however long the write took.
        self.assertEqual(
            mark_closed.call_args.kwargs["closed_at"],
            state["sent"]["K1"]["closed_at"],
        )

    def test_a_failed_close_mirror_still_closes_locally(self):

        state = {"sent": {"K1": {"event_type": "ENTRY", "symbol": "NVDA"}}}

        with patch.object(
            alerts, "_load_alert_state", return_value=state
        ), patch.object(alerts, "_save_alert_state"), patch(
            "app.db.telegram_alert_state_repository.TelegramAlertStateRepository.mark_closed",
            side_effect=RuntimeError("neon down"),
        ):

            alerts.mark_alert_closed("NVDA")

        self.assertTrue(state["sent"]["K1"]["closed"])


if __name__ == "__main__":

    unittest.main()


class HydrationRetriesAfterAFailedReadTests(unittest.TestCase):
    """Giving up permanently on one bad read is how a container spends its whole
    life with an empty dedup set, re-sending everything it is asked to send."""

    def setUp(self):
        alerts._alert_state_hydrated = False
        self.addCleanup(
            setattr, alerts, "_alert_state_hydrated", False)

    def test_a_failed_read_does_not_mark_hydration_done(self):

        with patch("app.db.telegram_alert_state_repository."
                   "TelegramAlertStateRepository.fetch_recent", return_value=None):

            alerts._hydrate_alert_state_from_db({"sent": {}})

        self.assertFalse(alerts._alert_state_hydrated)

    def test_a_successful_read_marks_hydration_done_once(self):

        with patch("app.db.telegram_alert_state_repository."
                   "TelegramAlertStateRepository.fetch_recent",
                   return_value={"K": {"sent_at": "2026-08-01T00:00:00+00:00"}}), \
             patch("app.db.telegram_alert_state_repository."
                   "TelegramAlertStateRepository.prune"), \
             patch.object(alerts, "_save_alert_state"):

            state = alerts._hydrate_alert_state_from_db({"sent": {}})

        self.assertTrue(alerts._alert_state_hydrated)
        self.assertIn("K", state["sent"])

    def test_an_empty_result_is_not_a_failure(self):
        """No keys stored yet is a real state, and must not retry forever."""

        with patch("app.db.telegram_alert_state_repository."
                   "TelegramAlertStateRepository.fetch_recent", return_value={}), \
             patch("app.db.telegram_alert_state_repository."
                   "TelegramAlertStateRepository.prune"):

            alerts._hydrate_alert_state_from_db({"sent": {}})

        self.assertTrue(alerts._alert_state_hydrated)
