"""The leverage floor ships off, and must stay inert until it is turned on."""

from app.risk.option_leverage import evaluate_option_leverage


def test_the_check_is_disabled_by_default_but_still_records_the_number(monkeypatch):
    """Off means "blocks nothing", not "measures nothing".

    The floor was derived by reconstructing leverage from entry and fill prices
    after the fact, because nothing ever recorded it. A disabled check that also
    stopped recording would guarantee the same gap next time.
    """

    monkeypatch.delenv("OPTION_MIN_LEVERAGE", raising=False)

    result = evaluate_option_leverage(entry_price=150.0, option_price=15.0)

    assert result["viable"] is True
    assert result["reason"] == "CHECK_DISABLED"
    assert result["leverage"] == 10.0, "the number is the reason this runs at all"


def test_leverage_is_the_underlying_over_the_premium(monkeypatch):
    monkeypatch.setenv("OPTION_MIN_LEVERAGE", "20")

    # 149.91 / 2.975 = 50.4x -- today's ORCL contract.
    result = evaluate_option_leverage(entry_price=149.91, option_price=2.975)

    assert result["leverage"] == 50.39
    assert result["viable"] is True
    assert result["reason"] == "LEVERAGE_SUFFICIENT"


def test_a_contract_below_the_floor_is_refused(monkeypatch):
    monkeypatch.setenv("OPTION_MIN_LEVERAGE", "20")

    # 150 / 10 = 15x, inside the bucket that won 3% of 31 archived trades.
    result = evaluate_option_leverage(entry_price=150.0, option_price=10.0)

    assert result["leverage"] == 15.0
    assert result["viable"] is False
    assert result["reason"] == "LEVERAGE_BELOW_FLOOR"


def test_an_unpriced_contract_is_a_data_gap_not_a_verdict(monkeypatch):
    """Blocking on missing data drops trades whenever the quote feed hiccups."""

    monkeypatch.setenv("OPTION_MIN_LEVERAGE", "20")

    for entry, option in ((150.0, None), (150.0, 0), (None, 15.0), (0, 15.0)):
        result = evaluate_option_leverage(entry_price=entry, option_price=option)
        assert result["viable"] is None, (entry, option)
        assert result["reason"] in {"NO_ENTRY_PRICE", "NO_OPTION_PRICE"}


def test_a_junk_value_does_not_raise(monkeypatch):
    monkeypatch.setenv("OPTION_MIN_LEVERAGE", "20")

    assert evaluate_option_leverage("not-a-price", "15.0")["viable"] is None
    assert evaluate_option_leverage(float("nan"), 15.0)["viable"] is None
