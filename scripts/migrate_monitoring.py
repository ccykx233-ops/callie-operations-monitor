"""Explicit additive migration for the isolated Callie monitoring schema."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import inspect, text

from database import create_engine_for_url
from monitoring_models import MONITOR_SCHEMA, MonitorBase


_LOCK_ID = 75071918
_EXPECTED_TABLES = {
    "monitor_runs",
    "component_reports",
    "monitor_incidents",
    "monitor_notifications",
    "daily_summaries",
}


def migrate(engine) -> None:
    if engine.dialect.name != "postgresql":
        raise ValueError("POSTGRESQL_REQUIRED")
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": _LOCK_ID})
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{MONITOR_SCHEMA}"'))
        MonitorBase.metadata.create_all(connection, checkfirst=True)
        present = set(inspect(connection).get_table_names(schema=MONITOR_SCHEMA))
        missing = _EXPECTED_TABLES - present
        if missing:
            raise RuntimeError("MONITOR_SCHEMA_INCOMPLETE")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="create the additive monitoring schema; without this flag nothing is read or changed",
    )
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required")
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        parser.error("DATABASE_URL is required")

    engine = None
    try:
        engine = create_engine_for_url(database_url)
        migrate(engine)
    except Exception:
        print("monitoring_migration_failed", file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()
    print("monitoring_migration_complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
