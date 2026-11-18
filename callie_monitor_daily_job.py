"""Generate one persisted daily monitoring summary from audit tables."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from callie_monitor_store import upsert_daily_summary
from callie_monitor_summary import build_daily_summary
from monitoring_models import MonitorIncident, MonitorNotification, MonitorRun


def generate_daily_summary(
    session: Session,
    *,
    summary_date: date,
    component: str,
    expected_runs: int,
    window_start: datetime,
    window_end: datetime,
    generated_at: datetime,
) -> dict:
    """Read the completed local day, upsert its roll-up, and return it."""

    runs = session.execute(
        select(MonitorRun).where(
            MonitorRun.component == component,
            MonitorRun.started_at >= window_start,
            MonitorRun.started_at < window_end,
        )
    ).scalars().all()
    incidents = session.execute(
        select(MonitorIncident).where(
            or_(
                (
                    (MonitorIncident.first_detected_at >= window_start)
                    & (MonitorIncident.first_detected_at < window_end)
                ),
                (
                    (MonitorIncident.recovered_at >= window_start)
                    & (MonitorIncident.recovered_at < window_end)
                ),
            )
        )
    ).scalars().all()
    notifications = session.execute(
        select(MonitorNotification)
        .join(MonitorIncident, MonitorNotification.incident_id == MonitorIncident.id)
        .where(
            MonitorIncident.component == component,
            MonitorNotification.attempted_at >= window_start,
            MonitorNotification.attempted_at < window_end,
        )
    ).scalars().all()
    values = build_daily_summary(
        summary_date=summary_date,
        component=component,
        expected_runs=expected_runs,
        runs=[{"status": row.status, "finished_at": row.finished_at} for row in runs],
        incidents=[
            {
                "opened_on": summary_date
                if window_start <= row.first_detected_at < window_end
                else None,
                "recovered_on": summary_date
                if row.recovered_at is not None and window_start <= row.recovered_at < window_end
                else None,
            }
            for row in incidents
        ],
        notifications=[{"delivery_status": row.delivery_status} for row in notifications],
        generated_at=generated_at,
    )
    upsert_daily_summary(session, values)
    return values
