"""Safe DingTalk presentation and delivery for operational incidents."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

from callie_monitor_incidents_store import NotificationDecision
from dingtalk_robot import DingTalkRobot, DingTalkRobotError


_COMPONENT_LABELS = {
    "worker": "Worker",
    "workbench": "客服工作台同步",
    "order": "查单",
}

_SHANGHAI = ZoneInfo("Asia/Shanghai")

_RULE_LABELS = {
    "WORKER_HEARTBEAT_STALE": "Worker心跳已过期",
    "WORKER_STATUS_INVALID": "Worker状态异常",
    "WORKER_BUSY_WITHOUT_TASK": "Worker显示忙碌但没有任务",
    "WORKER_TASK_HELD_TOO_LONG": "Worker持有同一任务超过10分钟",
    "WORKER_HOLDS_TERMINAL_TASK": "Worker仍占用已结束任务",
    "WORKER_IDENTITY_MISMATCH": "Worker身份与部署配置不一致",
    "WORKBENCH_REPORT_STALE": "工作台小时报告缺失或过期",
    "WORKBENCH_COUNT_MISSING": "工作台小时同步数量缺失",
    "WORKBENCH_COUNT_MISMATCH": "云端进线数与工作台同步数不一致",
    "ORDER_CREATED_AT_MISSING": "查单请求缺少创建时间",
    "ORDER_UNCLAIMED_TOO_LONG": "查单请求超过5分钟未领取",
    "ORDER_LEASE_EXPIRED": "查单租约已过期",
    "ORDER_TERMINAL_FAILURE": "查单已超时或失败关闭",
    "ORDER_TARGET_NOT_READY": "查单目标页面未就绪",
    "ORDER_COMPLETED_WITHOUT_TRUSTED_RECEIPT": "查单完成但没有可信回执",
    "ORDER_REPLY_NOT_CONFIRMED": "已有查单回执但客户发送未确认",
    "ORDER_UNTRUSTED_SUCCESS_REPLY": "没有可信回执却宣称查单成功",
    "ORDER_EXECUTOR_HEARTBEAT_STALE": "查单执行器小时报告过期",
    "ORDER_CHROME_POOL_NOT_READY": "查单Chrome页面池不是5/5",
    "ORDER_EXECUTOR_FAILED_CLOSED": "查单执行器处于失败关闭状态",
    "ORDER_EXECUTOR_STATE_INVALID": "查单执行器未处于可服务状态",
}


def render_notification(decision: NotificationDecision, *, observed_at: datetime) -> str:
    component = _COMPONENT_LABELS.get(decision.component, decision.component)
    reason = _RULE_LABELS.get(decision.rule_code, decision.rule_code)
    if decision.kind == "recovered":
        title = f"🟢 {component}监测恢复"
    elif decision.kind == "repeat":
        title = f"🔴 {component}异常仍在持续"
    else:
        title = f"🔴 {component}异常"
    lines = [
        "Callie 客服运行提醒",
        title,
        f"时间：{observed_at.astimezone(_SHANGHAI).strftime('%m-%d %H:%M')}",
        f"监测结果：{reason}",
    ]
    if decision.subject_ref:
        label = "请求/任务编号"
        if decision.component == "order" and decision.details.get("order_number"):
            lines.append(f"可信订单号：{decision.details['order_number']}")
        lines.append(f"{label}：{decision.subject_ref}")
    if decision.rule_code == "WORKBENCH_COUNT_MISMATCH":
        lines.append(
            "数量：云端{cloud}，工作台{workbench}，差异{difference}".format(
                cloud=decision.details.get("cloud_count"),
                workbench=decision.details.get("workbench_count"),
                difference=decision.details.get("difference"),
            )
        )
    lines.append("说明：监测只做核对和通知，没有自动修改业务数据。")
    return "\n".join(lines)


def deliver_notification(
    robot: DingTalkRobot | None,
    decision: NotificationDecision,
    *,
    observed_at: datetime,
) -> tuple[str, str, str | None]:
    """Return delivery status, payload hash and safe error code."""

    content = render_notification(decision, observed_at=observed_at)
    return deliver_text(robot, content)


def deliver_text(
    robot: DingTalkRobot | None,
    content: str,
) -> tuple[str, str, str | None]:
    """Deliver already-sanitized monitor text and return an auditable result."""

    payload_hash = sha256(content.encode("utf-8")).hexdigest()
    if robot is None:
        return "skipped", payload_hash, None
    try:
        robot.send_text(content)
        return "sent", payload_hash, None
    except DingTalkRobotError as exc:
        return (
            "uncertain" if exc.delivery_uncertain else "failed",
            payload_hash,
            exc.error_code,
        )
