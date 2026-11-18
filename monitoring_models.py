"""Isolated persistence contract for Callie operational monitoring.

Importing this module is side-effect free.  Tables live in their own PostgreSQL
schema and are only created by the explicit migration command.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


MONITOR_SCHEMA = "callie_monitor"


class MonitorBase(DeclarativeBase):
    pass


class MonitorRun(MonitorBase):
    """One scheduled check, including failed and skipped checks."""

    __tablename__ = "monitor_runs"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_monitor_run"),
        UniqueConstraint("schedule_key", name="uq_monitor_run_schedule_key"),
        CheckConstraint(
            "component IN ('worker','workbench','order','daily')",
            name="ck_monitor_run_component",
        ),
        CheckConstraint(
            "status IN ('running','healthy','abnormal','error','skipped')",
            name="ck_monitor_run_status",
        ),
        Index("ix_monitor_run_component_started", "component", "started_at"),
        {"schema": MONITOR_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    schedule_key: Mapped[str] = mapped_column(String(160))
    component: Mapped[str] = mapped_column(String(20))
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="running")
    rule_results: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    source_revision: Mapped[str | None] = mapped_column(String(64))


class ComponentReport(MonitorBase):
    """Best-effort heartbeat/counter report produced by a business component."""

    __tablename__ = "component_reports"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_monitor_component_report"),
        UniqueConstraint("report_key", name="uq_monitor_component_report_key"),
        CheckConstraint(
            "component IN ('workbench_sync','order_executor','order_supervisor')",
            name="ck_monitor_component_report_component",
        ),
        CheckConstraint(
            "status IN ('healthy','degraded','error')",
            name="ck_monitor_component_report_status",
        ),
        Index("ix_monitor_component_report_latest", "component", "reported_at"),
        {"schema": MONITOR_SCHEMA},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_key: Mapped[str] = mapped_column(String(160))
    component: Mapped[str] = mapped_column(String(30))
    instance_id: Mapped[str] = mapped_column(String(160))
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))


class MonitorIncident(MonitorBase):
    """Deduplicated abnormal condition and its recovery state."""

    __tablename__ = "monitor_incidents"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_monitor_incident"),
        UniqueConstraint("fingerprint", name="uq_monitor_incident_fingerprint"),
        CheckConstraint(
            "component IN ('worker','workbench','order')",
            name="ck_monitor_incident_component",
        ),
        CheckConstraint(
            "status IN ('open','recovered')",
            name="ck_monitor_incident_status",
        ),
        Index("ix_monitor_incident_status_last", "status", "last_detected_at"),
        {"schema": MONITOR_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    fingerprint: Mapped[str] = mapped_column(String(64))
    component: Mapped[str] = mapped_column(String(20))
    rule_code: Mapped[str] = mapped_column(String(80))
    subject_ref: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default="open")
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=1)
    consecutive_successes: Mapped[int] = mapped_column(Integer, default=0)
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{MONITOR_SCHEMA}.monitor_runs.id", name="fk_monitor_incident_run")
    )
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class MonitorNotification(MonitorBase):
    """Audit row for every DingTalk send decision and outcome."""

    __tablename__ = "monitor_notifications"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_monitor_notification"),
        CheckConstraint(
            "kind IN ('opened','repeat','recovered','test','daily_digest')",
            name="ck_monitor_notification_kind",
        ),
        CheckConstraint(
            "delivery_status IN ('pending','sent','failed','uncertain','skipped')",
            name="ck_monitor_notification_delivery_status",
        ),
        Index("ix_monitor_notification_incident_attempt", "incident_id", "attempted_at"),
        {"schema": MONITOR_SCHEMA},
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    incident_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            f"{MONITOR_SCHEMA}.monitor_incidents.id",
            name="fk_monitor_notification_incident",
        )
    )
    kind: Mapped[str] = mapped_column(String(20))
    delivery_status: Mapped[str] = mapped_column(String(20), default="pending")
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(80))
    safe_summary: Mapped[str | None] = mapped_column(Text)


class DailyMonitorSummary(MonitorBase):
    """Longer-lived daily roll-up; contains no customer content or identifiers."""

    __tablename__ = "daily_summaries"
    __table_args__ = (
        PrimaryKeyConstraint("summary_date", "component", name="pk_monitor_daily_summary"),
        CheckConstraint(
            "component IN ('worker','workbench','order','all')",
            name="ck_monitor_daily_component",
        ),
        {"schema": MONITOR_SCHEMA},
    )

    summary_date: Mapped[date] = mapped_column(Date)
    component: Mapped[str] = mapped_column(String(20))
    expected_runs: Mapped[int] = mapped_column(Integer, default=0)
    completed_runs: Mapped[int] = mapped_column(Integer, default=0)
    missed_runs: Mapped[int] = mapped_column(Integer, default=0)
    healthy_runs: Mapped[int] = mapped_column(Integer, default=0)
    abnormal_runs: Mapped[int] = mapped_column(Integer, default=0)
    error_runs: Mapped[int] = mapped_column(Integer, default=0)
    incidents_opened: Mapped[int] = mapped_column(Integer, default=0)
    incidents_recovered: Mapped[int] = mapped_column(Integer, default=0)
    notifications_sent: Mapped[int] = mapped_column(Integer, default=0)
    notifications_failed_or_uncertain: Mapped[int] = mapped_column(Integer, default=0)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
