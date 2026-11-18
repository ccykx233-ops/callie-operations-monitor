"""Pure incident state machine for alert deduplication and recovery."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta


@dataclass(frozen=True)
class IncidentState:
    status: str
    consecutive_failures: int
    consecutive_successes: int
    reopen_count: int
    first_detected_at: datetime
    last_detected_at: datetime
    recovered_at: datetime | None = None
    last_notified_at: datetime | None = None


@dataclass(frozen=True)
class IncidentDecision:
    state: IncidentState | None
    notification_kind: str | None


def advance_incident(
    current: IncidentState | None,
    *,
    abnormal: bool,
    component: str,
    now: datetime,
    repeat_after: timedelta = timedelta(minutes=60),
) -> IncidentDecision:
    """Advance one fingerprint after a confirmed check.

    Worker recovery needs two consecutive healthy checks.  Hourly checks recover
    on the next clean audit because waiting for a second audit would add an hour.
    """

    if abnormal:
        if current is None:
            return IncidentDecision(
                IncidentState(
                    status="open",
                    consecutive_failures=1,
                    consecutive_successes=0,
                    reopen_count=0,
                    first_detected_at=now,
                    last_detected_at=now,
                    last_notified_at=now,
                ),
                "opened",
            )
        if current.status == "recovered":
            return IncidentDecision(
                IncidentState(
                    status="open",
                    consecutive_failures=1,
                    consecutive_successes=0,
                    reopen_count=current.reopen_count + 1,
                    first_detected_at=current.first_detected_at,
                    last_detected_at=now,
                    last_notified_at=now,
                ),
                "opened",
            )
        should_repeat = (
            current.last_notified_at is None
            or now - current.last_notified_at >= repeat_after
        )
        return IncidentDecision(
            replace(
                current,
                consecutive_failures=current.consecutive_failures + 1,
                consecutive_successes=0,
                last_detected_at=now,
                last_notified_at=now if should_repeat else current.last_notified_at,
            ),
            "repeat" if should_repeat else None,
        )

    if current is None or current.status == "recovered":
        return IncidentDecision(current, None)
    successes = current.consecutive_successes + 1
    threshold = 2 if component == "worker" else 1
    if successes < threshold:
        return IncidentDecision(
            replace(current, consecutive_successes=successes),
            None,
        )
    return IncidentDecision(
        replace(
            current,
            status="recovered",
            consecutive_successes=successes,
            recovered_at=now,
            last_notified_at=now,
        ),
        "recovered",
    )
