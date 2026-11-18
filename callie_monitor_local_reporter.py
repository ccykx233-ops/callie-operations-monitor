"""One-shot local exporter; reads local state and writes only monitor reports."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from callie_monitor_local_sources import count_workbench_receipts, read_a7_runtime
from callie_monitor_reporters import (
    order_runtime_report_key,
    report_best_effort,
    workbench_hour_report_key,
)
from database import create_engine_for_url


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"CONFIG_MISSING_{name}")
    return value


def _previous_hour(now: datetime) -> tuple[datetime, datetime]:
    local_end = now.astimezone(SHANGHAI).replace(minute=0, second=0, microsecond=0)
    return (
        (local_end - timedelta(hours=1)).astimezone(timezone.utc),
        local_end.astimezone(timezone.utc),
    )


def _monitor_sessions():
    engine = create_engine_for_url(
        _required("MONITOR_DATABASE_URL"),
        connect_args={
            "connect_timeout": 8,
            "options": "-c statement_timeout=15000 -c lock_timeout=3000",
        },
    )
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def report_workbench(now: datetime) -> bool:
    local_url = _required("MONITOR_WORKBENCH_DATABASE_URL")
    if not local_url.startswith("sqlite:///"):
        raise ValueError("WORKBENCH_DATABASE_MUST_BE_SQLITE")
    start, end = _previous_hour(now)
    local_engine = create_engine(local_url)
    monitor_engine, sessions = _monitor_sessions()
    try:
        with local_engine.connect() as connection:
            count = count_workbench_receipts(connection, start=start, end=end)
        return report_best_effort(
            sessions,
            report_key=workbench_hour_report_key(
                _required("MONITOR_WORKBENCH_INSTANCE_ID"), start
            ),
            component="workbench_sync",
            instance_id=_required("MONITOR_WORKBENCH_INSTANCE_ID"),
            reported_at=now,
            status="healthy",
            metrics={"synced_count": count},
            period_start=start,
            period_end=end,
        )
    finally:
        local_engine.dispose()
        monitor_engine.dispose()


def report_order(now: datetime) -> bool:
    snapshot = read_a7_runtime(Path(_required("MONITOR_A7_STATE_ROOT")))
    monitor_engine, sessions = _monitor_sessions()
    instance_id = _required("MONITOR_ORDER_INSTANCE_ID")
    try:
        executor_ok = report_best_effort(
            sessions,
            report_key=order_runtime_report_key(
                "order_executor", instance_id, snapshot["executor_reported_at"]
            ),
            component="order_executor",
            instance_id=instance_id,
            reported_at=snapshot["executor_reported_at"],
            status="healthy",
            metrics={
                "executor_state": snapshot["executor_state"],
                "holds_task": snapshot["holds_task"],
            },
        )
        supervisor_ok = report_best_effort(
            sessions,
            report_key=order_runtime_report_key(
                "order_supervisor", instance_id, snapshot["supervisor_reported_at"]
            ),
            component="order_supervisor",
            instance_id=instance_id,
            reported_at=snapshot["supervisor_reported_at"],
            status="healthy" if snapshot["target_ready"] else "degraded",
            metrics={
                "supervisor_state": snapshot["supervisor_state"],
                "chrome_pool_ready": snapshot["chrome_pool_ready"],
                "target_ready": snapshot["target_ready"],
            },
        )
        return executor_ok and supervisor_ok
    finally:
        monitor_engine.dispose()


def main() -> int:
    now = datetime.now(timezone.utc)
    component = sys.argv[1] if len(sys.argv) == 2 else ""
    try:
        if component == "workbench":
            ok = report_workbench(now)
        elif component == "order":
            ok = report_order(now)
        else:
            raise ValueError("COMPONENT_MUST_BE_WORKBENCH_OR_ORDER")
        print(json.dumps({"component": component, "reported": bool(ok)}))
        return 0 if ok else 1
    except Exception:
        print(json.dumps({"component": component or "unknown", "reported": False}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
