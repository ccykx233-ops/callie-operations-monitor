"""One-shot Callie operations monitor; intended for a 10-minute scheduler."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
import time
from typing import Callable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import sessionmaker

from callie_monitor_alerts import deliver_notification, deliver_text
from callie_monitor_daily_job import generate_daily_summary
from callie_monitor_digest import collect_digest, render_daily_digest
from callie_monitor_incidents_store import (
    NotificationDecision,
    apply_findings,
    finding_fingerprint,
)
from callie_monitor_queries import (
    read_order_requests,
    read_order_runtime,
    read_workbench_hour,
    read_worker_snapshot,
)
from callie_monitor_rules import (
    Finding,
    evaluate_order_requests,
    evaluate_order_runtime,
    evaluate_workbench_hour,
    evaluate_worker,
)
from callie_monitor_store import finish_run, record_notification, start_run
from database import create_engine_for_url
from dingtalk_robot import DingTalkRobot


SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class MonitorConfig:
    database_url: str
    worker_id: str
    worker_instance_id: str
    worker_cabinet_id: str
    integration_ids: tuple[str, ...]
    source_revision: str | None
    worker_confirm_delay_seconds: int
    active_mode: bool
    dingtalk_webhook_url: str | None
    dingtalk_secret: str | None


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"CONFIG_MISSING_{name}")
    return value


def load_config() -> MonitorConfig:
    integrations = tuple(
        dict.fromkeys(item.strip() for item in _required("MONITOR_INTEGRATION_IDS").split(",") if item.strip())
    )
    if len(integrations) != 18:
        raise ValueError("CONFIG_INTEGRATION_IDS_MUST_BE_18_UNIQUE_VALUES")
    delay = int(os.getenv("MONITOR_WORKER_CONFIRM_DELAY_SECONDS", "60"))
    if not 0 <= delay <= 120:
        raise ValueError("CONFIG_WORKER_CONFIRM_DELAY_INVALID")
    active = os.getenv("MONITOR_MODE", "dry_run").strip().lower() == "active"
    webhook = os.getenv("DINGTALK_WEBHOOK_URL", "").strip() or None
    secret = os.getenv("DINGTALK_SECRET", "").strip() or None
    if active and webhook is None:
        raise ValueError("CONFIG_DINGTALK_WEBHOOK_REQUIRED")
    return MonitorConfig(
        database_url=_required("MONITOR_DATABASE_URL"),
        worker_id=_required("MONITOR_WORKER_ID"),
        worker_instance_id=_required("MONITOR_WORKER_INSTANCE_ID"),
        worker_cabinet_id=_required("MONITOR_WORKER_CABINET_ID"),
        integration_ids=integrations,
        source_revision=os.getenv("GITHUB_SHA", "").strip() or None,
        worker_confirm_delay_seconds=delay,
        active_mode=active,
        dingtalk_webhook_url=webhook,
        dingtalk_secret=secret,
    )


class MonitorRunner:
    def __init__(
        self,
        config: MonitorConfig,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.now = now
        self.sleep = sleep
        self.engine = create_engine_for_url(config.database_url, connect_args={
            "connect_timeout": 8,
            "options": "-c statement_timeout=15000 -c lock_timeout=3000",
        })
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.robot = (
            DingTalkRobot(config.dingtalk_webhook_url, config.dingtalk_secret)
            if config.active_mode and config.dingtalk_webhook_url
            else None
        )
        self.results: dict[str, str] = {}

    def close(self) -> None:
        self.engine.dispose()

    def _record_and_deliver(
        self, decisions: list[NotificationDecision], *, observed_at: datetime
    ) -> None:
        for decision in decisions:
            status, payload_hash, error_code = deliver_notification(
                self.robot, decision, observed_at=observed_at
            )
            with self.sessions() as session:
                record_notification(
                    session,
                    incident_id=decision.incident_id,
                    kind=decision.kind,
                    delivery_status=status,
                    attempted_at=observed_at,
                    payload_sha256=payload_hash,
                    error_code=error_code,
                )

    def _finish_component(
        self,
        *,
        run_id,
        component: str,
        started_at: datetime,
        findings: list[Finding],
    ) -> None:
        finished_at = self.now()
        with self.sessions() as session:
            finish_run(
                session,
                run_id=run_id,
                finished_at=finished_at,
                status="abnormal" if findings else "healthy",
                duration_ms=max(0, int((finished_at - started_at).total_seconds() * 1000)),
                rule_results={"findings": [asdict(item) for item in findings]},
            )
        with self.sessions() as session:
            decisions = apply_findings(
                session,
                component=component,
                run_id=run_id,
                findings=findings,
                now=finished_at,
            )
        self._record_and_deliver(decisions, observed_at=finished_at)
        self.results[component] = "abnormal" if findings else "healthy"

    def _finish_error(
        self,
        *,
        run_id,
        component: str,
        started_at: datetime,
        error_code: str,
    ) -> None:
        finished_at = self.now()
        with self.sessions() as session:
            finish_run(
                session,
                run_id=run_id,
                finished_at=finished_at,
                status="error",
                duration_ms=max(0, int((finished_at - started_at).total_seconds() * 1000)),
                rule_results={},
                error_code=error_code,
            )
        self.results[component] = "error"

    def run_worker(self) -> None:
        started_at = self.now()
        bucket = started_at.replace(
            minute=(started_at.minute // 10) * 10, second=0, microsecond=0
        )
        with self.sessions() as session:
            run_id = start_run(
                session,
                schedule_key=f"worker:{bucket.isoformat()}",
                component="worker",
                started_at=started_at,
                source_revision=self.config.source_revision,
            )
        if run_id is None:
            self.results["worker"] = "duplicate_skipped"
            return
        try:
            with self.engine.connect() as connection:
                first = evaluate_worker(read_worker_snapshot(
                    connection,
                    worker_id=self.config.worker_id,
                    expected_instance_id=self.config.worker_instance_id,
                    expected_cabinet_id=self.config.worker_cabinet_id,
                    observed_at=started_at,
                ), now=started_at)
            confirmed = first
            if first and self.config.worker_confirm_delay_seconds:
                self.sleep(self.config.worker_confirm_delay_seconds)
                confirmed_at = self.now()
                with self.engine.connect() as connection:
                    second = evaluate_worker(read_worker_snapshot(
                        connection,
                        worker_id=self.config.worker_id,
                        expected_instance_id=self.config.worker_instance_id,
                        expected_cabinet_id=self.config.worker_cabinet_id,
                        observed_at=confirmed_at,
                    ), now=confirmed_at)
                first_keys = {finding_fingerprint(item) for item in first}
                confirmed = [
                    item for item in second if finding_fingerprint(item) in first_keys
                ]
            self._finish_component(
                run_id=run_id,
                component="worker",
                started_at=started_at,
                findings=confirmed,
            )
        except Exception:
            self._finish_error(
                run_id=run_id,
                component="worker",
                started_at=started_at,
                error_code="WORKER_CHECK_FAILED",
            )

    def _previous_hour(self, now: datetime) -> tuple[datetime, datetime, str]:
        local = now.astimezone(SHANGHAI)
        end_local = local.replace(minute=0, second=0, microsecond=0)
        start_local = end_local - timedelta(hours=1)
        return (
            start_local.astimezone(timezone.utc),
            end_local.astimezone(timezone.utc),
            start_local.isoformat(),
        )

    def run_workbench_hour(self) -> None:
        started_at = self.now()
        window_start, window_end, period_ref = self._previous_hour(started_at)
        with self.sessions() as session:
            run_id = start_run(
                session,
                schedule_key=f"workbench:{period_ref}",
                component="workbench",
                started_at=started_at,
                window_start=window_start,
                window_end=window_end,
                source_revision=self.config.source_revision,
            )
        if run_id is None:
            self.results["workbench"] = "duplicate_skipped"
            return
        try:
            with self.engine.connect() as connection:
                snapshot = read_workbench_hour(
                    connection,
                    window_start=window_start,
                    window_end=window_end,
                    integration_ids=self.config.integration_ids,
                )
            findings = evaluate_workbench_hour(
                period_ref=period_ref,
                cloud_inbound_count=snapshot["cloud_inbound_count"],
                workbench_synced_count=snapshot["workbench_synced_count"],
                report_at=snapshot["report_at"],
                now=started_at,
            )
            self._finish_component(
                run_id=run_id,
                component="workbench",
                started_at=started_at,
                findings=findings,
            )
        except Exception:
            self._finish_error(
                run_id=run_id,
                component="workbench",
                started_at=started_at,
                error_code="WORKBENCH_CHECK_FAILED",
            )

    def run_order_hour(self) -> None:
        started_at = self.now()
        window_start, window_end, period_ref = self._previous_hour(started_at)
        with self.sessions() as session:
            run_id = start_run(
                session,
                schedule_key=f"order:{period_ref}",
                component="order",
                started_at=started_at,
                window_start=window_start,
                window_end=window_end,
                source_revision=self.config.source_revision,
            )
        if run_id is None:
            self.results["order"] = "duplicate_skipped"
            return
        try:
            with self.engine.connect() as connection:
                requests = read_order_requests(
                    connection, window_start=window_start, window_end=window_end
                )
                runtime = read_order_runtime(connection)
            findings = evaluate_order_requests(requests, now=started_at)
            findings.extend(evaluate_order_runtime(runtime, now=started_at))
            self._finish_component(
                run_id=run_id,
                component="order",
                started_at=started_at,
                findings=findings,
            )
        except Exception:
            self._finish_error(
                run_id=run_id,
                component="order",
                started_at=started_at,
                error_code="ORDER_CHECK_FAILED",
            )

    def run_daily_summaries(self) -> None:
        now = self.now()
        local = now.astimezone(SHANGHAI)
        if local.hour != 0 or local.minute < 15:
            return
        summary_date = local.date() - timedelta(days=1)
        start_local = datetime.combine(summary_date, datetime.min.time(), SHANGHAI)
        end_local = start_local + timedelta(days=1)
        expected = {"worker": 144, "workbench": 24, "order": 24}
        with self.sessions() as session:
            for component, expected_runs in expected.items():
                generate_daily_summary(
                    session,
                    summary_date=summary_date,
                    component=component,
                    expected_runs=expected_runs,
                    window_start=start_local.astimezone(timezone.utc),
                    window_end=end_local.astimezone(timezone.utc),
                    generated_at=now,
                )
        self.results["daily"] = "written"

    def run_evening_digest(self) -> None:
        started_at = self.now()
        local = started_at.astimezone(SHANGHAI)
        if local.hour < 18:
            return
        start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        with self.sessions() as session:
            run_id = start_run(
                session,
                schedule_key=f"daily_digest:{local.date().isoformat()}",
                component="daily",
                started_at=started_at,
                window_start=start_local.astimezone(timezone.utc),
                window_end=started_at,
                source_revision=self.config.source_revision,
            )
        if run_id is None:
            self.results["daily_digest"] = "duplicate_skipped"
            return
        try:
            with self.sessions() as session:
                payload = collect_digest(
                    session,
                    window_start=start_local.astimezone(timezone.utc),
                    window_end=started_at,
                )
            content = render_daily_digest(payload, generated_at=local)
            status, payload_hash, error_code = deliver_text(self.robot, content)
            with self.sessions() as session:
                record_notification(
                    session,
                    incident_id=None,
                    kind="daily_digest",
                    delivery_status=status,
                    attempted_at=started_at,
                    payload_sha256=payload_hash,
                    error_code=error_code,
                    safe_summary=content,
                )
                finish_run(
                    session,
                    run_id=run_id,
                    finished_at=self.now(),
                    status="healthy" if status in {"sent", "skipped"} else "error",
                    duration_ms=max(
                        0, int((self.now() - started_at).total_seconds() * 1000)
                    ),
                    rule_results={"delivery_status": status},
                    error_code=error_code,
                )
            self.results["daily_digest"] = (
                status if status in {"sent", "skipped"} else "error"
            )
        except Exception:
            self._finish_error(
                run_id=run_id,
                component="daily_digest",
                started_at=started_at,
                error_code="DAILY_DIGEST_FAILED",
            )

    def run(self) -> dict[str, str]:
        self.run_worker()
        self.run_workbench_hour()
        self.run_order_hour()
        self.run_daily_summaries()
        self.run_evening_digest()
        return dict(self.results)


def main() -> int:
    runner = None
    try:
        config = load_config()
        runner = MonitorRunner(config)
        result = runner.run()
        print(json.dumps({"monitor": "complete", "results": result}, sort_keys=True))
        return 0 if all(value not in {"error"} for value in result.values()) else 1
    except (ValueError, TypeError):
        print(json.dumps({"monitor": "configuration_error"}))
        return 2
    except Exception:
        print(json.dumps({"monitor": "failed"}))
        return 1
    finally:
        if runner is not None:
            runner.close()


if __name__ == "__main__":
    raise SystemExit(main())
