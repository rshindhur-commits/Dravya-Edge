"""The weekend review, and the one rule it must never break.

`TELEGRAM_CHAT_ID` is the subscriber channel. This report is an internal research
note, so delivering it there would push operator notes to paying subscribers. The
delivery path therefore uses a separate variable and refuses the subscriber chat
outright, and that refusal is the test that matters most here.

Everything else is about not failing: this runs unattended from Task Scheduler,
where an exception loses the summary silently.
"""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import mover_watch


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setattr(mover_watch, "LOG_DIR", tmp_path)
    return tmp_path


def _write(directory, day, results):
    (directory / f"{day}.json").write_text(
        json.dumps({"day": day, "results": results})
    )


class TestBuildReview:

    def test_no_logs_yields_none(self, logs):
        assert mover_watch.build_review() is None

    def test_counts_across_days(self, logs):
        _write(logs, "2026-08-14", {
            "AAOI": {"verdict": "CHAIN TOO WIDE", "needs_ceiling": 6},
            "NVDA": {"verdict": "TRADED"},
        })
        _write(logs, "2026-08-15", {
            "AAOI": {"verdict": "CHAIN TOO WIDE", "needs_ceiling": 4},
        })

        text = mover_watch.build_review()

        assert "2 sessions logged" in text
        # The LOWEST ceiling ever seen is the one worth acting on.
        assert "4%" in text
        assert "AAOI" in text and "NVDA" in text

    def test_a_corrupt_log_does_not_sink_the_others(self, logs):
        _write(logs, "2026-08-14", {"AAOI": {"verdict": "TRADED"}})
        (logs / "2026-08-15.json").write_text("{ not json")

        text = mover_watch.build_review()

        assert text is not None and "AAOI" in text

    def test_logs_with_no_results_yield_none(self, logs):
        _write(logs, "2026-08-14", {})
        assert mover_watch.build_review() is None


class TestDelivery:

    def test_unset_chat_is_silent_and_not_an_error(self, monkeypatch):
        monkeypatch.delenv("MOVER_WATCH_TELEGRAM_CHAT_ID", raising=False)
        assert mover_watch._deliver("anything") is None

    def test_it_refuses_the_subscriber_channel(self, monkeypatch):
        """The rule this module exists to protect."""

        monkeypatch.setenv("MOVER_WATCH_TELEGRAM_CHAT_ID", "-100999")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")

        problem = mover_watch._deliver("internal note")

        assert problem and "subscriber channel" in problem

    def test_missing_token_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setenv("MOVER_WATCH_TELEGRAM_CHAT_ID", "-100123")
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

        assert "TELEGRAM_BOT_TOKEN" in mover_watch._deliver("x")

    def test_a_transport_failure_is_returned_not_raised(self, monkeypatch):
        monkeypatch.setenv("MOVER_WATCH_TELEGRAM_CHAT_ID", "-100123")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100999")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")

        import requests

        def explode(*_args, **_kwargs):
            raise requests.ConnectionError("no network")

        monkeypatch.setattr(requests, "post", explode)

        problem = mover_watch._deliver("x")

        assert problem and "delivery failed" in problem


class TestReviewCommand:

    def test_it_writes_a_dated_copy(self, logs, monkeypatch, capsys):
        monkeypatch.delenv("MOVER_WATCH_TELEGRAM_CHAT_ID", raising=False)
        _write(logs, "2026-08-14", {"AAOI": {"verdict": "TRADED"}})

        mover_watch.review()

        written = list((logs / "review").glob("*.txt"))
        assert len(written) == 1
        assert "AAOI" in written[0].read_text()

    def test_it_survives_an_empty_log_directory(self, logs, capsys):
        mover_watch.review()
        assert "nothing logged yet" in capsys.readouterr().out
