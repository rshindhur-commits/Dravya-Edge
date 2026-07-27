from __future__ import annotations

import pandas as pd

from app.gates.rule_evaluation import rule_domain


def build_rule_outcome_attribution(evidence):
    evidence = evidence.copy() if evidence is not None else pd.DataFrame()
    if evidence.empty or "rule_evaluation" not in evidence:
        return pd.DataFrame()
    evidence["rule"] = evidence["rule_evaluation"].fillna("").astype(str).str.strip()
    evidence = evidence[evidence["rule"] != ""]
    if evidence.empty:
        return pd.DataFrame()
    evidence["final_r"] = pd.to_numeric(evidence.get("final_r"), errors="coerce")
    evidence["trend_capture"] = pd.to_numeric(evidence.get("trend_capture"), errors="coerce")
    dimensions = [column for column in ("setup", "direction", "regime") if column in evidence]
    rows = []
    for rule, group in evidence.groupby("rule", dropna=True):
        domain = rule_domain(rule.split()[0])
        comparable = evidence
        for dimension in dimensions:
            values = group[dimension].dropna().unique()
            if len(values) == 1:
                comparable = comparable[comparable[dimension] == values[0]]
        baseline = comparable[comparable["rule"] != rule]
        average_r = group["final_r"].mean()
        baseline_r = baseline["final_r"].mean()
        rows.append({
            "rule": rule,
            "domain": domain,
            "samples": int(len(group)),
            "average_final_r": round(float(average_r), 2) if pd.notna(average_r) else None,
            "average_trend_capture": round(float(group["trend_capture"].mean()), 2) if group["trend_capture"].notna().any() else None,
            "matched_baseline_final_r": round(float(baseline_r), 2) if pd.notna(baseline_r) else None,
            "estimated_final_r_delta": round(float(average_r - baseline_r), 2) if pd.notna(average_r) and pd.notna(baseline_r) else None,
            "methodology": "observational matched-cohort association; not causal attribution",
        })
    return pd.DataFrame(rows).sort_values(["domain", "samples"], ascending=[True, False])