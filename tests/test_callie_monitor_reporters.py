from datetime import datetime, timezone

from callie_monitor_reporters import (
    order_runtime_report_key,
    report_best_effort,
    workbench_hour_report_key,
)


NOW = datetime(2026, 11, 18, 9, 37, tzinfo=timezone.utc)


def test_reporter_failure_never_escapes_into_business_process():
    def broken_factory():
        raise RuntimeError("database unavailable")

    assert report_best_effort(
        broken_factory,
        report_key="key",
        component="workbench_sync",
        instance_id="main",
        reported_at=NOW,
        status="error",
        metrics={},
    ) is False


def test_report_keys_are_deterministic_and_hourly():
    assert workbench_hour_report_key("main", NOW).endswith(NOW.isoformat())
    first = order_runtime_report_key("order_executor", "main", NOW)
    second = order_runtime_report_key(
        "order_executor", "main", NOW.replace(minute=59)
    )
    assert first == second
