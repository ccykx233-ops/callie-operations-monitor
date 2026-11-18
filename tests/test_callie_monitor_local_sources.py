from datetime import datetime, timezone
import json

from sqlalchemy import create_engine, text

from callie_monitor_local_sources import (
    count_workbench_receipts,
    install_workbench_receipt_time,
    read_a7_runtime,
    receipt_rows,
    save_workbench_receipts,
)


def test_workbench_receipts_store_only_hashes_and_hour_timestamp():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE wb_message_observations "
            "(identity_hash VARCHAR(64) PRIMARY KEY, verified_at BIGINT NOT NULL)"
        ))
        install_workbench_receipt_time(connection)
        rows = receipt_rows(
            app_id="app-safe",
            conversation_id="conversation-safe",
            integration_id="channel-safe",
            messages=[
                {
                    "id": "raw-message-id",
                    "author": "customer",
                    "received_at": "2026-11-18T09:15:00Z",
                    "text": "must never be stored",
                },
                {
                    "id": "bot-message-id",
                    "author": "business",
                    "received_at": "2026-11-18T09:16:00Z",
                },
            ],
            verified_at=datetime(2026, 11, 18, 9, 16, tzinfo=timezone.utc),
        )
        save_workbench_receipts(connection, rows)
        assert count_workbench_receipts(
            connection,
            start=datetime(2026, 11, 18, 9, tzinfo=timezone.utc),
            end=datetime(2026, 11, 18, 10, tzinfo=timezone.utc),
        ) == 1
        stored = connection.execute(text(
            "SELECT identity_hash,received_at,verified_at "
            "FROM wb_message_observations"
        )).mappings().one()
    engine.dispose()
    assert stored["identity_hash"] not in {"raw-message-id", "bot-message-id"}
    assert set(stored) == {"identity_hash", "received_at", "verified_at"}


def test_a7_runtime_reads_only_safe_state(tmp_path):
    (tmp_path / "a7-supervisor.status").write_text(
        "updated_at=2026-11-18T09:50:42Z\n"
        "state=healthy\npool=5/5\ntarget_counter=1\n",
        encoding="utf-8",
    )
    (tmp_path / "a7-executor-state.json").write_text(
        json.dumps({
            "state": "claiming",
            "holds_task": False,
            "updated_at": "2026-11-18T09:50:40Z",
        }),
        encoding="utf-8",
    )
    snapshot = read_a7_runtime(tmp_path)
    assert snapshot["chrome_pool_ready"] == 5
    assert snapshot["target_ready"] is True
    assert snapshot["executor_state"] == "claiming"


def test_a7_runtime_marks_failed_closed_as_not_ready(tmp_path):
    (tmp_path / "a7-supervisor.status").write_text(
        "updated_at=2026-11-18T09:50:42Z\nstate=failed_closed_health_unknown\n",
        encoding="utf-8",
    )
    (tmp_path / "a7-executor-state.json").write_text(
        json.dumps({
            "state": "quiesced",
            "holds_task": False,
            "updated_at": "2026-11-18T09:50:40Z",
        }),
        encoding="utf-8",
    )
    snapshot = read_a7_runtime(tmp_path)
    assert snapshot["chrome_pool_ready"] is None
    assert snapshot["target_ready"] is False
