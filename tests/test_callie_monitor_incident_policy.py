from datetime import datetime, timedelta, timezone

from callie_monitor_incident_policy import IncidentState, advance_incident


NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def test_first_confirmed_abnormal_opens_and_notifies():
    decision = advance_incident(None, abnormal=True, component="worker", now=NOW)
    assert decision.state.status == "open"
    assert decision.notification_kind == "opened"


def test_open_incident_is_silent_until_hourly_repeat_limit():
    opened = advance_incident(None, abnormal=True, component="worker", now=NOW).state
    quiet = advance_incident(
        opened, abnormal=True, component="worker", now=NOW + timedelta(minutes=59)
    )
    assert quiet.notification_kind is None
    repeated = advance_incident(
        quiet.state, abnormal=True, component="worker", now=NOW + timedelta(minutes=60)
    )
    assert repeated.notification_kind == "repeat"


def test_worker_requires_two_healthy_checks_before_recovery():
    opened = advance_incident(None, abnormal=True, component="worker", now=NOW).state
    first = advance_incident(
        opened, abnormal=False, component="worker", now=NOW + timedelta(minutes=10)
    )
    assert first.state.status == "open"
    assert first.notification_kind is None
    second = advance_incident(
        first.state, abnormal=False, component="worker", now=NOW + timedelta(minutes=20)
    )
    assert second.state.status == "recovered"
    assert second.notification_kind == "recovered"


def test_hourly_component_recovers_on_next_clean_audit_and_can_reopen():
    opened = advance_incident(None, abnormal=True, component="order", now=NOW).state
    recovered = advance_incident(
        opened, abnormal=False, component="order", now=NOW + timedelta(hours=1)
    )
    assert recovered.state.status == "recovered"
    reopened = advance_incident(
        recovered.state, abnormal=True, component="order", now=NOW + timedelta(hours=2)
    )
    assert reopened.state.status == "open"
    assert reopened.state.reopen_count == 1
    assert reopened.notification_kind == "opened"
