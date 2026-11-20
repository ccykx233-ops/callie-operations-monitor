"""Read-only SQL used by the independent monitor.

The statements intentionally contain no UPDATE/INSERT/DELETE and never select
customer message bodies, phone numbers or email addresses.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection


WORKER_SNAPSHOT_SQL = text("""
SELECT h.worker_id,
       h.instance_id,
       h.cabinet_id,
       h.status,
       h.heartbeat_at,
       h.active_task_id,
       h.last_error_code,
       t.status AS active_task_status,
       CASE WHEN t.claimed_at IS NULL THEN NULL
            ELSE EXTRACT(EPOCH FROM (:observed_at - t.claimed_at))
       END AS active_task_age_seconds
FROM dispatch_worker_health AS h
LEFT JOIN dispatch_compute_tasks AS t ON t.id = h.active_task_id
WHERE h.worker_id = :worker_id
""")


WORKBENCH_CLOUD_COUNT_SQL = text("""
SELECT COUNT(*)::bigint AS inbound_count
FROM dispatch_inbound_messages
WHERE ingested_at >= :window_start
  AND ingested_at < :window_end
  AND integration_id = ANY(CAST(:integration_ids AS text[]))
""")


WORKBENCH_REPORT_SQL = text("""
SELECT reported_at,
       period_start,
       period_end,
       status,
       CASE WHEN (metrics ->> 'synced_count') IS NOT NULL
            THEN (metrics ->> 'synced_count')::bigint
            ELSE NULL
       END AS synced_count,
       metrics
FROM callie_monitor.component_reports
WHERE component = 'workbench_sync'
  AND period_start = :window_start
  AND period_end = :window_end
ORDER BY reported_at DESC
LIMIT 1
""")


ORDER_REQUESTS_SQL = text("""
SELECT request.id AS request_id,
       request.status,
       request.created_at,
       request.completed_at,
       request.lease_expires_at,
       request.receipt_id,
       request.payload ->> 'order_number' AS order_number,
       request.payload ->> 'language' AS site,
       (request.receipt_id IS NOT NULL AND request.result IS NOT NULL) AS trusted_receipt,
       EXISTS (
           SELECT 1
           FROM dispatch_outbound_plans AS plan
           JOIN dispatch_actions AS action ON action.plan_id = plan.id
           WHERE plan.cycle_id = run.cycle_id
             AND action.kind = 'send_message'
             AND action.status = 'completed'
       ) AS outbound_confirmed
FROM dispatch_dify_agent_order_requests_v2 AS request
JOIN dispatch_dify_agent_runs_v1 AS run ON run.id = request.agent_run_id
WHERE (request.created_at >= :window_start AND request.created_at < :window_end)
   OR (
       request.status IN ('pending','running')
       AND request.created_at >= :historical_baseline_at
   )
ORDER BY request.created_at
""")


LATEST_ORDER_RUNTIME_REPORT_SQL = text("""
SELECT DISTINCT ON (component) component, reported_at, status, metrics
FROM callie_monitor.component_reports
WHERE component IN ('order_executor','order_supervisor')
ORDER BY component, reported_at DESC
""")


def read_worker_snapshot(
    connection: Connection,
    *,
    worker_id: str,
    expected_instance_id: str,
    expected_cabinet_id: str,
    observed_at: datetime,
) -> dict[str, Any]:
    row = connection.execute(
        WORKER_SNAPSHOT_SQL,
        {"worker_id": worker_id, "observed_at": observed_at},
    ).mappings().one_or_none()
    if row is None:
        return {
            "worker_id": worker_id,
            "status": None,
            "heartbeat_at": None,
            "active_task_id": None,
            "identity_match": False,
        }
    result = dict(row)
    result["identity_match"] = (
        result.get("instance_id") == expected_instance_id
        and result.get("cabinet_id") == expected_cabinet_id
    )
    return result


def read_workbench_hour(
    connection: Connection,
    *,
    window_start: datetime,
    window_end: datetime,
    integration_ids: Iterable[str],
) -> dict[str, Any]:
    integration_ids = tuple(dict.fromkeys(integration_ids))
    if not integration_ids:
        raise ValueError("INTEGRATION_IDS_REQUIRED")
    cloud_count = connection.execute(
        WORKBENCH_CLOUD_COUNT_SQL,
        {
            "window_start": window_start,
            "window_end": window_end,
            "integration_ids": list(integration_ids),
        },
    ).scalar_one()
    report = connection.execute(
        WORKBENCH_REPORT_SQL,
        {"window_start": window_start, "window_end": window_end},
    ).mappings().one_or_none()
    return {
        "cloud_inbound_count": int(cloud_count),
        "workbench_synced_count": None if report is None else report["synced_count"],
        "report_at": None if report is None else report["reported_at"],
    }


def read_order_requests(
    connection: Connection,
    *,
    window_start: datetime,
    window_end: datetime,
    historical_baseline_at: datetime,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        ORDER_REQUESTS_SQL,
        {
            "window_start": window_start,
            "window_end": window_end,
            "historical_baseline_at": historical_baseline_at,
        },
    ).mappings()
    return [dict(row) for row in rows]


def read_order_runtime(connection: Connection) -> dict[str, Any]:
    rows = list(connection.execute(LATEST_ORDER_RUNTIME_REPORT_SQL).mappings())
    if not rows:
        return {"executor_heartbeat_at": None, "chrome_pool_ready": None}
    by_component = {row["component"]: row for row in rows}
    executor = by_component.get("order_executor")
    supervisor = by_component.get("order_supervisor")
    executor_metrics = (
        executor["metrics"] if executor is not None and isinstance(executor["metrics"], dict) else {}
    )
    supervisor_metrics = (
        supervisor["metrics"]
        if supervisor is not None and isinstance(supervisor["metrics"], dict)
        else {}
    )
    return {
        "executor_heartbeat_at": None if executor is None else executor["reported_at"],
        "executor_state": executor_metrics.get("executor_state"),
        "chrome_pool_ready": supervisor_metrics.get("chrome_pool_ready"),
        "target_ready": supervisor_metrics.get("target_ready"),
    }
