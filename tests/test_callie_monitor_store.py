from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from callie_monitor_store import finish_run, record_component_report, start_run


NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def test_start_run_uses_postgresql_idempotent_insert():
    session = Mock()
    session.execute.return_value.scalar_one_or_none.return_value = None
    assert start_run(
        session,
        schedule_key="worker:2026-11-18T00:00Z",
        component="worker",
        started_at=NOW,
    ) is None
    statement = session.execute.call_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (schedule_key) DO NOTHING" in compiled
    session.commit.assert_called_once_with()


def test_component_report_is_idempotent():
    session = Mock()
    session.execute.return_value.scalar_one_or_none.return_value = 1
    assert record_component_report(
        session,
        report_key="workbench:2026-11-18T00:00Z",
        component="workbench_sync",
        instance_id="workbench-main",
        reported_at=NOW,
        status="healthy",
        metrics={"synced_count": 3},
    ) is True
    compiled = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (report_key) DO NOTHING" in compiled


def test_finish_run_refuses_missing_or_already_finished_rows():
    session = Mock()
    session.get.return_value = None
    with pytest.raises(LookupError, match="MONITOR_RUN_NOT_FOUND"):
        finish_run(
            session,
            run_id=uuid4(),
            finished_at=NOW,
            status="healthy",
            duration_ms=10,
            rule_results={},
        )
    session.get.return_value = Mock(status="healthy")
    with pytest.raises(RuntimeError, match="MONITOR_RUN_ALREADY_FINISHED"):
        finish_run(
            session,
            run_id=uuid4(),
            finished_at=NOW,
            status="healthy",
            duration_ms=10,
            rule_results={},
        )
