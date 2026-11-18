from datetime import date, datetime, timezone

from callie_monitor_summary import build_daily_summary


def test_daily_summary_counts_runs_incidents_notifications_and_misses():
    day = date(2026, 11, 18)
    morning = datetime(2026, 11, 18, 1, tzinfo=timezone.utc)
    evening = datetime(2026, 11, 18, 9, tzinfo=timezone.utc)
    summary = build_daily_summary(
        summary_date=day,
        component="worker",
        expected_runs=144,
        runs=[
            {"status": "healthy", "finished_at": morning},
            {"status": "healthy", "finished_at": evening},
            {"status": "abnormal", "finished_at": evening},
            {"status": "error", "finished_at": evening},
            {"status": "running", "finished_at": None},
        ],
        incidents=[
            {"opened_on": day, "recovered_on": day},
            {"opened_on": date(2026, 11, 17), "recovered_on": None},
        ],
        notifications=[
            {"delivery_status": "sent"},
            {"delivery_status": "failed"},
            {"delivery_status": "uncertain"},
        ],
        generated_at=evening,
    )
    assert summary["completed_runs"] == 4
    assert summary["missed_runs"] == 140
    assert summary["healthy_runs"] == 2
    assert summary["abnormal_runs"] == 1
    assert summary["error_runs"] == 1
    assert summary["incidents_opened"] == 1
    assert summary["incidents_recovered"] == 1
    assert summary["notifications_sent"] == 1
    assert summary["notifications_failed_or_uncertain"] == 2
    assert summary["last_success_at"] == evening


def test_daily_summary_never_reports_negative_missed_runs():
    summary = build_daily_summary(
        summary_date=date(2026, 11, 18),
        component="workbench",
        expected_runs=1,
        runs=[{"status": "healthy", "finished_at": None}, {"status": "skipped"}],
        incidents=[],
        notifications=[],
        generated_at=datetime(2026, 11, 18, tzinfo=timezone.utc),
    )
    assert summary["missed_runs"] == 0
