from datetime import datetime, timedelta, timezone

from callie_monitor_rules import (
    evaluate_order_requests,
    evaluate_order_runtime,
    evaluate_workbench_hour,
    evaluate_worker,
)


NOW = datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc)


def codes(findings):
    return {finding.rule_code for finding in findings}


def test_worker_healthy_idle_is_normal():
    assert evaluate_worker({
        "worker_id": "worker-a",
        "status": "healthy",
        "heartbeat_at": NOW - timedelta(seconds=30),
        "active_task_id": None,
        "identity_match": True,
    }, now=NOW) == []


def test_worker_detects_stale_heartbeat_and_held_terminal_task():
    result = evaluate_worker({
        "worker_id": "worker-a",
        "status": "busy",
        "heartbeat_at": NOW - timedelta(minutes=3),
        "active_task_id": "task-1",
        "active_task_age_seconds": 601,
        "active_task_status": "cancelled",
        "identity_match": True,
    }, now=NOW)
    assert codes(result) == {
        "WORKER_HEARTBEAT_STALE",
        "WORKER_TASK_HELD_TOO_LONG",
        "WORKER_HOLDS_TERMINAL_TASK",
    }


def test_workbench_uses_hourly_counts_not_message_ids():
    assert evaluate_workbench_hour(
        period_ref="2026-11-18T15:00+08:00",
        cloud_inbound_count=7,
        workbench_synced_count=7,
        report_at=NOW - timedelta(minutes=2),
        now=NOW,
    ) == []
    result = evaluate_workbench_hour(
        period_ref="2026-11-18T15:00+08:00",
        cloud_inbound_count=7,
        workbench_synced_count=6,
        report_at=NOW - timedelta(minutes=2),
        now=NOW,
    )
    assert codes(result) == {"WORKBENCH_COUNT_MISMATCH"}


def test_workbench_no_messages_only_needs_fresh_report():
    assert evaluate_workbench_hour(
        period_ref="quiet-hour",
        cloud_inbound_count=0,
        workbench_synced_count=0,
        report_at=NOW - timedelta(minutes=69),
        now=NOW,
    ) == []
    result = evaluate_workbench_hour(
        period_ref="quiet-hour",
        cloud_inbound_count=0,
        workbench_synced_count=0,
        report_at=NOW - timedelta(minutes=71),
        now=NOW,
    )
    assert codes(result) == {"WORKBENCH_REPORT_STALE"}


def test_order_detects_unclaimed_expired_and_missing_delivery():
    result = evaluate_order_requests([
        {"request_id": "r1", "created_at": NOW - timedelta(minutes=6), "status": "pending"},
        {"request_id": "r2", "created_at": NOW - timedelta(minutes=2), "status": "running",
         "lease_expires_at": NOW - timedelta(seconds=1)},
        {"request_id": "r3", "created_at": NOW - timedelta(minutes=6), "status": "completed",
         "trusted_receipt": True, "outbound_confirmed": False, "order_number": "SAFE-ORDER"},
    ], now=NOW)
    assert codes(result) == {
        "ORDER_UNCLAIMED_TOO_LONG",
        "ORDER_LEASE_EXPIRED",
        "ORDER_REPLY_NOT_CONFIRMED",
    }
    delivery = next(item for item in result if item.rule_code == "ORDER_REPLY_NOT_CONFIRMED")
    assert delivery.facts["order_number"] == "SAFE-ORDER"


def test_order_runtime_requires_heartbeat_and_five_ready_browsers():
    result = evaluate_order_runtime({
        "executor_heartbeat_at": NOW - timedelta(minutes=71),
        "chrome_pool_ready": 4,
        "target_ready": False,
        "executor_state": "failed_closed",
    }, now=NOW)
    assert codes(result) == {
        "ORDER_EXECUTOR_HEARTBEAT_STALE",
        "ORDER_CHROME_POOL_NOT_READY",
        "ORDER_TARGET_NOT_READY",
        "ORDER_EXECUTOR_FAILED_CLOSED",
    }


def test_order_runtime_detects_quiesced_executor():
    result = evaluate_order_runtime({
        "executor_heartbeat_at": NOW,
        "chrome_pool_ready": 5,
        "target_ready": True,
        "executor_state": "quiesced",
    }, now=NOW)
    assert "ORDER_EXECUTOR_STATE_INVALID" in codes(result)


def test_approved_24_hour_handoff_text_is_not_an_order_alert():
    result = evaluate_order_requests([
        {
            "request_id": "r-approved-handoff",
            "created_at": NOW - timedelta(minutes=1),
            "status": "cancelled",
            "contains_banned_24h_promise": True,
        }
    ], now=NOW)
    assert "ORDER_BANNED_24H_PROMISE" not in codes(result)
