from app.gates.rule_evaluation import build_rule_evaluations


def test_build_rule_evaluations_records_actual_thresholds():
    evaluations = build_rule_evaluations({
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
    }, "scan-1")

    by_name = {item.rule_name: item for item in evaluations}
    assert by_name["RR"].actual_value == 1.74
    assert by_name["RR"].required_value == 1.8
    assert not by_name["RR"].passed
    assert by_name["RR"].blocked_trade
    assert by_name["Quote Freshness"].actual_value == "STALE_QUOTE"
    assert not by_name["Quote Freshness"].passed
    assert by_name["Quote Freshness"].evaluation_phase == "ENTRY"
    assert by_name["Quote Freshness"].to_record()["evaluation_phase"] == "ENTRY"


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
