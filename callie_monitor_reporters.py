"""Best-effort heartbeat/counter reporters for existing local processes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from callie_monitor_store import record_component_report


def report_best_effort(
    session_factory: Callable[[], Session],
    *,
    report_key: str,
    component: str,
    instance_id: str,
    reported_at: datetime,
    status: str,
    metrics: dict[str, Any],
    period_start: datetime | None = None,
    period_end: datetime | None = None,
    error_code: str | None = None,
) -> bool:
    """Never let monitoring-report failure stop the business process."""

    try:
        with session_factory() as session:
            return record_component_report(
                session,
                report_key=report_key,
                component=component,
                instance_id=instance_id,
                reported_at=reported_at,
                status=status,
                metrics=metrics,
                period_start=period_start,
                period_end=period_end,
                error_code=error_code,
            )
    except Exception:
        return False


def workbench_hour_report_key(instance_id: str, period_start: datetime) -> str:
    return f"workbench:{instance_id}:{period_start.isoformat()}"


def order_runtime_report_key(component: str, instance_id: str, reported_at: datetime) -> str:
    bucket = reported_at.replace(minute=0, second=0, microsecond=0)
    return f"{component}:{instance_id}:{bucket.isoformat()}"
