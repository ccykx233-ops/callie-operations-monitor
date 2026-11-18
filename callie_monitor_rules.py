"""Pure, deterministic health rules used by the external monitor runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable


@dataclass(frozen=True)
class Finding:
    component: str
    rule_code: str
    subject_ref: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)


def _age_seconds(now: datetime, value: datetime | None) -> float | None:
    return None if value is None else max(0.0, (now - value).total_seconds())


def evaluate_worker(snapshot: dict[str, Any], *, now: datetime) -> list[Finding]:
    """Evaluate the single cloud compute worker without changing its state."""

    findings: list[Finding] = []
    worker_id = str(snapshot.get("worker_id") or "unknown")
    heartbeat_age = _age_seconds(now, snapshot.get("heartbeat_at"))
    if heartbeat_age is None or heartbeat_age > 120:
        findings.append(Finding("worker", "WORKER_HEARTBEAT_STALE", worker_id, {
            "heartbeat_age_seconds": heartbeat_age,
        }))
    status = snapshot.get("status")
    if status not in {"healthy", "busy"}:
        findings.append(Finding("worker", "WORKER_STATUS_INVALID", worker_id, {
            "status": status,
        }))
    active_task_id = snapshot.get("active_task_id")
    if status == "busy" and not active_task_id:
        findings.append(Finding("worker", "WORKER_BUSY_WITHOUT_TASK", worker_id))
    held_seconds = snapshot.get("active_task_age_seconds")
    if active_task_id and isinstance(held_seconds, (int, float)) and held_seconds > 600:
        findings.append(Finding("worker", "WORKER_TASK_HELD_TOO_LONG", str(active_task_id), {
            "held_seconds": int(held_seconds),
        }))
    if active_task_id and snapshot.get("active_task_status") in {"completed", "cancelled", "failed"}:
        findings.append(Finding("worker", "WORKER_HOLDS_TERMINAL_TASK", str(active_task_id), {
            "task_status": snapshot.get("active_task_status"),
        }))
    if snapshot.get("identity_match") is False:
        findings.append(Finding("worker", "WORKER_IDENTITY_MISMATCH", worker_id))
    return findings


def evaluate_workbench_hour(
    *,
    period_ref: str,
    cloud_inbound_count: int,
    workbench_synced_count: int | None,
    report_at: datetime | None,
    now: datetime,
) -> list[Finding]:
    """Compare aggregate hourly counts; deliberately does not match message IDs."""

    findings: list[Finding] = []
    report_age = _age_seconds(now, report_at)
    if report_age is None or report_age > 4200:
        findings.append(Finding("workbench", "WORKBENCH_REPORT_STALE", period_ref, {
            "report_age_seconds": report_age,
        }))
    if workbench_synced_count is None:
        findings.append(Finding("workbench", "WORKBENCH_COUNT_MISSING", period_ref, {
            "cloud_count": cloud_inbound_count,
        }))
    elif cloud_inbound_count != workbench_synced_count:
        findings.append(Finding("workbench", "WORKBENCH_COUNT_MISMATCH", period_ref, {
            "cloud_count": cloud_inbound_count,
            "workbench_count": workbench_synced_count,
            "difference": workbench_synced_count - cloud_inbound_count,
        }))
    return findings


def evaluate_order_requests(
    requests: Iterable[dict[str, Any]], *, now: datetime
) -> list[Finding]:
    """Evaluate current-hour requests plus any older still-open request."""

    findings: list[Finding] = []
    for item in requests:
        request_id = str(item.get("request_id") or "unknown")
        age = _age_seconds(now, item.get("created_at"))
        status = item.get("status")
        facts = {
            "order_number": item.get("order_number"),
            "site": item.get("site"),
            "age_seconds": None if age is None else int(age),
            "status": status,
        }
        if age is None:
            findings.append(Finding("order", "ORDER_CREATED_AT_MISSING", request_id, facts))
            continue
        if status == "pending" and age > 300:
            findings.append(Finding("order", "ORDER_UNCLAIMED_TOO_LONG", request_id, facts))
        if status == "running" and item.get("lease_expires_at") is not None:
            if item["lease_expires_at"] < now:
                findings.append(Finding("order", "ORDER_LEASE_EXPIRED", request_id, facts))
        if status in {"timed_out", "failed_closed"}:
            findings.append(Finding("order", "ORDER_TERMINAL_FAILURE", request_id, facts))
        if item.get("target_ready") is False:
            findings.append(Finding("order", "ORDER_TARGET_NOT_READY", request_id, facts))
        if status == "completed" and not item.get("trusted_receipt"):
            findings.append(Finding("order", "ORDER_COMPLETED_WITHOUT_TRUSTED_RECEIPT", request_id, facts))
        if item.get("trusted_receipt") and not item.get("outbound_confirmed") and age > 300:
            findings.append(Finding("order", "ORDER_REPLY_NOT_CONFIRMED", request_id, facts))
        if item.get("reply_claims_success") and not item.get("trusted_receipt"):
            findings.append(Finding("order", "ORDER_UNTRUSTED_SUCCESS_REPLY", request_id, facts))
    return findings


def evaluate_order_runtime(snapshot: dict[str, Any], *, now: datetime) -> list[Finding]:
    findings: list[Finding] = []
    heartbeat_age = _age_seconds(now, snapshot.get("executor_heartbeat_at"))
    if heartbeat_age is None or heartbeat_age > 4200:
        findings.append(Finding("order", "ORDER_EXECUTOR_HEARTBEAT_STALE", facts={
            "heartbeat_age_seconds": heartbeat_age,
        }))
    if snapshot.get("chrome_pool_ready") != 5:
        findings.append(Finding("order", "ORDER_CHROME_POOL_NOT_READY", facts={
            "ready": snapshot.get("chrome_pool_ready"), "expected": 5,
        }))
    if snapshot.get("target_ready") is False:
        findings.append(Finding("order", "ORDER_TARGET_NOT_READY", facts={
            "target_ready": False,
        }))
    if snapshot.get("executor_state") == "failed_closed":
        findings.append(Finding("order", "ORDER_EXECUTOR_FAILED_CLOSED", facts={
            "executor_state": "failed_closed",
        }))
    elif snapshot.get("executor_state") not in {
        "starting", "idle", "claiming", "waiting_for_counter", "executing"
    }:
        findings.append(Finding("order", "ORDER_EXECUTOR_STATE_INVALID", facts={
            "executor_state": snapshot.get("executor_state"),
        }))
    return findings
