"""Verify monitor privileges without changing business or monitor data."""

from __future__ import annotations

import json
import os

import psycopg

from bootstrap_monitor_database import READ_TABLES, ROLE


MONITOR_TABLES = (
    "monitor_runs",
    "component_reports",
    "monitor_incidents",
    "monitor_notifications",
    "daily_summaries",
)


def main() -> int:
    url = os.getenv("MONITOR_DATABASE_URL", "")
    if not url:
        print(json.dumps({"status": "configuration_error"}))
        return 2
    try:
        with psycopg.connect(url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_user")
                if cursor.fetchone()[0] != ROLE:
                    raise RuntimeError("MONITOR_ROLE_IDENTITY_MISMATCH")
                for table in READ_TABLES:
                    cursor.execute(
                        "SELECT has_table_privilege(current_user,%s,'SELECT'),"
                        "has_table_privilege(current_user,%s,'INSERT,UPDATE,DELETE')",
                        (f"public.{table}", f"public.{table}"),
                    )
                    can_read, can_write = cursor.fetchone()
                    if not can_read or can_write:
                        raise RuntimeError("BUSINESS_TABLE_PRIVILEGE_INVALID")
                for table in MONITOR_TABLES:
                    cursor.execute("SELECT to_regclass(%s)", (f"callie_monitor.{table}",))
                    if cursor.fetchone()[0] is None:
                        raise RuntimeError("MONITOR_TABLE_MISSING")
                    cursor.execute(
                        "SELECT has_table_privilege(current_user,%s,'SELECT,INSERT,UPDATE,DELETE')",
                        (f"callie_monitor.{table}",),
                    )
                    if not cursor.fetchone()[0]:
                        raise RuntimeError("MONITOR_TABLE_PRIVILEGE_INVALID")
        print(json.dumps({"status": "ok", "business_tables": len(READ_TABLES), "monitor_tables": len(MONITOR_TABLES)}))
        return 0
    except Exception:
        print(json.dumps({"status": "failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
