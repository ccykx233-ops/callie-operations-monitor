from datetime import datetime
from zoneinfo import ZoneInfo

from callie_monitor_digest import render_daily_digest


SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_digest_is_concise_and_includes_open_safe_reference():
    text = render_daily_digest(
        {
            "open_incidents": [
                {
                    "component": "order",
                    "rule_code": "ORDER_REPLY_NOT_CONFIRMED",
                    "subject_ref": "request-safe-1",
                    "details": {"order_number": "ORDER-SAFE-1"},
                }
            ],
            "opened_today": 2,
            "recovered_today": 1,
            "run_errors": {"worker": 0, "workbench": 1, "order": 0},
            "notification_failures": 0,
        },
        generated_at=datetime(2026, 11, 18, 18, tzinfo=SHANGHAI),
    )
    assert "截至18:00" in text
    assert "未恢复1项" in text
    assert "ORDER-SAFE-1" in text
    assert "request-safe-1" not in text
    assert len(text.splitlines()) <= 8


def test_digest_still_reports_when_no_problem_exists():
    text = render_daily_digest(
        {
            "open_incidents": [],
            "opened_today": 0,
            "recovered_today": 0,
            "run_errors": {},
            "notification_failures": 0,
        },
        generated_at=datetime(2026, 11, 18, 18, tzinfo=SHANGHAI),
    )
    assert "今日未发现仍未恢复的监测异常" in text
