import pandas as pd

from app.analytics.rule_attribution import build_rule_outcome_attribution


def test_rule_attribution_is_explicitly_observational_and_separates_domains():
    attribution = build_rule_outcome_attribution(pd.DataFrame([
        {"rule_evaluation": "RR below threshold", "setup": "EMA", "direction": "CALL", "regime": "BULL", "final_r": -1.0, "trend_capture": 20},
        {"rule_evaluation": "RR below threshold", "setup": "EMA", "direction": "CALL", "regime": "BULL", "final_r": -0.5, "trend_capture": 30},
        {"rule_evaluation": "Telegram", "setup": "EMA", "direction": "CALL", "regime": "BULL", "final_r": 1.5, "trend_capture": 75},
    ]))

    rr = attribution.set_index("rule").loc["RR below threshold"]
    telegram = attribution.set_index("rule").loc["Telegram"]
    assert rr["domain"] == "TRADING"
    assert telegram["domain"] == "OPERATIONAL"
    assert "not causal" in rr["methodology"]