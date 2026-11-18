"""Persist deduplicated incident transitions in the isolated monitor schema."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Iterable
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from callie_monitor_incident_policy import IncidentState, advance_incident
from callie_monitor_rules import Finding
from monitoring_models import MonitorIncident


@dataclass(frozen=True)
class NotificationDecision:
    incident_id: UUID
    kind: str
    component: str
    rule_code: str
    subject_ref: str | None
    details: dict


def finding_fingerprint(finding: Finding) -> str:
    raw = "|".join((finding.component, finding.rule_code, finding.subject_ref or "-"))
    return sha256(raw.encode("utf-8")).hexdigest()


def _to_state(row: MonitorIncident) -> IncidentState:
    return IncidentState(
        status=row.status,
        consecutive_failures=row.consecutive_failures,
        consecutive_successes=row.consecutive_successes,
        reopen_count=row.reopen_count,
        first_detected_at=row.first_detected_at,
        last_detected_at=row.last_detected_at,
        recovered_at=row.recovered_at,
        last_notified_at=row.last_notified_at,
    )


def _apply_state(row: MonitorIncident, state: IncidentState, run_id: UUID) -> None:
    row.status = state.status
    row.consecutive_failures = state.consecutive_failures
    row.consecutive_successes = state.consecutive_successes
    row.reopen_count = state.reopen_count
    row.first_detected_at = state.first_detected_at
    row.last_detected_at = state.last_detected_at
    row.recovered_at = state.recovered_at
    row.last_notified_at = state.last_notified_at
    row.last_run_id = run_id


def apply_findings(
    session: Session,
    *,
    component: str,
    run_id: UUID,
    findings: Iterable[Finding],
    now: datetime,
) -> list[NotificationDecision]:
    """Update incidents for one complete component check in one transaction."""

    current = {finding_fingerprint(item): item for item in findings}
    rows = session.execute(
        select(MonitorIncident)
        .where(
            MonitorIncident.component == component,
            or_(
                MonitorIncident.status == "open",
                MonitorIncident.fingerprint.in_(tuple(current)),
            ),
        )
        .with_for_update()
    ).scalars().all()
    by_fingerprint = {row.fingerprint: row for row in rows}
    notifications: list[NotificationDecision] = []

    for fingerprint, finding in current.items():
        row = by_fingerprint.get(fingerprint)
        decision = advance_incident(
            None if row is None else _to_state(row),
            abnormal=True,
            component=component,
            now=now,
        )
        if decision.state is None:
            raise RuntimeError("INCIDENT_STATE_MISSING")
        if row is None:
            row = MonitorIncident(
                fingerprint=fingerprint,
                component=component,
                rule_code=finding.rule_code,
                subject_ref=finding.subject_ref,
                status=decision.state.status,
                first_detected_at=decision.state.first_detected_at,
                last_detected_at=decision.state.last_detected_at,
                recovered_at=decision.state.recovered_at,
                last_notified_at=decision.state.last_notified_at,
                consecutive_failures=decision.state.consecutive_failures,
                consecutive_successes=decision.state.consecutive_successes,
                reopen_count=decision.state.reopen_count,
                last_run_id=run_id,
                details=finding.facts,
            )
            session.add(row)
            session.flush()
            by_fingerprint[fingerprint] = row
        else:
            _apply_state(row, decision.state, run_id)
            row.details = finding.facts
        if decision.notification_kind:
            notifications.append(NotificationDecision(
                incident_id=row.id,
                kind=decision.notification_kind,
                component=component,
                rule_code=row.rule_code,
                subject_ref=row.subject_ref,
                details=row.details,
            ))

    for fingerprint, row in by_fingerprint.items():
        if fingerprint in current or row.status != "open":
            continue
        decision = advance_incident(
            _to_state(row), abnormal=False, component=component, now=now
        )
        if decision.state is None:
            continue
        _apply_state(row, decision.state, run_id)
        if decision.notification_kind:
            notifications.append(NotificationDecision(
                incident_id=row.id,
                kind=decision.notification_kind,
                component=component,
                rule_code=row.rule_code,
                subject_ref=row.subject_ref,
                details=row.details,
            ))

    session.commit()
    return notifications
