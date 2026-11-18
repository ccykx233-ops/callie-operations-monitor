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

EXPECTED_PUBLIC_SYSTEM_VIEWS = (
    "pg_stat_statements",
    "pg_stat_statements_info",
)


def main() -> int:
    url = os.getenv("MONITOR_DATABASE_URL", "")
    if not url:
        print(json.dumps({"status": "configuration_error"}))
        return 2
    stage = "connect"
    try:
        with psycopg.connect(url) as connection:
            with connection.cursor() as cursor:
                stage = "role_identity"
                cursor.execute("SELECT current_user")
                if cursor.fetchone()[0] != ROLE:
                    raise RuntimeError("MONITOR_ROLE_IDENTITY_MISMATCH")
                for table in READ_TABLES:
                    stage = "required_table_privileges"
                    cursor.execute(
                        "SELECT has_table_privilege(current_user,%s,'SELECT'),"
                        "has_table_privilege(current_user,%s,'INSERT,UPDATE,DELETE')",
                        (f"public.{table}", f"public.{table}"),
                    )
                    can_read, can_write = cursor.fetchone()
                    if not can_read or can_write:
                        raise RuntimeError("BUSINESS_TABLE_PRIVILEGE_INVALID")
                stage = "unexpected_public_table_privileges"
                cursor.execute(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') "
                    "AND NOT (c.relname = ANY(%s)) "
                    "AND (has_table_privilege(current_user,c.oid,'SELECT') "
                    "OR has_table_privilege(current_user,c.oid,'INSERT') "
                    "OR has_table_privilege(current_user,c.oid,'UPDATE') "
                    "OR has_table_privilege(current_user,c.oid,'DELETE'))",
                    (list(READ_TABLES + EXPECTED_PUBLIC_SYSTEM_VIEWS),),
                )
                if cursor.fetchone() is not None:
                    raise RuntimeError("UNEXPECTED_PUBLIC_TABLE_PRIVILEGE")
                stage = "system_view_read_only_privileges"
                for view in EXPECTED_PUBLIC_SYSTEM_VIEWS:
                    cursor.execute(
                        "SELECT has_table_privilege(current_user,%s,'INSERT') "
                        "OR has_table_privilege(current_user,%s,'UPDATE') "
                        "OR has_table_privilege(current_user,%s,'DELETE')",
                        (f"public.{view}", f"public.{view}", f"public.{view}"),
                    )
                    if cursor.fetchone()[0]:
                        raise RuntimeError("SYSTEM_VIEW_WRITE_PRIVILEGE_INVALID")
                for table in MONITOR_TABLES:
                    stage = "monitor_table_privileges"
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
    except Exception as exc:
        original = getattr(exc, "orig", exc)
        print(
            json.dumps(
                {
                    "status": "failed",
                    "stage": stage,
                    "error_type": type(exc).__name__,
                    "sqlstate": getattr(original, "sqlstate", None),
                },
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
