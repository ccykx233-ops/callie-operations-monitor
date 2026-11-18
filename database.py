"""Safe PostgreSQL connection helpers for Sunshine webhook persistence."""

from __future__ import annotations

from functools import lru_cache
import os
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker


class DatabaseConfigurationError(RuntimeError):
    """Raised when DATABASE_URL is missing or cannot be used safely."""


class DatabaseUnavailableError(RuntimeError):
    """Raised when a database health check cannot complete."""


def normalize_database_url(raw_url: str) -> str:
    """Normalize supported Render PostgreSQL URLs for psycopg 3."""

    value = raw_url.strip()
    if not value:
        raise DatabaseConfigurationError("DATABASE_URL is required")
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value.removeprefix("postgres://")
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    if not value.startswith("postgresql+psycopg://"):
        raise DatabaseConfigurationError("DATABASE_URL is invalid")
    return value


def database_url_from_env() -> str:
    """Read DATABASE_URL without ever including its value in an error."""

    return normalize_database_url(os.getenv("DATABASE_URL", ""))


def create_engine_for_url(
    database_url: str, *, connect_args: dict[str, Any] | None = None
) -> Engine:
    """Create a lazy engine; this function does not open a connection."""

    try:
        return create_engine(
            normalize_database_url(database_url),
            pool_pre_ping=True,
            connect_args=connect_args or {},
        )
    except (SQLAlchemyError, ValueError):
        raise DatabaseConfigurationError("DATABASE_URL is invalid") from None


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build short-lived sessions with explicit transaction control."""

    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Return the process-local session factory configured from DATABASE_URL."""

    return create_session_factory(create_engine_for_url(database_url_from_env()))


def check_database_health(
    session_factory: sessionmaker[Session] | None = None,
) -> None:
    """Run a minimal query and expose only a safe availability error."""

    factory = session_factory or get_session_factory()
    try:
        with factory() as session:
            session.execute(text("SELECT 1"))
    except DatabaseConfigurationError:
        raise
    except SQLAlchemyError:
        raise DatabaseUnavailableError("Database is unavailable") from None
