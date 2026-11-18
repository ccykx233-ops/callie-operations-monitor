"""Deterministic daily roll-up for monitoring audit rows."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any, Iterable


def build_daily_summary(
    *,
    summary_date: date,
    component: str,
    expected_runs: int,
    runs: Iterable[dict[str, Any]],
    incidents: Iterable[dict[str, Any]],
    notifications: Iterable[dict[str, Any]],
    generated_at: datetime,
) -> dict[str, Any]:
    """Build one idempotent row for ``callie_monitor.daily_summaries``."""

    run_rows = list(runs)
    incident_rows = list(incidents)
    notification_rows = list(notifications)
    statuses = Counter(row.get("status") for row in run_rows)
    delivery = Counter(row.get("delivery_status") for row in notification_rows)
    last_successes = [
        row.get("finished_at")
        for row in run_rows
        if row.get("status") == "healthy" and row.get("finished_at") is not None
    ]
    # A skipped invocation is recorded for audit, but it did not perform the
    # scheduled check and therefore must not hide a missed run.
    completed = sum(statuses[key] for key in ("healthy", "abnormal", "error"))
    return {
        "summary_date": summary_date,
        "component": component,
        "expected_runs": expected_runs,
        "completed_runs": completed,
        "missed_runs": max(0, expected_runs - completed),
        "healthy_runs": statuses["healthy"],
        "abnormal_runs": statuses["abnormal"],
        "error_runs": statuses["error"],
        "incidents_opened": sum(1 for row in incident_rows if row.get("opened_on") == summary_date),
        "incidents_recovered": sum(
            1 for row in incident_rows if row.get("recovered_on") == summary_date
        ),
        "notifications_sent": delivery["sent"],
        "notifications_failed_or_uncertain": delivery["failed"] + delivery["uncertain"],
        "last_success_at": max(last_successes) if last_successes else None,
        "generated_at": generated_at,
        "metrics": {},
    }
