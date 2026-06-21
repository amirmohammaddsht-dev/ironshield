"""
Tests for IronShield database layer.
"""

import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from ironshield.db.database import Database
from ironshield.db.models import User, Tunnel, AuditLog, Setting


@pytest.fixture
def db(tmp_path):
    """Provide a temporary test database."""
    db_path = tmp_path / "test.db"
    database = Database(db_path)
    database.init()
    yield database
    database.close()


class TestDatabase:

    def test_init_creates_tables(self, db):
        assert db.health_check() is True

    def test_default_settings_seeded(self, db):
        mode = db.get_setting("routing.mode")
        assert mode == "auto"

    def test_set_and_get_setting(self, db):
        db.set_setting("routing.cooldown_minutes", 15)
        value = db.get_setting("routing.cooldown_minutes")
        assert value == 15

    def test_get_setting_type_cast(self, db):
        val = db.get_setting("alerts.cpu_warning")
        assert isinstance(val, float)
        assert val == 80.0


class TestUserModel:

    def test_create_user(self, db):
        with db.session() as s:
            user = User(
                username="testuser",
                traffic_limit_bytes=50 * (1024 ** 3),
                expire_at=datetime.now(timezone.utc) + timedelta(days=30),
            )
            s.add(user)
            s.flush()
            assert user.id is not None

    def test_traffic_properties(self, db):
        with db.session() as s:
            user = User(
                username="trafficuser",
                traffic_limit_bytes=50 * (1024 ** 3),
                traffic_used_bytes=10 * (1024 ** 3),
            )
            s.add(user)
            s.flush()
            assert user.traffic_limit_gb == pytest.approx(50.0, rel=1e-3)
            assert user.traffic_used_gb == pytest.approx(10.0, rel=1e-3)
            assert user.traffic_remaining_gb == pytest.approx(40.0, rel=1e-3)
            assert user.traffic_percent_used == pytest.approx(20.0, rel=1e-3)

    def test_user_expiry(self, db):
        with db.session() as s:
            expired_user = User(
                username="expireduser",
                expire_at=datetime.now(timezone.utc) - timedelta(days=1),
            )
            s.add(expired_user)
            s.flush()
            assert expired_user.is_expired is True

    def test_user_over_quota(self, db):
        with db.session() as s:
            user = User(
                username="quotauser",
                traffic_limit_bytes=1024,
                traffic_used_bytes=2048,
            )
            s.add(user)
            s.flush()
            assert user.is_over_quota is True
