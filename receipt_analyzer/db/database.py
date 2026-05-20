"""
Database engine and session factory.

Default: SQLite file at ./receipts.db
Override: set DATABASE_URL environment variable to any SQLAlchemy URL,
e.g. postgresql+psycopg2://user:pass@localhost/receipts
"""

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session

from receipt_analyzer.db.orm import Base

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "receipts.db"
_DEFAULT_URL = f"sqlite:///{_DEFAULT_DB_PATH}"


def _get_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_URL)


def make_engine(url: str | None = None, echo: bool = False) -> Engine:
    resolved = url or _get_url()
    engine = create_engine(resolved, echo=echo)

    # Enable WAL mode and foreign keys for SQLite
    if resolved.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(conn, _):
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create all tables if they don't exist yet."""
    Base.metadata.create_all(engine)
