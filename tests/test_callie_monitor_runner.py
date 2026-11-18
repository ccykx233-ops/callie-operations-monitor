from datetime import datetime, timezone

import pytest

import callie_monitor_runner as runner_module
from callie_monitor_runner import MonitorRunner, load_config


def env_values():
    return {
        "MONITOR_DATABASE_URL": "postgresql://user:pass@host/db",
        "MONITOR_WORKER_ID": "worker-a",
        "MONITOR_WORKER_INSTANCE_ID": "instance-a",
        "MONITOR_WORKER_CABINET_ID": "cabinet-a",
        "MONITOR_INTEGRATION_IDS": ",".join(f"channel-{index}" for index in range(18)),
    }


def test_config_requires_exactly_eighteen_unique_integrations(monkeypatch):
    values = env_values()
    values["MONITOR_INTEGRATION_IDS"] = "one,two"
    monkeypatch.setattr(runner_module.os, "environ", values)
    with pytest.raises(ValueError, match="MUST_BE_18"):
        load_config()


def test_config_defaults_to_dry_run_without_dingtalk(monkeypatch):
    monkeypatch.setattr(runner_module.os, "environ", env_values())
    config = load_config()
    assert config.active_mode is False
    assert config.dingtalk_webhook_url is None
    assert len(config.integration_ids) == 18


def test_active_mode_requires_dingtalk_webhook(monkeypatch):
    values = env_values()
    values["MONITOR_MODE"] = "active"
    monkeypatch.setattr(runner_module.os, "environ", values)
    with pytest.raises(ValueError, match="DINGTALK_WEBHOOK_REQUIRED"):
        load_config()


def test_previous_hour_uses_complete_shanghai_natural_hour():
    runner = object.__new__(MonitorRunner)
    start, end, period = runner._previous_hour(
        datetime(2026, 11, 18, 9, 37, tzinfo=timezone.utc)
    )
    assert start == datetime(2026, 11, 18, 8, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
    assert period.startswith("2026-11-18T16:00:00+08:00")
