from app.gates.rule_evaluation import build_rule_evaluations


def _row(**overrides):
    row = {
        "Symbol": "NVDA",
        "Entry": "EMA_PULLBACK",
        "Action Status": "ENTER_PAPER",
        "Setup %": 82,
        "Candidate RR": 1.74,
        "Option Quality Score": 75,
        "Option Spread %": 4,
        "Option Quote Freshness": "STALE_QUOTE",
        "Affordable": True,
        "Candidate Direction": "CALL",
        "Candidate Entry Price": 100,
        "Candidate Stop Price": 98,
        "Candidate Target Price": 104,
    }
    row.update(overrides)
    return row


def test_build_rule_evaluations_records_actual_thresholds(monkeypatch):
    """The recorded bar is the deployed one, not the dataclass default.

    Pinned through the environment rather than left to whatever .env the run
    picks up, so this asserts a value it controls.
    """

    monkeypatch.setenv("SCANNER_GATE_MIN_RR", "2.0")
    monkeypatch.setenv("SCANNER_GATE_MIN_SETUP", "70.0")

    by_name = {item.rule_name: item for item in build_rule_evaluations(_row(), "scan-1")}

    assert by_name["RR"].actual_value == 1.74
    assert by_name["RR"].required_value == 2.0
    assert not by_name["RR"].passed
    assert by_name["RR"].blocked_trade
    assert by_name["Quote Freshness"].actual_value == "STALE_QUOTE"
    assert not by_name["Quote Freshness"].passed
    assert by_name["Quote Freshness"].evaluation_phase == "ENTRY"
    assert by_name["Quote Freshness"].to_record()["evaluation_phase"] == "ENTRY"


def test_the_audit_never_falls_back_to_the_dataclass_default(monkeypatch):
    """Regression: a persisted audit that reports its own defaults is unfalsifiable.

    `build_rule_evaluations` is called with no config by all five of its callers,
    and every one of them persists what it builds. While it constructed a bare
    `EntryGateConfig()` the spread ceiling was recorded as 10.0 no matter what
    the worker enforced -- 50 of 50 rows on 2026-08-10 against a deployed 6 --
    so "what did the gate require" could not be answered from the audit that
    exists to answer it.
    """

    monkeypatch.setenv("OPTION_MAX_SPREAD_PCT", "3.0")

    spread = {
        item.rule_name: item
        for item in build_rule_evaluations(_row(**{"Option Spread %": 7.5}), "scan-1")
    }["Option Spread"]

    # 7.5 clears the 10.0 dataclass default and fails the deployed 3.0. Which
    # number the audit used is therefore visible in `passed`, not just recorded.
    assert spread.required_value == 3.0
    assert not spread.passed


def _candidate_without_a_contract():
    """What the scanner writes when selection never ran: every option field None.

    A candidate that dies at Momentum or Setup gets here, and used to pick up an
    Option Quality failure at 0.0 against a floor of 65 -- 2,913 of them on
    2026-08-03, none of which blocked anything.
    """

    return {
        "Symbol": "NVDA",
        "Entry": "EMA_PULLBACK",
        "Action Status": "ENTER_PAPER",
        "Setup %": 82,
        "Candidate RR": 2.4,
        "Affordable": True,
        "Candidate Direction": "CALL",
        "Candidate Entry Price": 100,
        "Candidate Stop Price": 98,
        "Candidate Target Price": 104,
        "Option Quality Score": None,
        "Option Spread %": None,
        "Option Quote Freshness": None,
        "Option Mid Price": None,
        "Option Ticker": None,
    }


def test_option_rules_are_not_emitted_without_a_contract():
    names = {
        item.rule_name
        for item in build_rule_evaluations(_candidate_without_a_contract(), "scan-3")
    }

    assert "Option Quality" not in names
    assert "Quote Freshness" not in names
    assert "Option Spread" not in names
    # The rules that do not depend on a contract still report.
    assert {"Setup", "RR", "Affordability"} <= names


def test_option_rules_return_once_a_contract_is_priced():
    row = _candidate_without_a_contract()
    row["Option Mid Price"] = 2.50
    row["Option Quality Score"] = 40
    row["Option Quote Freshness"] = "LIVE_QUOTE"

    by_name = {
        item.rule_name: item
        for item in build_rule_evaluations(row, "scan-4")
    }

    assert by_name["Option Quality"].actual_value == 40
    assert not by_name["Option Quality"].passed
    assert by_name["Option Quality"].blocked_trade


def test_build_rule_evaluations_emits_lifecycle_phases():
    evaluations = build_rule_evaluations({
        "Symbol": "NVDA",
        "Entry": "EMA_PULLBACK",
        "Trade Action": "HOLD",
        "Bars In Trade": 3,
        "Live Exit Signal": True,
        "Live Exit Reason": "Hard stop hit",
        "Replay Ran": True,
        "Replay Outcome": "TARGET_HIT",
    }, "scan-2")

    phases = {item.evaluation_phase for item in evaluations}
    assert {"ENTRY", "ACTIVE", "EXIT", "REPLAY"}.issubset(phases)
