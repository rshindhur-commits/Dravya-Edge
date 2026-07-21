from app.alerts.telegram_alerts import build_telegram_rule_evaluations
from app.decision.decision_engine import TradeDecision, build_review_rule_evaluations
from app.gates.rule_evaluation import aggregate_rule_evaluations
from app.options.option_affordability import build_affordability_rule_evaluations
from app.options.options_filter import build_option_rule_evaluations
from app.risk.risk_manager import build_risk_rule_evaluations
from app.runtime.paper_automation_support import build_paper_rule_evaluations


def test_native_emitters_cover_operational_rule_groups():
    risk = build_risk_rule_evaluations({"risk_reward": 1.2, "trade_allowed": False}, "scan", "AAPL", "BREAKOUT")
    option = build_option_rule_evaluations(
        {"option_quality_score": 70, "quote_freshness": "LIVE_QUOTE", "spread_pct": 2},
        {"liquid": True, "code": "LIQUID", "spread_pct": 2}, "scan", "AAPL", "BREAKOUT"
    )
    affordability = build_affordability_rule_evaluations(
        {"affordable": True, "contract_cost": 200, "max_allowed_contract_cost": 500, "delta": 0.4, "affordability_status": "AFFORDABLE"},
        "scan", "AAPL", "BREAKOUT"
    )
    telegram = build_telegram_rule_evaluations({}, {"sent": False, "reason": "ELIGIBLE"}, "scan", "AAPL", "BREAKOUT")
    paper = build_paper_rule_evaluations({"Symbol": "AAPL", "Entry": "BREAKOUT"}, True, "ELIGIBLE", "scan")
    review = build_review_rule_evaluations({"Symbol": "AAPL", "Entry": "BREAKOUT"}, TradeDecision("ENTER_PAPER", 80, 2, 75, 90), "scan")
    rules = aggregate_rule_evaluations(risk, option, affordability, telegram, paper, review)
    groups = {rule.rule_group for rule in rules}
    assert {"Risk", "Option", "Realtime", "Affordability", "Telegram", "Paper", "Review"}.issubset(groups)
    assert any(rule.rule_name == "RR" and not rule.passed for rule in rules)


def test_aggregate_rule_evaluations_deduplicates_by_rule_identity():
    first = build_risk_rule_evaluations({"risk_reward": 2, "trade_allowed": True}, "scan", "AAPL")
    assert len(aggregate_rule_evaluations(first, first)) == len(first)
