"""Explicit PostgreSQL writes limited to the isolated monitoring schema."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from monitoring_models import (
    ComponentReport,
    DailyMonitorSummary,
    MonitorNotification,
    MonitorRun,
)


def start_run(
    session: Session,
    *,
    schedule_key: str,
    component: str,
    started_at: datetime,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    source_revision: str | None = None,
) -> UUID | None:
    """Claim one scheduled audit; duplicate workflow starts return ``None``."""

    run_id = uuid4()
    statement = (
        insert(MonitorRun)
        .values(
            id=run_id,
            schedule_key=schedule_key,
            component=component,
            started_at=started_at,
            window_start=window_start,
            window_end=window_end,
            status="running",
            rule_results={},
            source_revision=source_revision,
        )
        .on_conflict_do_nothing(index_elements=[MonitorRun.schedule_key])
        .returning(MonitorRun.id)
    )
    claimed = session.execute(statement).scalar_one_or_none()
    session.commit()
    return claimed


def finish_run(
    session: Session,
    *,
    run_id: UUID,
    finished_at: datetime,
    status: str,
    duration_ms: int,
    rule_results: dict[str, Any],
    error_code: str | None = None,
) -> None:
    run = session.get(MonitorRun, run_id, with_for_update=True)
    if run is None:
        raise LookupError("MONITOR_RUN_NOT_FOUND")
    if run.status != "running":
        raise RuntimeError("MONITOR_RUN_ALREADY_FINISHED")
    run.finished_at = finished_at
    run.status = status
    run.duration_ms = duration_ms
    run.rule_results = rule_results
    run.error_code = error_code
    session.commit()


def record_component_report(
    session: Session,
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
    """Insert a best-effort report exactly once."""

    statement = (
        insert(ComponentReport)
        .values(
            report_key=report_key,
            component=component,
            instance_id=instance_id,
            reported_at=reported_at,
            period_start=period_start,
            period_end=period_end,
            status=status,
            metrics=metrics,
            error_code=error_code,
        )
        .on_conflict_do_nothing(index_elements=[ComponentReport.report_key])
        .returning(ComponentReport.id)
    )
    inserted = session.execute(statement).scalar_one_or_none() is not None
    session.commit()
    return inserted


def upsert_daily_summary(session: Session, values: dict[str, Any]) -> None:
    """Regenerate a day's summary without creating duplicate rows."""

    statement = insert(DailyMonitorSummary).values(**values)
    excluded = statement.excluded
    statement = statement.on_conflict_do_update(
        index_elements=[DailyMonitorSummary.summary_date, DailyMonitorSummary.component],
        set_={
            "expected_runs": excluded.expected_runs,
            "completed_runs": excluded.completed_runs,
            "missed_runs": excluded.missed_runs,
            "healthy_runs": excluded.healthy_runs,
            "abnormal_runs": excluded.abnormal_runs,
            "error_runs": excluded.error_runs,
            "incidents_opened": excluded.incidents_opened,
            "incidents_recovered": excluded.incidents_recovered,
            "notifications_sent": excluded.notifications_sent,
            "notifications_failed_or_uncertain": excluded.notifications_failed_or_uncertain,
            "last_success_at": excluded.last_success_at,
            "generated_at": excluded.generated_at,
            "metrics": excluded.metrics,
        },
    )
    session.execute(statement)
    session.commit()


def record_notification(
    session: Session,
    *,
    incident_id: UUID | None,
    kind: str,
    delivery_status: str,
    attempted_at: datetime,
    payload_sha256: str,
    error_code: str | None = None,
    safe_summary: str | None = None,
) -> UUID:
    row = MonitorNotification(
        incident_id=incident_id,
        kind=kind,
        delivery_status=delivery_status,
        attempted_at=attempted_at,
        confirmed_at=attempted_at if delivery_status == "sent" else None,
        payload_sha256=payload_sha256,
        error_code=error_code,
        safe_summary=safe_summary,
    )
    session.add(row)
    session.commit()
    return row.id
