"""
Database session management.

Provides SQLAlchemy engine and session handling.
"""

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from polykalsh.config import get_settings
from polykalsh.database.models import Base

# Module-level engine and session factory
_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Get or create the SQLAlchemy engine."""
    global _engine
    if _engine is None:
        settings = get_settings()

        # Ensure data directory exists
        db_path = Path(settings.database_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        _engine = create_engine(
            settings.database_url,
            echo=settings.log_level == "DEBUG",
            pool_pre_ping=True,
        )

        # Enable SQLite foreign keys
        @event.listens_for(_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Get or create the session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
    return _session_factory


def init_db() -> None:
    """Initialize the database (create all tables)."""
    engine = get_engine()
    Base.metadata.create_all(engine)


def drop_db() -> None:
    """Drop all tables (use with caution!)."""
    engine = get_engine()
    Base.metadata.drop_all(engine)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context manager for database sessions.

    Usage:
        with get_session() as session:
            leaders = session.query(Leader).all()
            session.add(new_leader)
            session.commit()
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_scoped_session() -> Session:
    """
    Get a new session (caller is responsible for closing).

    Prefer using get_session() context manager when possible.
    """
    factory = get_session_factory()
    return factory()
