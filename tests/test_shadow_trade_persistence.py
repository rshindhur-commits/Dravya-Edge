"""The V2 shadow needs storage that survives a restart.

It exists to A/B the V2 engines against the live ones and recorded **2 trades
across 23 days**, zero on each of the last 14, while shadow entries were firing
normally -- 8 of 53 recent paper trades suggested one and 3 were open.

The cause was storage, not logic: state lived only in
`app/state/entry_exit_v2_shadow_state.json` while paper trades are mirrored to
Postgres. On an ephemeral container that file does not survive a restart, and
`close_shadow_trade` pops the trade out of it, so a process dying between the
open and the close loses the result entirely.
"""

from unittest import mock

import pytest

from app.state import entry_exit_v2_shadow_state as shadow


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "SHADOW_STATE_FILE", tmp_path / "shadow.json")
    return tmp_path


@pytest.fixture
def mirrored():
    """Capture what would be queued to the database."""

    seen = []
    with mock.patch.object(shadow, "_mirror_to_db", side_effect=lambda t: seen.append(dict(t))):
        yield seen


class TestEveryWriteIsMirrored:

    def test_the_close_is_mirrored(self, isolated_state, mirrored):
        """The write that produces the final R, and the one the file cannot keep."""

        shadow.save_shadow_trades({"NVDA": {
            "symbol": "NVDA", "status": "OPEN", "direction": "PUT",
            "entry_price": 100.0, "entry_friction_r": 0.1,
        }})

        closed = shadow.close_shadow_trade(
            "NVDA", {"rr_progress": 1.4, "exit_phase": "TARGET"}, "2026-08-21T11:00:00", 97.0
        )

        assert closed["status"] == "CLOSED"
        assert [t["status"] for t in mirrored] == ["CLOSED"]
        assert mirrored[0]["final_r"] == 1.4

    def test_the_close_is_gone_from_the_file(self, isolated_state, mirrored):
        """`state.pop` is why the mirror is the only durable record of a result."""

        shadow.save_shadow_trades({"NVDA": {
            "symbol": "NVDA", "status": "OPEN", "entry_friction_r": 0.1,
        }})
        shadow.close_shadow_trade("NVDA", {"rr_progress": 1.0}, "2026-08-21T11:00:00", 97.0)

        assert "NVDA" not in shadow.load_shadow_trades()
        assert mirrored, "so if this had not been mirrored the result would not exist"


class TestRestore:

    def _rows(self, payloads):
        rows = [mock.Mock(symbol=p["symbol"], payload=p) for p in payloads]
        conn = mock.MagicMock()
        conn.__enter__.return_value.execute.return_value = rows
        engine = mock.Mock(connect=mock.Mock(return_value=conn))
        return mock.patch("app.db.connection.get_engine", return_value=engine)

    def test_an_open_position_survives_a_wiped_file(self, isolated_state):

        with self._rows([{"symbol": "AVGO", "status": "OPEN", "entry_price": 360.0}]):
            restored = shadow.restore_open_shadow_trades()

        assert restored == ["AVGO"]
        assert shadow.load_shadow_trades()["AVGO"]["entry_price"] == 360.0

    def test_the_file_wins_where_it_already_has_the_symbol(self, isolated_state):
        """The mirror is a queued best-effort copy and can lag."""

        shadow.save_shadow_trades({"AVGO": {"symbol": "AVGO", "status": "OPEN",
                                            "entry_price": 999.0}})

        with self._rows([{"symbol": "AVGO", "status": "OPEN", "entry_price": 360.0}]):
            restored = shadow.restore_open_shadow_trades()

        assert restored == []
        assert shadow.load_shadow_trades()["AVGO"]["entry_price"] == 999.0

    def test_a_failed_read_returns_none_not_empty(self, isolated_state):
        """`None` means "could not read"; `[]` means "nothing to restore"."""

        with mock.patch("app.db.connection.get_engine", side_effect=RuntimeError("down")):
            assert shadow.restore_open_shadow_trades() is None


def test_the_upsert_keys_on_symbol_and_open_time():
    """The state file is keyed by symbol alone, so two trades on one symbol
    would collide in the mirror without the timestamp."""

    from app.db import persistence

    captured = {}
    with mock.patch.object(persistence, "_safe_execute",
                           side_effect=lambda s, p: captured.update(p) or True):
        persistence.upsert_shadow_trade(
            {"symbol": "TSLA", "opened_at": "2026-08-21T10:45:00", "status": "OPEN"}
        )

    assert captured["trade_key"] == "TSLA|2026-08-21T10:45:00"


def test_net_final_r_is_preferred_over_gross():
    """`final_r` is the underlying move; only `net_final_r` is comparable to V1,
    which pays the option friction."""

    from app.db import persistence

    captured = {}
    with mock.patch.object(persistence, "_safe_execute",
                           side_effect=lambda s, p: captured.update(p) or True):
        persistence.upsert_shadow_trade({
            "symbol": "TSLA", "opened_at": "x", "status": "CLOSED",
            "final_r": 2.0, "net_final_r": 1.6,
        })

    assert captured["r_multiple"] == 1.6
