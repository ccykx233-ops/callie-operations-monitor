"""Build the concise 18:00 Asia/Shanghai operations digest."""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from callie_monitor_alerts import _COMPONENT_LABELS, _RULE_LABELS
from monitoring_models import MonitorIncident, MonitorNotification, MonitorRun


def collect_digest(
    session: Session,
    *,
    window_start: datetime,
    window_end: datetime,
) -> dict:
    runs = session.execute(
        select(MonitorRun).where(
            MonitorRun.started_at >= window_start,
            MonitorRun.started_at < window_end,
        )
    ).scalars().all()
    incidents = session.execute(
        select(MonitorIncident).where(
            or_(
                MonitorIncident.status == "open",
                (
                    (MonitorIncident.first_detected_at >= window_start)
                    & (MonitorIncident.first_detected_at < window_end)
                ),
                (
                    (MonitorIncident.recovered_at >= window_start)
                    & (MonitorIncident.recovered_at < window_end)
                ),
            )
        )
    ).scalars().all()
    notification_failures = session.execute(
        select(MonitorNotification).where(
            MonitorNotification.attempted_at >= window_start,
            MonitorNotification.attempted_at < window_end,
            MonitorNotification.delivery_status.in_(("failed", "uncertain")),
        )
    ).scalars().all()
    status_counts = Counter((row.component, row.status) for row in runs)
    return {
        "open_incidents": [
            {
                "component": row.component,
                "rule_code": row.rule_code,
                "subject_ref": row.subject_ref,
                "details": row.details or {},
            }
            for row in incidents
            if row.status == "open"
        ],
        "opened_today": sum(
            window_start <= row.first_detected_at < window_end for row in incidents
        ),
        "recovered_today": sum(
            row.recovered_at is not None
            and window_start <= row.recovered_at < window_end
            for row in incidents
        ),
        "run_errors": {
            component: status_counts[(component, "error")]
            for component in ("worker", "workbench", "order")
        },
        "notification_failures": len(notification_failures),
    }


def render_daily_digest(payload: dict, *, generated_at: datetime) -> str:
    open_incidents = list(payload.get("open_incidents") or [])
    lines = [
        "Callie 客服运行日报",
        f"日期：{generated_at.strftime('%Y-%m-%d')}（截至18:00）",
        (
            f"概况：未恢复{len(open_incidents)}项，今日新增"
            f"{int(payload.get('opened_today') or 0)}项，已恢复"
            f"{int(payload.get('recovered_today') or 0)}项"
        ),
    ]
    run_errors = payload.get("run_errors") or {}
    lines.append(
        "监测失败：Worker {worker}，工作台 {workbench}，查单 {order}".format(
            worker=int(run_errors.get("worker") or 0),
            workbench=int(run_errors.get("workbench") or 0),
            order=int(run_errors.get("order") or 0),
        )
    )
    lines.append(
        f"通知失败或结果不确定：{int(payload.get('notification_failures') or 0)}次"
    )
    if not open_incidents:
        lines.append("当前：今日未发现仍未恢复的监测异常。")
    else:
        lines.append("当前问题：")
        for item in open_incidents[:8]:
            component = _COMPONENT_LABELS.get(item.get("component"), item.get("component"))
            reason = _RULE_LABELS.get(item.get("rule_code"), item.get("rule_code"))
            reference = item.get("subject_ref")
            details = item.get("details") or {}
            if item.get("component") == "order" and details.get("order_number"):
                reference = details["order_number"]
            suffix = f"（{reference}）" if reference else ""
            lines.append(f"- {component}：{reason}{suffix}")
        if len(open_incidents) > 8:
            lines.append(f"- 另有{len(open_incidents) - 8}项，已保留在监测日志中")
    lines.append("说明：日报只做汇总，不会自动修改客服、Worker或查单状态。")
    return "\n".join(lines)
