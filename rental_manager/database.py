from __future__ import annotations

import os
import time
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from rental_manager.observability.logging import get_logger


LOGGER = get_logger("rental_manager.database")


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DEFAULT_DATABASE_URL = f"sqlite:///{(DATA_DIR / 'rental_manager.db').as_posix()}"
DEFAULT_POSTGRES_MAINTENANCE_DATABASE = "postgres"
DEFAULT_POSTGRES_POOL_RECYCLE_SECONDS = 1800
DEFAULT_DB_SLOW_QUERY_MS = 500


def normalize_database_url(url: str) -> str:
    normalized = (url or "").strip()
    if normalized.startswith("postgres://"):
        normalized = "postgresql://" + normalized[len("postgres://") :]
    if normalized.startswith("postgresql://") and "+psycopg" not in normalized:
        normalized = "postgresql+psycopg://" + normalized[len("postgresql://") :]
    return normalized


def configured_database_url(environ: Mapping[str, str] | None = None) -> str:
    source = environ if environ is not None else os.environ
    return source.get("RENTAL_MANAGER_DATABASE_URL") or source.get("DATABASE_URL") or DEFAULT_DATABASE_URL


def quote_postgres_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def postgres_database_bootstrap_target(
    database_url: str,
    maintenance_database: str = DEFAULT_POSTGRES_MAINTENANCE_DATABASE,
) -> tuple[URL, str] | None:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "postgresql":
        return None
    target_database = parsed.database
    if not target_database or target_database == maintenance_database:
        return None
    return parsed.set(database=maintenance_database), target_database


def ensure_postgres_database_exists(database_url: str) -> None:
    maintenance_database = os.environ.get(
        "POSTGRES_MAINTENANCE_DATABASE",
        DEFAULT_POSTGRES_MAINTENANCE_DATABASE,
    )
    target = postgres_database_bootstrap_target(database_url, maintenance_database)
    if target is None:
        return

    maintenance_url, target_database = target
    maintenance_engine = create_engine(
        maintenance_url,
        isolation_level="AUTOCOMMIT",
        future=True,
    )
    try:
        with maintenance_engine.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
                {"database_name": target_database},
            )
            if exists:
                return
            connection.execute(text(f"CREATE DATABASE {quote_postgres_identifier(target_database)}"))
            LOGGER.info("postgres database created", extra={"event": "database_created"})
    except SQLAlchemyError as exc:
        LOGGER.warning("postgres database bootstrap skipped: %s", exc, extra={"event": "database_bootstrap_skipped"})
    finally:
        maintenance_engine.dispose()


def database_engine_options(database_url: str) -> dict[str, object]:
    options: dict[str, object] = {"future": True}
    if database_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
    elif make_url(database_url).get_backend_name() == "postgresql":
        options["pool_pre_ping"] = True
        options["pool_recycle"] = DEFAULT_POSTGRES_POOL_RECYCLE_SECONDS
    return options


DATABASE_URL = normalize_database_url(configured_database_url())

engine = create_engine(DATABASE_URL, **database_engine_options(DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def compact_sql_statement(statement: str) -> str:
    return " ".join(str(statement or "").split())[:280]


def install_query_timing(target_engine) -> None:
    slow_ms = int(os.environ.get("RENTAL_MANAGER_DB_SLOW_MS", str(DEFAULT_DB_SLOW_QUERY_MS)) or DEFAULT_DB_SLOW_QUERY_MS)

    @event.listens_for(target_engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        conn.info.setdefault("query_start_time", []).append(time.perf_counter())

    @event.listens_for(target_engine, "after_cursor_execute")
    def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        started_stack = conn.info.get("query_start_time") or []
        started = started_stack.pop() if started_stack else None
        if started is None:
            return
        duration_ms = int((time.perf_counter() - started) * 1000)
        if duration_ms >= slow_ms:
            LOGGER.warning(
                "slow query sql=%s",
                compact_sql_statement(statement),
                extra={"event": "slow_query", "duration_ms": duration_ms},
            )


install_query_timing(engine)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from rental_manager import models  # noqa: F401

    parsed = make_url(DATABASE_URL)
    bootstrap_requested = os.environ.get("RENTAL_MANAGER_BOOTSTRAP_SCHEMA", "").strip().lower() in {"1", "true", "yes"}
    sqlite_missing = False
    if parsed.get_backend_name() == "sqlite":
        database_name = parsed.database or ""
        sqlite_missing = database_name == ":memory:" or not Path(database_name).exists()
    if bootstrap_requested or sqlite_missing:
        Base.metadata.create_all(bind=engine)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
