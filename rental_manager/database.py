from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


def normalize_database_url(url: str) -> str:
    normalized = (url or "").strip()
    if normalized.startswith("postgres://"):
        normalized = "postgresql://" + normalized[len("postgres://") :]
    if normalized.startswith("postgresql://") and "+psycopg" not in normalized:
        normalized = "postgresql+psycopg://" + normalized[len("postgresql://") :]
    return normalized


DATABASE_URL = normalize_database_url(
    os.getenv(
        "RENTAL_MANAGER_DATABASE_URL",
        f"sqlite:///{(DATA_DIR / 'rental_manager.db').as_posix()}",
    )
)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from rental_manager import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
