"""Local, privacy-preserving sources for workbench and A7 monitor reports."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Iterable

from sqlalchemy import text


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("TIMESTAMP_TIMEZONE_REQUIRED")
    return value.astimezone(timezone.utc)


def message_identity(
    app_id: str,
    conversation_id: str,
    integration_id: str,
    message_id: str,
) -> str:
    alias = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
    raw = json.dumps(
        [app_id, conversation_id, integration_id, alias],
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def receipt_rows(
    *,
    app_id: str,
    conversation_id: str,
    integration_id: str,
    messages: Iterable[dict],
    verified_at: datetime,
) -> list[dict]:
    """Return deduplicated receipt rows without message text or raw identifiers."""

    verified_epoch = int(_utc(verified_at).timestamp())
    rows: dict[str, dict] = {}
    for item in messages:
        if item.get("author") != "customer":
            continue
        message_id = item.get("id")
        received_at = item.get("received_at")
        if not isinstance(message_id, str) or not message_id:
            raise ValueError("WORKBENCH_RECEIPT_MESSAGE_ID_INVALID")
        if isinstance(received_at, str):
            received_at = _parse_iso(received_at)
        if not isinstance(received_at, datetime):
            raise ValueError("WORKBENCH_RECEIPT_TIME_INVALID")
        identity_hash = message_identity(
            app_id, conversation_id, integration_id, message_id
        )
        rows[identity_hash] = {
            "identity_hash": identity_hash,
            "received_at": int(_utc(received_at).timestamp()),
            "verified_at": verified_epoch,
        }
    return list(rows.values())


def install_workbench_receipt_time(connection) -> None:
    """Explicit additive migration; never called during import or reporter startup."""

    columns = {
        row[1]
        for row in connection.execute(text("PRAGMA table_info(wb_message_observations)"))
    }
    if "identity_hash" not in columns or "verified_at" not in columns:
        raise RuntimeError("WORKBENCH_RECEIPT_TABLE_MISSING")
    if "received_at" not in columns:
        connection.execute(
            text("ALTER TABLE wb_message_observations ADD COLUMN received_at BIGINT")
        )
    connection.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_wb_observation_received "
            "ON wb_message_observations(received_at)"
        )
    )


def save_workbench_receipts(connection, rows: Iterable[dict]) -> None:
    values = list(rows)
    if not values:
        return
    connection.execute(
        text(
            "INSERT INTO wb_message_observations"
            "(identity_hash,verified_at,received_at) "
            "VALUES (:identity_hash,:verified_at,:received_at) "
            "ON CONFLICT(identity_hash) DO UPDATE SET "
            "verified_at=excluded.verified_at, "
            "received_at=coalesce(wb_message_observations.received_at,excluded.received_at)"
        ),
        values,
    )


def count_workbench_receipts(connection, *, start: datetime, end: datetime) -> int:
    start_epoch = int(_utc(start).timestamp())
    end_epoch = int(_utc(end).timestamp())
    return int(
        connection.execute(
            text(
                "SELECT count(*) FROM wb_message_observations "
                "WHERE received_at>=:start AND received_at<:end"
            ),
            {"start": start_epoch, "end": end_epoch},
        ).scalar_one()
    )


def _parse_iso(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("LOCAL_STATE_TIMESTAMP_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("LOCAL_STATE_TIMESTAMP_INVALID") from None
    if parsed.tzinfo is None:
        raise ValueError("LOCAL_STATE_TIMESTAMP_INVALID")
    return parsed.astimezone(timezone.utc)


def read_key_value_status(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and "\n" not in value and "\r" not in value:
            values[key] = value
    return values


def read_a7_runtime(state_root: Path) -> dict:
    supervisor = read_key_value_status(state_root / "a7-supervisor.status")
    executor = json.loads(
        (state_root / "a7-executor-state.json").read_text(encoding="utf-8")
    )
    if not isinstance(executor, dict):
        raise ValueError("A7_EXECUTOR_STATE_INVALID")
    pool_text = supervisor.get("pool")
    pool_ready = None
    if isinstance(pool_text, str) and "/" in pool_text:
        head, tail = pool_text.split("/", 1)
        if head.isdigit() and tail == "5":
            pool_ready = int(head)
    supervisor_state = supervisor.get("state")
    target_ready = bool(
        pool_ready == 5
        and supervisor_state
        and not supervisor_state.startswith("failed_closed")
        and supervisor_state != "a7_start_failed"
    )
    return {
        "executor_reported_at": _parse_iso(executor.get("updated_at")),
        "executor_state": executor.get("state"),
        "holds_task": executor.get("holds_task"),
        "supervisor_reported_at": _parse_iso(supervisor.get("updated_at")),
        "supervisor_state": supervisor_state,
        "chrome_pool_ready": pool_ready,
        "target_ready": target_ready,
    }
