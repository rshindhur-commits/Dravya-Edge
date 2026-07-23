from __future__ import annotations

from datetime import datetime, timezone


def build_quote_refresh_observation(
    retry_count,
    latency_ms,
    freshness=None,
    refreshed_at=None,
):
    """Normalize bounded quote-refresh telemetry for scanner artifacts."""

    return {
        "quote_retry_count": int(retry_count or 0),
        "quote_latency_ms": round(float(latency_ms or 0), 2),
        "quote_refresh_time": (
            refreshed_at
            or datetime.now(timezone.utc).isoformat()
        ),
        "quote_refresh_outcome": freshness or "QUOTE_UNAVAILABLE",
    }