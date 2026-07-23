from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.analytics.trend_capture import summarize_trend_capture
from app.runtime.scan_generation import atomic_write_json, metadata_from_generation
from app.storage.daily_paths import DAILY_DIR, daily_path, live_path
from app.ui.dashboard_state import build_today_performance_summary


def _read_csv(path: Path):

    try:

        if not path.exists() or path.stat().st_size == 0:

            return pd.DataFrame()

        return pd.read_csv(path)

    except Exception:

        return pd.DataFrame()


def _read_json(path: Path):

    try:

        if not path.exists() or path.stat().st_size == 0:

            return {}

        return json.loads(path.read_text(encoding="utf-8"))

    except Exception:

        return {}


def _safe_number(value, default=None):

    try:

        if value is None or pd.isna(value):

            return default

        return round(float(value), 4)

    except Exception:

        return default


def _json_records(df, limit=None):

    if df is None or df.empty:

        return []

    records = df.head(limit).to_dict("records") if limit else df.to_dict("records")

    return [
        {
            key: (
                None
                if pd.isna(value)
                else value
            )
            for key, value in record.items()
        }
        for record in records
    ]


def _paper_summary(paper_events):

    if paper_events is None or paper_events.empty:

        return {
            "closed_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "total_r": 0,
            "average_r": None,
        }

    events = paper_events.copy()
    event_type = events.get("event_type", pd.Series(dtype=object)).astype(str).str.upper()
    closed = events[
        event_type.isin(["AUTO_EXIT", "MANUAL_CLOSE", "CLOSE", "CLOSED", "EXIT"])
    ].copy()

    if closed.empty:

        closed = events[events.get("status", pd.Series(dtype=object)).astype(str).str.upper().eq("CLOSED")].copy()

    r_values = pd.to_numeric(
        closed.get("r_multiple", pd.Series(dtype=object)),
        errors="coerce"
    ).dropna()
    wins = r_values[r_values > 0]
    losses = r_values[r_values < 0]

    return {
        "closed_trades": int(len(closed)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": round(len(wins) / len(r_values) * 100, 1) if len(r_values) else None,
        "total_r": round(float(r_values.sum()), 2) if len(r_values) else 0,
        "average_r": round(float(r_values.mean()), 2) if len(r_values) else None,
    }


def _scanner_kpis(scanner):

    if scanner is None or scanner.empty:

        return {
            "rows": 0,
            "enter_paper": 0,
            "review": 0,
            "wait": 0,
            "avoid": 0,
        }

    action = scanner.get("Action Status", pd.Series(dtype=object)).astype(str).str.upper()

    return {
        "rows": int(len(scanner)),
        "enter_paper": int(action.eq("ENTER_PAPER").sum()),
        "review": int(action.eq("REVIEW_TV_CHART").sum()),
        "wait": int(action.eq("WAIT").sum()),
        "avoid": int(action.eq("AVOID").sum()),
    }


def _strategy_confidence(report_date, trend_capture):

    frames = []

    try:

        for directory in sorted(DAILY_DIR.iterdir()):

            if not directory.is_dir() or directory.name > report_date:

                continue

            frame = _read_csv(directory / "trend_capture_analysis.csv")

            if not frame.empty:

                frames.append(frame)

    except Exception:

        pass

    if not frames and trend_capture is not None and not trend_capture.empty:

        frames.append(trend_capture)

    if not frames:

        return {
            "evidence_days": 0,
            "completed_trades": 0,
            "confidence_pct": 0,
            "level": "INSUFFICIENT_EVIDENCE",
            "rule_change_allowed": False,
            "message": "No completed trades. Do not change rules.",
        }

    evidence = pd.concat(frames, ignore_index=True, sort=False)
    evidence_days = int(len(frames))
    completed_trades = int(len(evidence))
    day_component = min(35, 2 + max(0, evidence_days - 1) * 33 / 19)
    trade_component = min(42, completed_trades)
    confidence_pct = min(95, round(15 + day_component + trade_component))
    rule_change_allowed = evidence_days >= 20 and completed_trades >= 80

    return {
        "evidence_days": evidence_days,
        "completed_trades": completed_trades,
        "confidence_pct": confidence_pct,
        "level": "ACTIONABLE_EVIDENCE" if rule_change_allowed else "OBSERVATIONAL_ONLY",
        "rule_change_allowed": rule_change_allowed,
        "message": (
            "Evidence threshold met; review changes through controlled validation."
            if rule_change_allowed
            else "Evidence is still observational. DO NOT CHANGE RULE."
        ),
    }


def _setup_performance(trend_capture):

    if trend_capture is None or trend_capture.empty:

        return pd.DataFrame(columns=["Setup", "trades", "average_capture"])

    required = {"Setup", "Trend Capture %"}

    if not required.issubset(trend_capture.columns):

        return pd.DataFrame(columns=["Setup", "trades", "average_capture"])

    data = trend_capture[["Setup", "Trend Capture %"]].copy()
    data["Trend Capture %"] = pd.to_numeric(data["Trend Capture %"], errors="coerce")
    data = data.dropna(subset=["Setup", "Trend Capture %"])

    if data.empty:

        return pd.DataFrame(columns=["Setup", "trades", "average_capture"])

    return (
        data.groupby("Setup", dropna=True)["Trend Capture %"]
        .agg(trades="count", average_capture="mean")
        .reset_index()
        .sort_values("average_capture", ascending=False)
    )


def _engineering_finding(reason, evidence, action, status="OBSERVE"):

    return {
        "status": status,
        "reason": reason,
        "evidence": evidence,
        "action": action,
    }


def _build_engineering_diagnosis(scanner, trend_capture, report_date, confidence):

    scanner_kpis = _scanner_kpis(scanner)
    setup_performance = _setup_performance(trend_capture)
    completed_trades = int(len(trend_capture)) if trend_capture is not None else 0
    promotion_count = scanner_kpis["enter_paper"] + scanner_kpis["review"]
    scanner_status = "CANDIDATE_COVERAGE" if promotion_count else "NO_CANDIDATE_PROMOTION"
    scanner_action = (
        "Observe promotion quality; DO NOT CHANGE RULE."
        if promotion_count
        else "Inspect scanner promotion gates; DO NOT CHANGE RULE."
    )
    diagnosis = {
        "scanner": {
            "status": scanner_status,
            "findings": [
                _engineering_finding(
                    f"{promotion_count} candidates reached review or paper-entry status.",
                    f"{scanner_kpis['rows']} scanner rows | {scanner_kpis['enter_paper']} ENTER_PAPER | {scanner_kpis['review']} REVIEW_TV_CHART",
                    scanner_action,
                    "OBSERVE" if promotion_count else "INVESTIGATE",
                )
            ],
        },
        "entry": {"status": "INSUFFICIENT_EVIDENCE", "findings": []},
        "exit": {"status": "INSUFFICIENT_EVIDENCE", "findings": []},
        "replay": {
            "status": "REPLAY_NOT_GENERATED",
            "findings": [
                _engineering_finding(
                    "Replay output is not available for comparison.",
                    "0 replay rows available in validation cache.",
                    "Generate replay before evaluating chart agreement.",
                    "MISSING",
                )
            ],
        },
        "missed_winners": {"status": "NO_MISSED_WINNERS", "findings": []},
        "tomorrow": {"status": "CONTINUE_VALIDATION", "findings": []},
    }

    if not setup_performance.empty:

        best = setup_performance.iloc[0]
        worst = setup_performance.iloc[-1]
        entry_findings = [
            _engineering_finding(
                (
                    f"{best['Setup']} outperformed today."
                    if len(setup_performance) > 1
                    else f"{best['Setup']} capture was observed today."
                ),
                f"{int(best['trades'])} trades | average capture {best['average_capture']:.1f}%",
                "Observe; DO NOT CHANGE RULE.",
                "OUTPERFORMED" if len(setup_performance) > 1 else "OBSERVE",
            )
        ]

        if len(setup_performance) > 1:

            entry_findings.append(
                _engineering_finding(
                    f"{worst['Setup']} underperformed today.",
                    f"{int(worst['trades'])} trades | average capture {worst['average_capture']:.1f}%",
                    "Observe; DO NOT CHANGE RULE.",
                    "UNDERPERFORMED",
                )
            )

        diagnosis["entry"] = {
            "status": "SETUP_PERFORMANCE_AVAILABLE",
            "findings": entry_findings,
        }
        diagnosis["tomorrow"] = {
            "status": "OBSERVE_WORST_SETUP",
            "findings": [
                _engineering_finding(
                    (
                        f"Investigate {worst['Setup']} execution evidence."
                        if len(setup_performance) > 1
                        else f"Collect more {best['Setup']} observations before comparison."
                    ),
                    f"{int(worst['trades'])} trades | average capture {worst['average_capture']:.1f}% | strategy confidence {confidence['confidence_pct']}%",
                    "Observe next session; DO NOT CHANGE RULE.",
                    "OBSERVE",
                )
            ],
        }

    if completed_trades:

        verdicts = trend_capture.get("Exit Verdict", pd.Series(dtype=object)).astype(str).str.upper()
        early_exits = int(verdicts.eq("EXIT_TOO_EARLY").sum())
        exit_reason = (
            "No evidence exits caused today's losses."
            if early_exits == 0
            else f"{early_exits} exits were classified EXIT_TOO_EARLY."
        )
        diagnosis["exit"] = {
            "status": "NO_EXIT_LOSS_EVIDENCE" if early_exits == 0 else "EXIT_REVIEW_REQUIRED",
            "findings": [
                _engineering_finding(
                    exit_reason,
                    f"{completed_trades} completed trades | {early_exits} EXIT_TOO_EARLY verdicts",
                    "Keep exit rules unchanged." if early_exits == 0 else "Observe exit triggers; DO NOT CHANGE RULE.",
                    "CLEAR" if early_exits == 0 else "OBSERVE",
                )
            ],
        }

    try:

        from app.analytics.loss_attribution import build_loss_attribution

        missed = build_loss_attribution(report_date)

        if not missed.empty:

            primary_reason = missed["reason"].astype(str).str.upper().value_counts().index[0]
            diagnosis["missed_winners"] = {
                "status": "MISSED_WINNERS_IDENTIFIED",
                "findings": [
                    _engineering_finding(
                        f"{len(missed)} missed winners require attribution review.",
                        f"Primary classification: {primary_reason} | {len(missed)} qualifying movers",
                        f"Investigate {primary_reason.lower()} promotion evidence; DO NOT CHANGE RULE.",
                        "INVESTIGATE",
                    )
                ],
            }

    except Exception:

        pass

    replay_state = _read_json(daily_path(report_date, "replay_state.json"))

    if replay_state.get("status") == "READY":

        coverage = replay_state.get("coverage_pct", 0)
        missing = replay_state.get("missing_indicators", 0)
        diagnosis["replay"] = {
            "status": "REPLAY_READY",
            "findings": [
                _engineering_finding(
                    "Replay classifications are ready for chart comparison.",
                    f"{replay_state.get('replay_rows', 0)} replay rows | {coverage}% coverage | {missing} missing indicators",
                    "Compare flagged rows with charts before changing rules.",
                    "READY",
                )
            ],
        }

    return diagnosis


def _recommendations(trend_summary, paper_summary, scanner_kpis):

    recommendations = []

    if trend_summary.get("engineering_recommendation"):

        recommendations.append(trend_summary["engineering_recommendation"])

    if paper_summary.get("closed_trades", 0) == 0 and scanner_kpis.get("enter_paper", 0) > 0:

        recommendations.append({
            "priority": "MEDIUM",
            "reason": "Paper entries exist but no closed paper trades are available yet.",
            "recommendation": "Wait for closes before changing strategy thresholds."
        })

    if not recommendations:

        recommendations.append({
            "priority": "LOW",
            "reason": "Validation cache did not find urgent blockers.",
            "recommendation": "Continue paper validation."
        })

    return recommendations


def build_trade_efficiency_state(trend_capture, paper_events=None):

    trend_capture = trend_capture if trend_capture is not None else pd.DataFrame()
    paper_events = paper_events if paper_events is not None else pd.DataFrame()
    trend_summary = summarize_trend_capture(trend_capture)
    today_performance = build_today_performance_summary(paper_events, trend_capture)
    columns = [
        "Trade Key", "Symbol", "Direction", "Setup", "Trend Capture %",
        "Trade Efficiency Score", "Exit Verdict", "Exit Quality",
        "Exit Verdict Reason", "Exit Trigger", "Engineering Recommendation",
        "Entry Grade", "Exit Grade", "Trend Health State", "Left On Table",
        "Exit Reason", "Bars Held",
    ]
    trades = trend_capture[[column for column in columns if column in trend_capture.columns]].copy()
    trades = trades.rename(columns={
        "Trade Key": "Trade",
        "Trend Capture %": "Capture %",
        "Trade Efficiency Score": "TES",
        "Trend Health State": "Trend Health",
    })
    chart_columns = ["Trade Key", "Trend Capture %", "Trade Efficiency Score", "Left On Table", "Trend Health Score"]
    chart_rows = trend_capture[[column for column in chart_columns if column in trend_capture.columns]]

    return {
        "summary": {
            "average_capture": trend_summary.get("average_capture"),
            "today_capture": trend_summary.get("average_capture"),
            "best_capture": trend_summary.get("best_capture"),
            "worst_capture": trend_summary.get("worst_capture"),
            "average_tes": today_performance.get("average_tes"),
            "average_r": today_performance.get("average_r"),
            "average_left_on_table": trend_summary.get("average_left_on_table"),
        },
        "trades": _json_records(trades),
        "charts": {
            "capture_histogram": _json_records(chart_rows[[column for column in ["Trade Key", "Trend Capture %"] if column in chart_rows.columns]]),
            "tes_histogram": _json_records(chart_rows[[column for column in ["Trade Key", "Trade Efficiency Score"] if column in chart_rows.columns]]),
            "capture_by_setup": _json_records(trend_summary.get("by_setup")),
            "capture_by_regime": _json_records(trend_summary.get("by_regime")),
            "exit_verdict": _json_records(trend_summary.get("exit_verdict_distribution")),
            "opportunity_cost": _json_records(chart_rows[[column for column in ["Trade Key", "Left On Table"] if column in chart_rows.columns]]),
            "trend_health_scatter": _json_records(chart_rows),
        },
        "recommendations": [
            recommendation for recommendation in [
                trend_summary.get("engineering_recommendation"),
                {"priority": "MEDIUM", "reason": "Average capture", "recommendation": trend_summary.get("recommendation")}
                if trend_summary.get("recommendation") else None,
            ] if recommendation
        ],
    }


def build_validation_state_payload(
    report_date: str,
    scanner: pd.DataFrame | None = None,
    paper_events: pd.DataFrame | None = None,
    trend_capture: pd.DataFrame | None = None,
    generated_at: str | None = None,
    scan_id: str | None = None,
    generation=None,
) -> dict[str, Any]:

    scanner = scanner if scanner is not None else pd.DataFrame()
    paper_events = paper_events if paper_events is not None else pd.DataFrame()
    trend_capture = trend_capture if trend_capture is not None else pd.DataFrame()
    candidate_outcomes = _read_csv(daily_path(report_date, "candidate_outcomes.csv"))
    from app.analytics.candidate_evidence import load_candidate_evidence
    from app.analytics.candidate_intelligence import build_candidate_intelligence
    from app.analytics.delay_attribution import build_delay_attribution
    candidate_intelligence = build_candidate_intelligence(
        load_candidate_evidence(report_date)
    )
    delay_attribution = build_delay_attribution(report_date)
    trend_summary = summarize_trend_capture(trend_capture)
    paper = _paper_summary(paper_events)
    scanner_kpis = _scanner_kpis(scanner)
    trade_efficiency = build_trade_efficiency_state(trend_capture, paper_events)
    strategy_confidence = _strategy_confidence(report_date, trend_capture)
    diagnosis = _build_engineering_diagnosis(
        scanner,
        trend_capture,
        report_date,
        strategy_confidence,
    )

    return {
        "metadata": metadata_from_generation(generation, scan_id=scan_id) or {
            "scan_id": scan_id,
            "generation": scan_id,
            "schema": 1,
            "created_at": generated_at or datetime.now(timezone.utc).isoformat(),
        },
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "trading_day": report_date,
        "scan_id": scan_id,
        "kpis": {
            "scanner": scanner_kpis,
            "paper": paper,
            "trend_capture": {
                "average_capture": trend_summary.get("average_capture"),
                "median_capture": trend_summary.get("median_capture"),
                "average_mfe": trend_summary.get("average_mfe"),
                "average_mae": trend_summary.get("average_mae"),
                "average_left_on_table": trend_summary.get("average_left_on_table"),
                "average_trend_health": trend_summary.get("average_trend_health"),
                "average_delay_gain": trend_summary.get("average_delay_gain"),
                "trade_efficiency_score": trend_summary.get("trade_efficiency_score"),
            },
        },
        "trend_capture": {
            "exit_verdict_distribution": _json_records(
                trend_summary.get("exit_verdict_distribution")
            ),
            "by_setup": _json_records(trend_summary.get("by_setup")),
            "by_regime": _json_records(trend_summary.get("by_regime")),
            "by_exit_reason": _json_records(trend_summary.get("by_exit_reason")),
        },
        "trade_efficiency": trade_efficiency,
        "candidate_outcomes": _json_records(candidate_outcomes),
        "candidate_intelligence": {
            "summary": candidate_intelligence["summary"],
            "good_candidates": _json_records(candidate_intelligence["good_candidates"]),
            "high_quality_blocked": _json_records(candidate_intelligence["high_quality_blocked"]),
            "investigation_queue": _json_records(candidate_intelligence["investigation_queue"]),
            "outcome_matrix": _json_records(candidate_intelligence["outcome_matrix"]),
            "missed_winner_breakdown": _json_records(candidate_intelligence["missed_winner_breakdown"]),
        },
        "telegram_quality": {
            "misses": int(candidate_outcomes.get("telegram_miss", pd.Series(dtype=bool)).sum()) if not candidate_outcomes.empty else 0,
            "false_alerts": int(candidate_outcomes.get("false_alert", pd.Series(dtype=bool)).sum()) if not candidate_outcomes.empty else 0,
        },
        "delay_attribution": _json_records(delay_attribution),
        "strategy_confidence": strategy_confidence,
        "recommendations": _recommendations(
            trend_summary,
            paper,
            scanner_kpis
        ),
        "diagnosis": diagnosis,
    }


def write_validation_state(report_date: str, scan_id: str | None = None, generation=None):

    scanner = _read_csv(daily_path(report_date, "scanner_output_close.csv"))
    paper_events = _read_csv(daily_path(report_date, "paper_trade_events.csv"))
    trend_capture = _read_csv(daily_path(report_date, "trend_capture_analysis.csv"))
    payload = build_validation_state_payload(
        report_date,
        scanner=scanner,
        paper_events=paper_events,
        trend_capture=trend_capture,
        scan_id=scan_id,
        generation=generation,
    )
    paths = [
        live_path("validation_state.json"),
        daily_path(report_date, "validation_state.json"),
    ]

    for path in paths:

        atomic_write_json(path, payload)

    try:

        performance = build_today_performance_summary(
            paper_events,
            trend_capture,
        )

        for dashboard_path in [
            live_path("dashboard_state.json"),
            daily_path(report_date, "dashboard_state.json"),
        ]:

            if dashboard_path.exists():

                dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
                dashboard["today_performance"] = performance
                atomic_write_json(dashboard_path, dashboard)

    except Exception:

        pass

    return payload