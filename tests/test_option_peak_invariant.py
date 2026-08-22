"""A contract cannot close above its own peak.

TSLA on 2026-08-21 recorded `option_peak_mid` **8.62** and sold at **9.60**. A
peak below the exit price is arithmetically impossible, and that is what makes
this class of bug detectable without a replay -- no market knowledge required,
just two numbers on the same row.

The real error was larger than the tell: the contract traded to **11.05** from a
7.70 entry, a **+43.5%** peak recorded as +11.9%. The cause was sampling. The
peak was read only on the scan cycle, so a twenty-minute trade was priced about
four times.

It matters because `_option_giveback_exit` halves this number to place a
protective floor. A peak 30% low puts the floor at the wrong price, and no
arming threshold can compensate -- which is why "lower the arm so it fires" was
the wrong advice.

`mfe_r` received exactly this ratchet after the identical bug on the underlying
side. `option_peak_mid` was missed.
"""

import pytest

from app.state import paper_trade_manager as ptm


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ptm, "PAPER_TRADE_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(ptm, "_queue_paper_trade_upsert", lambda trade: None)
    return tmp_path


def _open_trade(**over):
    trade = {
        "symbol": "TSLA",
        "trade_key": "TSLA|O:TSLA260828C00360000|2026-08-21 10:45:53",
        "option_ticker": "O:TSLA260828C00360000",
        "status": "OPEN",
        "direction": "CALL",
        "entry_price": 356.40,
        "stop_loss": 353.74,
        "initial_stop_loss": 353.74,
        "take_profit": 362.08,
        "opened_at": "2026-08-21 10:45:53",
        "option_entry_mid": 7.675,
        "option_entry_ask": 7.70,
        "option_entry_bid": 7.65,
        "option_mid": 7.675,
    }
    trade.update(over)
    return trade


class TestTheRecordedPeakCannotBeBelowTheExit:

    def test_the_tsla_case(self, isolated):
        """The exact numbers from 2026-08-21."""

        ptm.save_paper_trades({_open_trade()["trade_key"]: _open_trade(
            option_peak_mid=8.62,
            option_bid=9.60,
            option_ask=9.75,
        )})

        closed = ptm.close_paper_trade(
            "TSLA", close_price=362.08, exit_reason="Profit target reached (long)",
            notify_exit=False,
        )

        exit_mid = closed.get("option_close_mid")
        assert exit_mid is not None, "the close must price the contract"
        assert closed["option_peak_mid"] >= exit_mid, (
            f"peak {closed['option_peak_mid']} is below the exit {exit_mid} -- "
            "the trade sold above a price it supposedly never reached"
        )

    def test_a_genuine_giveback_still_shows(self, isolated):
        """Ratcheted, not assigned. A real peak above the exit must survive."""

        ptm.save_paper_trades({_open_trade()["trade_key"]: _open_trade(
            option_peak_mid=14.00,
            option_bid=9.60,
            option_ask=9.75,
        )})

        closed = ptm.close_paper_trade(
            "TSLA", close_price=362.08, exit_reason="Profit target reached (long)",
            notify_exit=False,
        )

        assert closed["option_peak_mid"] == 14.00, "a real give-back must not be erased"

    def test_a_trade_that_never_recorded_a_peak_gets_one(self, isolated):
        """Every trade before 2026-08-19 is in this state."""

        ptm.save_paper_trades({_open_trade()["trade_key"]: _open_trade(
            option_bid=9.60, option_ask=9.75,
        )})

        closed = ptm.close_paper_trade(
            "TSLA", close_price=362.08, exit_reason="Profit target reached (long)",
            notify_exit=False,
        )

        assert closed["option_peak_mid"] is not None
        assert closed["option_peak_mid"] >= closed["option_close_mid"]


class TestTheMonitorRatchetsBetweenScans:
    """The sampling half. Without it the close-path floor is all there is, and a
    peak made mid-flight is still lost."""

    def test_a_fresh_quote_raises_the_peak(self, monkeypatch):
        from app.runtime import position_monitor as pm

        monkeypatch.setattr(pm, "get_bool_env", lambda *a, **k: True)
        monkeypatch.setattr(
            "app.options.live_options_chain.fetch_latest_option_quote",
            lambda ticker: {"mid_price": 11.05},
        )

        mid, fresh = pm._live_option_mid(
            {"option_ticker": "O:TSLA260828C00360000", "option_current_mid": 8.62}
        )

        assert fresh is True
        assert mid == 11.05, "the monitor must see what the scan cycle missed"

    def test_an_unpriceable_quote_keeps_the_last_true_mid(self, monkeypatch):
        """None is not an update. Returning it would blind the give-back floor."""

        from app.runtime import position_monitor as pm

        monkeypatch.setattr(pm, "get_bool_env", lambda *a, **k: True)
        monkeypatch.setattr(
            "app.options.live_options_chain.fetch_latest_option_quote",
            lambda ticker: {"mid_price": None},
        )

        mid, fresh = pm._live_option_mid(
            {"option_ticker": "O:X", "option_current_mid": 8.62}
        )

        assert (mid, fresh) == (8.62, False)

    def test_a_failed_fetch_does_not_raise(self, monkeypatch):
        from app.runtime import position_monitor as pm

        monkeypatch.setattr(pm, "get_bool_env", lambda *a, **k: True)
        monkeypatch.setattr(
            "app.options.live_options_chain.fetch_latest_option_quote",
            lambda ticker: (_ for _ in ()).throw(RuntimeError("polygon down")),
        )

        assert pm._live_option_mid({"option_ticker": "O:X",
                                    "option_current_mid": 8.62}) == (8.62, False)

    def test_the_switch_turns_it_off(self, monkeypatch):
        from app.runtime import position_monitor as pm

        monkeypatch.setattr(pm, "get_bool_env", lambda *a, **k: False)

        assert pm._live_option_mid({"option_ticker": "O:X",
                                    "option_current_mid": 8.62}) == (8.62, False)
