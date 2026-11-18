"""Create the least-privilege Callie monitor role and schema explicitly."""

from __future__ import annotations

import argparse
import json
import os

import psycopg
from psycopg import sql


ROLE = "callie_monitor_runtime"
READ_TABLES = (
    "dispatch_worker_health",
    "dispatch_compute_tasks",
    "dispatch_inbound_messages",
    "dispatch_dify_agent_order_requests_v2",
    "dispatch_dify_agent_runs_v1",
    "dispatch_outbound_plans",
    "dispatch_actions",
)


def bootstrap(owner_url: str, password: str) -> None:
    if not owner_url or not password or len(password) < 32:
        raise ValueError("BOOTSTRAP_CONFIGURATION_INVALID")
    with psycopg.connect(owner_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            database_name = cursor.fetchone()[0]
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (ROLE,))
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER "
                        "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                    ).format(sql.Identifier(ROLE), sql.Literal(password))
                )
            else:
                cursor.execute(
                    sql.SQL(
                        "ALTER ROLE {} WITH LOGIN PASSWORD {} NOSUPERUSER "
                        "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                    ).format(sql.Identifier(ROLE), sql.Literal(password))
                )
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database_name), sql.Identifier(ROLE)
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(ROLE)
                )
            )
            cursor.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA public FROM {}").format(
                    sql.Identifier(ROLE)
                )
            )
            for table in READ_TABLES:
                cursor.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                if cursor.fetchone()[0] is None:
                    raise RuntimeError("REQUIRED_BUSINESS_TABLE_MISSING")
                cursor.execute(
                    sql.SQL("REVOKE ALL PRIVILEGES ON TABLE public.{} FROM {}").format(
                        sql.Identifier(table), sql.Identifier(ROLE)
                    )
                )
                cursor.execute(
                    sql.SQL("GRANT SELECT ON TABLE public.{} TO {}").format(
                        sql.Identifier(table), sql.Identifier(ROLE)
                    )
                )
            cursor.execute("CREATE SCHEMA IF NOT EXISTS callie_monitor")
            cursor.execute(
                sql.SQL("GRANT USAGE, CREATE ON SCHEMA callie_monitor TO {}").format(
                    sql.Identifier(ROLE)
                )
            )
            cursor.execute(
                sql.SQL("ALTER ROLE {} SET statement_timeout='15s'").format(
                    sql.Identifier(ROLE)
                )
            )
            cursor.execute(
                sql.SQL("ALTER ROLE {} SET lock_timeout='3s'").format(
                    sql.Identifier(ROLE)
                )
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required")
    try:
        bootstrap(
            os.getenv("DATABASE_OWNER_URL", ""),
            os.getenv("MONITOR_ROLE_PASSWORD", ""),
        )
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "monitor_database_bootstrap_failed",
                    "error_type": type(exc).__name__,
                    "sqlstate": getattr(exc, "sqlstate", None),
                },
                sort_keys=True,
            )
        )
        return 1
    print("monitor_database_bootstrap_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
