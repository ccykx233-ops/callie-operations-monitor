"""Report safe, read-only prerequisites for monitor database bootstrap."""

from __future__ import annotations

import json
import os

import psycopg

from bootstrap_monitor_database import READ_TABLES, ROLE


def main() -> int:
    owner_url = os.getenv("DATABASE_OWNER_URL", "")
    if not owner_url:
        print(json.dumps({"status": "configuration_error"}))
        return 2
    try:
        with psycopg.connect(owner_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT rolsuper, rolcreaterole FROM pg_roles WHERE rolname=current_user"
                )
                is_superuser, can_create_role = cursor.fetchone()
                cursor.execute(
                    "SELECT pg_has_role(current_user, datdba, 'MEMBER') "
                    "FROM pg_database WHERE datname=current_database()"
                )
                can_manage_database = bool(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT pg_has_role(current_user, nspowner, 'MEMBER') "
                    "FROM pg_namespace WHERE nspname='public'"
                )
                can_manage_public_schema = bool(cursor.fetchone()[0])
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (ROLE,))
                monitor_role_exists = cursor.fetchone() is not None
                cursor.execute(
                    "SELECT pg_has_role(current_user, %s, 'MEMBER')",
                    (ROLE,),
                )
                can_set_monitor_role = bool(cursor.fetchone()[0])
                missing_tables: list[str] = []
                tables_without_grant_authority: list[str] = []
                for table in READ_TABLES:
                    cursor.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                    if cursor.fetchone()[0] is None:
                        missing_tables.append(table)
                        continue
                    cursor.execute(
                        "SELECT pg_has_role(current_user, c.relowner, 'MEMBER') "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname='public' AND c.relname=%s",
                        (table,),
                    )
                    if not cursor.fetchone()[0]:
                        tables_without_grant_authority.append(table)
                cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname='callie_monitor'")
                monitor_schema_exists = cursor.fetchone() is not None
        print(
            json.dumps(
                {
                    "status": "ok",
                    "can_manage_roles": bool(is_superuser or can_create_role),
                    "can_manage_database": can_manage_database,
                    "can_manage_public_schema": can_manage_public_schema,
                    "can_set_monitor_role": can_set_monitor_role,
                    "monitor_role_exists": monitor_role_exists,
                    "monitor_schema_exists": monitor_schema_exists,
                    "required_table_count": len(READ_TABLES),
                    "missing_tables": missing_tables,
                    "tables_without_grant_authority": tables_without_grant_authority,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception:
        print(json.dumps({"status": "connection_or_query_failed"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
