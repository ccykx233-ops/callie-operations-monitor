from datetime import datetime, timezone
from uuid import uuid4

from callie_monitor_alerts import deliver_notification, render_notification
from callie_monitor_incidents_store import NotificationDecision


NOW = datetime(2026, 11, 18, 9, 30, tzinfo=timezone.utc)


def decision(**changes):
    values = {
        "incident_id": uuid4(),
        "kind": "opened",
        "component": "order",
        "rule_code": "ORDER_REPLY_NOT_CONFIRMED",
        "subject_ref": "request-safe-1",
        "details": {"order_number": "ORDER-SAFE-1"},
    }
    values.update(changes)
    return NotificationDecision(**values)


def test_order_alert_contains_safe_locators_and_shanghai_time():
    text = render_notification(decision(), observed_at=NOW)
    assert "11-18 17:30" in text
    assert "ORDER-SAFE-1" in text
    assert "request-safe-1" in text
    assert "客户消息" not in text


def test_dry_run_skips_delivery_but_still_hashes_payload():
    status, payload_hash, error = deliver_notification(None, decision(), observed_at=NOW)
    assert status == "skipped"
    assert len(payload_hash) == 64
    assert error is None
