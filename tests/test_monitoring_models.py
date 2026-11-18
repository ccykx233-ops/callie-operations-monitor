from datetime import date, datetime, timezone
import importlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine


ROOT = Path(__file__).resolve().parents[1]


def test_monitor_tables_are_isolated_and_complete():
    models = importlib.import_module("monitoring_models")
    assert set(models.MonitorBase.metadata.tables) == {
        "callie_monitor.monitor_runs",
        "callie_monitor.component_reports",
        "callie_monitor.monitor_incidents",
        "callie_monitor.monitor_notifications",
        "callie_monitor.daily_summaries",
    }
    assert all(table.schema == "callie_monitor" for table in models.MonitorBase.metadata.tables.values())


def test_import_has_no_database_side_effects():
    code = """
from unittest.mock import patch
with patch('sqlalchemy.create_engine', side_effect=AssertionError('engine on import')), \\
     patch('sqlalchemy.schema.MetaData.create_all', side_effect=AssertionError('create on import')):
    import monitoring_models
    import scripts.migrate_monitoring
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_migration_rejects_non_postgresql():
    migration = importlib.import_module("scripts.migrate_monitoring")
    engine = create_engine("sqlite://")
    try:
        with pytest.raises(ValueError, match="^POSTGRESQL_REQUIRED$"):
            migration.migrate(engine)
    finally:
        engine.dispose()


def test_cli_without_apply_does_not_read_database_url(monkeypatch):
    migration = importlib.import_module("scripts.migrate_monitoring")

    class ForbiddenEnvironment(dict):
        def get(self, key, default=None):
            if key == "DATABASE_URL":
                raise AssertionError("read before apply")
            return super().get(key, default)

    monkeypatch.setattr(os, "environ", ForbiddenEnvironment())
    monkeypatch.setattr(sys, "argv", ["migrate_monitoring.py"])
    with pytest.raises(SystemExit) as exc:
        migration.main()
    assert exc.value.code == 2


def test_cli_redacts_database_url_on_failure(monkeypatch, capsys):
    migration = importlib.import_module("scripts.migrate_monitoring")
    secret = "postgresql://user:password@host/private-secret"
    monkeypatch.setattr(os, "environ", {"DATABASE_URL": secret})
    monkeypatch.setattr(sys, "argv", ["migrate_monitoring.py", "--apply"])
    monkeypatch.setattr(migration, "create_engine_for_url", Mock(side_effect=RuntimeError(secret)))
    assert migration.main() == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert secret not in output
    assert "password" not in output
