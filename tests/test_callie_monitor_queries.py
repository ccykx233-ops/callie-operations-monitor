from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from callie_monitor_queries import (
    LATEST_ORDER_RUNTIME_REPORT_SQL,
    ORDER_REQUESTS_SQL,
    WORKBENCH_CLOUD_COUNT_SQL,
    WORKBENCH_REPORT_SQL,
    WORKER_SNAPSHOT_SQL,
    read_workbench_hour,
    read_worker_snapshot,
)


NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def test_business_queries_are_read_only_and_do_not_select_message_text():
    for statement in (
        WORKER_SNAPSHOT_SQL,
        WORKBENCH_CLOUD_COUNT_SQL,
        WORKBENCH_REPORT_SQL,
        ORDER_REQUESTS_SQL,
        LATEST_ORDER_RUNTIME_REPORT_SQL,
    ):
        sql = str(statement).upper()
        assert "UPDATE " not in sql
        assert "DELETE " not in sql
        assert "INSERT " not in sql
    assert "MESSAGE_TEXT" not in str(WORKBENCH_CLOUD_COUNT_SQL).upper()
    assert "24 HOURS" not in str(ORDER_REQUESTS_SQL).upper()
    assert "24 HORAS" not in str(ORDER_REQUESTS_SQL).upper()


def test_workbench_count_requires_explicit_allowlisted_integrations():
    connection = Mock()
    with pytest.raises(ValueError, match="INTEGRATION_IDS_REQUIRED"):
        read_workbench_hour(
            connection,
            window_start=NOW,
            window_end=NOW,
            integration_ids=[],
        )
    connection.execute.assert_not_called()


def test_missing_worker_row_becomes_an_abnormal_snapshot():
    connection = Mock()
    connection.execute.return_value.mappings.return_value.one_or_none.return_value = None
    result = read_worker_snapshot(
        connection,
        worker_id="worker-a",
        expected_instance_id="instance-a",
        expected_cabinet_id="cabinet-a",
        observed_at=NOW,
    )
    assert result == {
        "worker_id": "worker-a",
        "status": None,
        "heartbeat_at": None,
        "active_task_id": None,
        "identity_match": False,
    }
