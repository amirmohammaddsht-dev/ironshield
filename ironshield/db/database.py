"""
IronShield - Database Engine
Path: ironshield/db/database.py
Purpose: SQLAlchemy engine setup, session management, and database initialization
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional, Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from ironshield.db.models import Base, Setting
from ironshield.utils.logger import get_logger

logger = get_logger("database")

DEFAULT_DB_PATH = Path("/opt/ironshield/db/ironshield.db")


class Database:
    """
    Manages the SQLite database connection and sessions.

    Usage:
        db = Database()
        db.init()

        with db.session() as session:
            users = session.query(User).all()
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._engine = None
        self._session_factory = None

    def init(self) -> None:
        """
        Initialize the database engine and create all tables.
        Must be called once at application startup.
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{self._db_path}"

        self._engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=False,
        )

        # Enable WAL mode for better concurrent read performance
        @event.listens_for(self._engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
            cursor.close()

        # Create all tables
        Base.metadata.create_all(self._engine)

        self._session_factory = sessionmaker(
            bind=self._engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )

        logger.info(f"Database initialized: {self._db_path}")
        self._seed_default_settings()

    def _seed_default_settings(self) -> None:
        """Insert default settings if they don't exist."""
        defaults = [
            ("routing.mode", "auto", "str", "Smart routing mode: auto/manual/emergency"),
            ("routing.cooldown_minutes", "10", "int", "Minimum minutes between tunnel switches"),
            ("routing.min_score_diff", "10", "float", "Minimum score improvement to trigger switch"),
            ("routing.consecutive_failures", "3", "int", "Failures before marking tunnel as failed"),
            ("benchmark.quick_interval_minutes", "5", "int", "Quick benchmark interval"),
            ("benchmark.standard_interval_minutes", "30", "int", "Standard benchmark interval"),
            ("benchmark.full_interval_hours", "6", "int", "Full benchmark interval"),
            ("alerts.cpu_warning", "80", "float", "CPU warning threshold %"),
            ("alerts.cpu_critical", "95", "float", "CPU critical threshold %"),
            ("alerts.ram_warning", "85", "float", "RAM warning threshold %"),
            ("alerts.ram_critical", "95", "float", "RAM critical threshold %"),
            ("alerts.disk_warning", "85", "float", "Disk warning threshold %"),
            ("alerts.disk_critical", "95", "float", "Disk critical threshold %"),
            ("alerts.user_expiry_warning_days", "3", "int", "Days before expiry to warn user"),
            ("alerts.user_quota_warning_percent", "80", "float", "Traffic % before quota warning"),
        ]

        with self.session() as s:
            for key, value, vtype, desc in defaults:
                existing = s.get(Setting, key)
                if existing is None:
                    s.add(Setting(key=key, value=value, value_type=vtype, description=desc))
            s.commit()

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """
        Context manager that provides a database session.
        Automatically commits on success and rolls back on error.

        Usage:
            with db.session() as s:
                s.add(some_object)
                # commit happens automatically
        """
        if self._session_factory is None:
            raise RuntimeError("Database not initialized. Call db.init() first.")

        session: Session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_setting(self, key: str, default: Any = None) -> Any:
        """
        Get a runtime setting value with automatic type casting.

        Args:
            key: Setting key
            default: Default value if key not found

        Returns:
            Typed value
        """
        with self.session() as s:
            setting = s.get(Setting, key)
            if setting is None:
                return default
            return self._cast_value(setting.value, setting.value_type)

    def set_setting(self, key: str, value: Any) -> None:
        """
        Update a runtime setting value.

        Args:
            key: Setting key
            value: New value
        """
        with self.session() as s:
            setting = s.get(Setting, key)
            if setting is None:
                logger.warning(f"Setting key not found: {key}")
                return
            setting.value = str(value)
            s.commit()

    @staticmethod
    def _cast_value(value: str, value_type: str) -> Any:
        """Cast a string value to its correct type."""
        try:
            if value_type == "int":
                return int(value)
            elif value_type == "float":
                return float(value)
            elif value_type == "bool":
                return value.lower() in ("true", "1", "yes")
            elif value_type == "json":
                return json.loads(value)
            else:
                return value
        except (ValueError, json.JSONDecodeError):
            return value

    def health_check(self) -> bool:
        """Verify database is accessible and responsive."""
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False

    def get_db_size_mb(self) -> float:
        """Get current database file size in MB."""
        if self._db_path.exists():
            return self._db_path.stat().st_size / (1024 * 1024)
        return 0.0

    def close(self) -> None:
        """Close the database engine and all connections."""
        if self._engine:
            self._engine.dispose()
            logger.info("Database connection closed")


# ─── Singleton Instance ───────────────────────

_db_instance: Optional[Database] = None


def get_db() -> Database:
    """
    Get the global Database singleton.
    Must call init() before first use.
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance


def init_db(db_path: Optional[Path] = None) -> Database:
    """
    Initialize the global database instance.

    Args:
        db_path: Optional custom path for the SQLite file

    Returns:
        Database: Initialized database instance
    """
    global _db_instance
    _db_instance = Database(db_path)
    _db_instance.init()
    return _db_instance
