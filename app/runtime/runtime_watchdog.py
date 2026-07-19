from __future__ import annotations

from datetime import datetime, timezone
import json

from app.storage.daily_paths import live_path
from app.utils.json_store import load_json_file


def _parse_time(value):

    if not value:

        return None

    try:

        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    except Exception:

        return None


def _age_seconds(value):

    parsed = _parse_time(value)

    if not parsed:

        return None

    return (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()


def evaluate_runtime_health(
    runtime_state=None,
    dashboard_state=None,
    validation_state=None,
    replay_state=None,
    report_state=None,
    performance_summary=None,
    worker_alive=True,
):

    runtime_state = runtime_state or {}
    performance_summary = performance_summary or {}
    warnings = []
    errors = []
    score = 100.0
    queue_depth = sum(
        int(runtime_state.get(key, 0) or 0)
        for key in ["critical_jobs", "high_jobs", "normal_jobs", "low_jobs"]
    )

    if queue_depth > 50:

        warnings.append("Runtime queue depth exceeds 50 jobs.")
        score -= 10

    if not worker_alive:

        errors.append("Runtime worker is not alive.")
        score -= 35

    if runtime_state.get("scanner_running"):

        scanner_age = _age_seconds(runtime_state.get("updated_at_utc"))

        if scanner_age is not None and scanner_age > 600:

            errors.append("Scanner has been running for more than 10 minutes.")
            score -= 30

    dashboard_age = _age_seconds((dashboard_state or {}).get("generated_at"))

    if dashboard_age is not None and dashboard_age > 600:

        errors.append("Dashboard state is older than 10 minutes.")
        score -= 20

    for label, state in [
        ("validation", validation_state),
        ("replay", replay_state),
        ("report", report_state),
    ]:

        age = _age_seconds((state or {}).get("generated_at"))

        if age is not None and age > 3600:

            warnings.append(f"{label} state is older than 60 minutes.")
            score -= 5

    for row in performance_summary.get("recent_timings", [])[:10]:

        if row.get("category") == "telegram" and float(row.get("seconds") or 0) > 0.5:

            warnings.append("Telegram latency exceeded 500ms.")
            score -= 5
            break

    return {
        "healthy": not errors,
        "warnings": warnings,
        "errors": errors,
        "score": round(max(score, 0), 2),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def write_runtime_health():

    def live_json(filename):

        return load_json_file(str(live_path(filename)), {})

    health = evaluate_runtime_health(
        runtime_state=live_json("runtime_state.json"),
        dashboard_state=live_json("dashboard_state.json"),
        validation_state=live_json("validation_state.json"),
        replay_state=live_json("replay_state.json"),
        report_state=live_json("report_state.json"),
        performance_summary=live_json("runtime_performance_summary.json"),
    )
    path = live_path("runtime_health.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(health, indent=2, default=str), encoding="utf-8")
    return health