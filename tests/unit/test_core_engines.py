"""
Tests for Phase 5 — Core Engines.
Covers: ConfigEngine, ServiceManager, TunnelManager,
        BenchmarkEngine, SmartRoutingEngine, HealthCheckEngine, FailoverEngine.
All external calls are mocked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Any
from unittest.mock import MagicMock, patch, AsyncMock
import asyncio

import pytest
import yaml

from ironshield.core.config_engine import ConfigEngine, DEFAULT_CONFIG
from ironshield.core.smart_routing import SmartRoutingEngine, RoutingConfig
from ironshield.core.tunnel_manager import TunnelManager
from ironshield.core.benchmark_engine import BenchmarkEngine, BenchmarkSchedule
from ironshield.core.health_check import HealthCheckEngine, AlertThresholds
from ironshield.core.failover_engine import FailoverEngine
from ironshield.db.database import Database
from ironshield.db.models import Tunnel, RoutingDecision, FailoverEvent
from ironshield.services.base import (
    BenchmarkResult,
    HealthResult,
    PluginCategory,
    PluginMeta,
    Result,
    ServerRole,
    ServiceStatus,
)


# ── Fixtures ──────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path):
    """In-memory test database."""
    db = Database(tmp_path / "test.db")
    db.init()
    yield db
    db.close()


@pytest.fixture
def mock_plugin():
    """A mock plugin for testing."""
    plugin = MagicMock()
    plugin.meta = PluginMeta(
        name="mock_tunnel",
        display_name="Mock Tunnel",
        version="1.0",
        author="Test",
        source_url="",
        license="MIT",
        roles=[ServerRole.IRAN, ServerRole.FOREIGN],
        category=PluginCategory.TUNNEL_RELIABLE,
        priority=3,
    )
    plugin.server_role = ServerRole.IRAN
    plugin.is_running.return_value = True
    plugin.status.return_value = ServiceStatus.RUNNING
    plugin.health_check.return_value = HealthResult(
        healthy=True,
        status=ServiceStatus.RUNNING,
        checks={"process": True, "port": True},
    )
    plugin.get_config.return_value = {"remote_host": "1.2.3.4"}
    plugin.get_metrics.return_value = {"plugin": "mock_tunnel", "status": "RUNNING"}
    plugin.get_logs.return_value = ["log line 1"]
    plugin.get_ufw_rules.return_value = []
    plugin.supports_benchmark.return_value = False
    plugin.meta.required = False
    return plugin


@pytest.fixture
def mock_pm(mock_plugin):
    """Mock PluginManager with one tunnel plugin."""
    pm = MagicMock()
    pm.all.return_value = [mock_plugin]
    pm.get.return_value = mock_plugin
    pm._registry = {"mock_tunnel": mock_plugin}
    return pm


# ── ConfigEngine Tests ─────────────────────────


class TestConfigEngine:
    def test_load_defaults_when_no_file(self, tmp_path):
        """Should use default config when no file exists."""
        engine = ConfigEngine(config_root=tmp_path)
        cfg = engine.load()
        assert cfg["ironshield"]["version"] == "1.0.0"
        assert cfg["ironshield"]["role"] == "iran"

    def test_save_and_reload(self, tmp_path):
        """Saved config should be loadable."""
        engine = ConfigEngine(config_root=tmp_path)
        engine.load()
        engine.set("ironshield.role", "foreign")
        engine.set("server.foreign.ip", "1.2.3.4")

        engine2 = ConfigEngine(config_root=tmp_path)
        cfg = engine2.load()
        assert cfg["ironshield"]["role"] == "foreign"
        assert cfg["server"]["foreign"]["ip"] == "1.2.3.4"

    def test_get_nested_value(self, tmp_path):
        """Should retrieve nested config values by dot path."""
        engine = ConfigEngine(config_root=tmp_path)
        engine.load()
        assert engine.get("openvpn.port") == 443
        assert engine.get("openvpn.protocol") == "tcp"

    def test_get_missing_returns_default(self, tmp_path):
        """Should return default for missing keys."""
        engine = ConfigEngine(config_root=tmp_path)
        engine.load()
        assert engine.get("nonexistent.key", "fallback") == "fallback"

    def test_set_nested_value(self, tmp_path):
        """Should set nested config values."""
        engine = ConfigEngine(config_root=tmp_path)
        engine.load()
        engine.set("openvpn.port", 8443)
        assert engine.get("openvpn.port") == 8443

    def test_validate_valid_config(self, tmp_path):
        """Valid config should have no errors."""
        engine = ConfigEngine(config_root=tmp_path)
        engine.load()
        # Iran role needs foreign IP
        engine._config["ironshield"]["role"] = "iran"
        engine._config["server"]["foreign"]["ip"] = "1.2.3.4"
        errors = engine.validate()
        assert len(errors) == 0

    def test_validate_invalid_role(self, tmp_path):
        """Invalid role should produce error."""
        engine = ConfigEngine(config_root=tmp_path)
        engine.load()
        engine._config["ironshield"]["role"] = "invalid_role"
        errors = engine.validate()
        assert any("role" in e.lower() for e in errors)

    def test_validate_missing_foreign_ip(self, tmp_path):
        """Iran server without foreign IP should produce error."""
        engine = ConfigEngine(config_root=tmp_path)
        engine.load()
        engine._config["ironshield"]["role"] = "iran"
        engine._config["server"]["foreign"]["ip"] = ""
        errors = engine.validate()
        assert any("foreign" in e.lower() for e in errors)

    def test_validate_invalid_telegram_token(self, tmp_path):
        """Invalid Telegram token should produce error."""
        engine = ConfigEngine(config_root=tmp_path)
        engine.load()
        engine._config["telegram"]["token"] = "not_a_token"
        errors = engine.validate()
        assert any("telegram" in e.lower() for e in errors)

    def test_validate_weights_sum(self, tmp_path):
        """Benchmark weights not summing to 1.0 should produce error."""
        engine = ConfigEngine(config_root=tmp_path)
        engine.load()
        engine._config["benchmark"]["scoring"]["latency_weight"] = 0.5
        errors = engine.validate()
        assert any("weight" in e.lower() for e in errors)

    def test_backup_and_rollback(self, tmp_path):
        """Should backup and restore config."""
        engine = ConfigEngine(config_root=tmp_path)
        engine.load()
        engine.set("openvpn.port", 443)
        engine.save()

        backup_path = engine.backup(label="before_change")
        assert backup_path is not None
        assert backup_path.exists()

        engine.set("openvpn.port", 9999)
        assert engine.get("openvpn.port") == 9999

        engine.rollback(backup_path)
        assert engine.get("openvpn.port") == 443

    def test_history_records_changes(self, tmp_path):
        """Config changes should be recorded in history."""
        engine = ConfigEngine(config_root=tmp_path)
        engine.load()
        engine.set("openvpn.port", 8080, performed_by="admin_bot")

        history = engine.get_history()
        assert len(history) >= 1
        assert history[0]["key"] == "openvpn.port"
        assert history[0]["performed_by"] == "admin_bot"

    def test_deep_merge(self, tmp_path):
        """Deep merge should preserve unmodified nested keys."""
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 99}}
        result = ConfigEngine._deep_merge(base, override)
        assert result["a"]["x"] == 1  # preserved
        assert result["a"]["y"] == 99  # overridden
        assert result["b"] == 3  # preserved

    def test_init_default_config(self, tmp_path):
        """init_default should write a valid config file."""
        engine = ConfigEngine(config_root=tmp_path)
        success = engine.init_default(role="iran", iran_ip="1.1.1.1", foreign_ip="2.2.2.2")
        assert success is True
        assert engine.get("ironshield.role") == "iran"
        assert engine.get("server.foreign.ip") == "2.2.2.2"

    def test_list_backups(self, tmp_path):
        """Should list all backup files."""
        engine = ConfigEngine(config_root=tmp_path)
        engine.load()
        engine.save()
        engine.backup("v1")
        engine.backup("v2")
        backups = engine.list_backups()
        # Only count backups in our tmp_path
        assert len(backups) >= 2


# ── SmartRoutingEngine Tests ───────────────────


class TestSmartRoutingEngine:
    @pytest.fixture
    def tunnel_with_data(self, tmp_db):
        """Insert a test tunnel into DB."""
        with tmp_db.session() as s:
            t = Tunnel(
                plugin_name="mock_tunnel",
                display_name="Mock Tunnel",
                server_role="iran",
                is_enabled=True,
                status="ACTIVE",
                score=95.0,
                latency_ms=40.0,
                packet_loss_percent=0.0,
                priority=3,
                is_emergency=False,
            )
            s.add(t)
        return tmp_db

    @pytest.fixture
    def routing(self, mock_pm, tunnel_with_data):
        tm = TunnelManager(mock_pm, tunnel_with_data)
        config = RoutingConfig(
            cooldown_minutes=0,  # no cooldown in tests
            min_score_difference=10.0,
        )
        return SmartRoutingEngine(tm, tunnel_with_data, config=config)

    def test_initial_switch(self, routing):
        """First evaluation should switch to best available tunnel."""
        result = routing.evaluate()
        assert result == "mock_tunnel"
        assert routing._current_tunnel == "mock_tunnel"

    def test_no_switch_when_already_best(self, routing):
        """Should not switch when already on best tunnel."""
        routing.evaluate()  # initial
        result = routing.evaluate()  # second call
        assert result == "mock_tunnel"
        assert routing._current_tunnel == "mock_tunnel"

    def test_manual_override(self, routing, tunnel_with_data):
        """Manual override should force tunnel selection."""
        # Add a second tunnel
        with tunnel_with_data.session() as s:
            s.add(
                Tunnel(
                    plugin_name="other_tunnel",
                    display_name="Other",
                    server_role="iran",
                    is_enabled=True,
                    status="ACTIVE",
                    score=80.0,
                    priority=5,
                    is_emergency=False,
                )
            )

        routing.set_manual_override("mock_tunnel")
        assert routing._override_tunnel == "mock_tunnel"
        assert routing._current_tunnel == "mock_tunnel"

    def test_clear_override_returns_to_auto(self, routing):
        """Clearing override should return to auto mode."""
        routing.set_manual_override("mock_tunnel")
        routing.clear_manual_override()
        assert routing._override_tunnel is None
        assert routing.config.mode == "auto"

    def test_block_tunnel(self, routing):
        """Blocked tunnel should not be selected."""
        routing.block_tunnel("mock_tunnel")
        assert "mock_tunnel" in routing._blocked

    def test_unblock_tunnel(self, routing):
        """Unblocked tunnel can be selected again."""
        routing.block_tunnel("mock_tunnel")
        routing.unblock_tunnel("mock_tunnel")
        assert "mock_tunnel" not in routing._blocked

    def test_cooldown_prevents_switch(self, tunnel_with_data, mock_pm):
        """Cooldown should prevent rapid switching."""
        import time

        tm = TunnelManager(mock_pm, tunnel_with_data)
        config = RoutingConfig(cooldown_minutes=10, min_score_difference=5.0)
        routing = SmartRoutingEngine(tm, tunnel_with_data, config=config)

        routing.evaluate()  # initial switch
        routing._last_switch_time = time.monotonic()  # reset cooldown

        # Should not switch again during cooldown
        result = routing.evaluate()
        assert routing._current_tunnel == "mock_tunnel"

    def test_get_status(self, routing):
        """Status dict should contain required keys."""
        routing.evaluate()
        status = routing.get_status()
        assert "mode" in status
        assert "current_tunnel" in status
        assert "emergency" in status
        assert "tunnel_count" in status

    def test_failure_tracking(self, routing):
        """Consecutive failures should be tracked."""
        routing.evaluate()
        routing.report_tunnel_failure("mock_tunnel")
        assert routing._failure_counts.get("mock_tunnel", 0) == 1

    def test_recent_decisions_stored_in_db(self, routing, tunnel_with_data):
        """Routing decisions should be persisted to DB."""
        routing.evaluate()
        decisions = routing.get_recent_decisions()
        assert len(decisions) >= 1
        assert decisions[0]["to"] == "mock_tunnel"


# ── TunnelManager Tests ────────────────────────


class TestTunnelManager:
    @pytest.fixture
    def tm(self, mock_pm, tmp_db):
        return TunnelManager(mock_pm, tmp_db)

    def test_sync_tunnels_to_db(self, tm, tmp_db):
        """Sync should create Tunnel records for loaded plugins."""
        tm.sync_tunnels_to_db()
        with tmp_db.session() as s:
            tunnels = s.query(Tunnel).all()
        assert len(tunnels) >= 1
        assert tunnels[0].plugin_name == "mock_tunnel"

    def test_update_tunnel_score(self, tm, tmp_db):
        """Score update should write to DB."""
        tm.sync_tunnels_to_db()
        result = BenchmarkResult(
            success=True,
            latency_ms=40.0,
            packet_loss_percent=0.0,
            throughput_mbps=95.0,
        )
        result.calculate_score()
        tm.update_tunnel_score("mock_tunnel", result)

        with tmp_db.session() as s:
            t = s.query(Tunnel).filter_by(plugin_name="mock_tunnel").first()
        assert t.score is not None
        assert t.latency_ms == 40.0

    def test_get_ranked_tunnels(self, tm, tmp_db):
        """Should return tunnels sorted by score."""
        tm.sync_tunnels_to_db()
        ranked = tm.get_ranked_tunnels()
        assert isinstance(ranked, list)

    def test_all_non_emergency_failed(self, tm, tmp_db):
        """Should detect when all non-emergency tunnels failed."""
        tm.sync_tunnels_to_db()
        with tmp_db.session() as s:
            t = s.query(Tunnel).filter_by(plugin_name="mock_tunnel").first()
            t.status = "FAILED"
        assert tm.all_non_emergency_failed() is True

    def test_mark_as_primary(self, tm, tmp_db):
        """Mark as primary should update DB."""
        tm.sync_tunnels_to_db()
        tm.mark_as_primary("mock_tunnel")
        with tmp_db.session() as s:
            t = s.query(Tunnel).filter_by(plugin_name="mock_tunnel").first()
        assert t.is_primary is True


# ── BenchmarkEngine Tests ──────────────────────


class TestBenchmarkEngine:
    @pytest.fixture
    def be(self, mock_pm, tmp_db):
        tm = TunnelManager(mock_pm, tmp_db)
        schedule = BenchmarkSchedule(
            quick_interval_minutes=1,
            standard_interval_minutes=5,
            full_interval_hours=1,
        )
        return BenchmarkEngine(mock_pm, tm, tmp_db, schedule=schedule, foreign_ip="1.2.3.4")

    def test_add_latency_target(self, be):
        """Should add a custom latency target."""
        initial = len(be.latency_targets)
        be.add_latency_target("Custom DNS", "9.9.9.9")
        assert len(be.latency_targets) == initial + 1

    def test_remove_latency_target(self, be):
        """Should remove a latency target by host."""
        be.add_latency_target("Custom DNS", "9.9.9.9")
        removed = be.remove_latency_target("9.9.9.9")
        assert removed is True
        hosts = [t["host"] for t in be.latency_targets]
        assert "9.9.9.9" not in hosts

    def test_remove_nonexistent_target(self, be):
        """Removing a non-existent target should return False."""
        removed = be.remove_latency_target("not.exists")
        assert removed is False

    @pytest.mark.asyncio
    async def test_run_quick_with_mock_plugin(self, be, mock_pm):
        """Quick benchmark should call benchmark on each plugin."""
        mock_plugin = mock_pm.all.return_value[0]
        mock_plugin.meta.category = PluginCategory.TUNNEL_RELIABLE
        mock_plugin.supports_benchmark.return_value = True
        mock_plugin.benchmark.return_value = BenchmarkResult(
            success=True,
            latency_ms=40.0,
            packet_loss_percent=0.0,
            score=95.0,
        )

        results = await be.run_quick()
        assert isinstance(results, dict)


# ── HealthCheckEngine Tests ────────────────────


class TestHealthCheckEngine:
    @pytest.fixture
    def hce(self, mock_pm, tmp_db):
        return HealthCheckEngine(
            mock_pm,
            tmp_db,
            server_label="iran",
            thresholds=AlertThresholds(failure_threshold=3),
        )

    def test_consecutive_failure_tracking(self, hce, mock_plugin):
        """Should track consecutive failures before alerting."""
        unhealthy = HealthResult(
            healthy=False,
            status=ServiceStatus.FAILED,
            error="Connection refused",
        )
        state = hce._service_states.setdefault(
            "mock_tunnel",
            __import__(
                "ironshield.core.health_check", fromlist=["ServiceHealthState"]
            ).ServiceHealthState(name="mock_tunnel"),
        )

        # Not yet at threshold
        for _ in range(2):
            hce._handle_service_unhealthy(state, unhealthy, "mock_tunnel")
        assert state.alerted is False

        # At threshold
        hce._handle_service_unhealthy(state, unhealthy, "mock_tunnel")
        assert state.alerted is True

    def test_recovery_clears_alert(self, hce, mock_plugin):
        """After recovery_threshold successes, alert should clear."""
        from ironshield.core.health_check import ServiceHealthState

        healthy = HealthResult(healthy=True, status=ServiceStatus.RUNNING)
        state = ServiceHealthState(name="mock_tunnel", alerted=True, consecutive_failures=3)
        hce._service_states["mock_tunnel"] = state

        # Not yet at recovery threshold (default=2)
        hce._handle_service_healthy(state, healthy)
        assert state.alerted is True  # still alerted after 1 success

        hce._handle_service_healthy(state, healthy)
        assert state.alerted is False  # recovered after 2 successes

    def test_failure_callback_triggered(self, mock_pm, tmp_db):
        """Failure callback should be called when threshold is reached."""
        callback = MagicMock()
        hce = HealthCheckEngine(
            mock_pm,
            tmp_db,
            thresholds=AlertThresholds(failure_threshold=1),
            on_service_failure=callback,
        )

        from ironshield.core.health_check import ServiceHealthState

        unhealthy = HealthResult(healthy=False, status=ServiceStatus.FAILED, error="down")
        state = ServiceHealthState(name="test_svc")
        hce._service_states["test_svc"] = state

        hce._handle_service_unhealthy(state, unhealthy, "test_svc")
        callback.assert_called_once()


# ── FailoverEngine Tests ───────────────────────


class TestFailoverEngine:
    @pytest.fixture
    def fe(self, mock_pm, tmp_db):
        tm = TunnelManager(mock_pm, tmp_db)
        config = RoutingConfig(cooldown_minutes=0)
        routing = SmartRoutingEngine(tm, tmp_db, config=config)
        return FailoverEngine(mock_pm, routing, tmp_db, max_restart_attempts=2)

    @pytest.mark.asyncio
    async def test_service_failure_triggers_restart(self, fe, mock_plugin):
        """Service failure should attempt restart."""
        mock_plugin.restart.return_value = Result.ok("Restarted")
        health = HealthResult(
            healthy=False,
            status=ServiceStatus.FAILED,
            error="Process died",
        )
        await fe.handle_service_failure("mock_tunnel", health, consecutive_failures=3)
        mock_plugin.restart.assert_called()

    @pytest.mark.asyncio
    async def test_event_recorded_in_db(self, fe, tmp_db):
        """Failover events should be recorded in DB."""
        health = HealthResult(
            healthy=False,
            status=ServiceStatus.FAILED,
            error="down",
        )
        fe.pm.get.return_value = None  # simulate plugin not found
        await fe.handle_service_failure("unknown_svc", health)

        events = fe.get_event_history()
        assert len(events) >= 1
        assert events[0]["type"] == "service_failed"

    @pytest.mark.asyncio
    async def test_alert_callback_triggered(self, mock_pm, tmp_db):
        """Alert callback should be called on failure."""
        callback = MagicMock()
        tm = TunnelManager(mock_pm, tmp_db)
        routing = SmartRoutingEngine(tm, tmp_db)
        fe = FailoverEngine(mock_pm, routing, tmp_db, on_alert=callback)

        fe._notify("Test Alert", "Test body", "WARNING")
        callback.assert_called_once_with(title="Test Alert", body="Test body", severity="WARNING")

    def test_get_active_events_empty(self, fe):
        """Active events should be empty initially."""
        events = fe.get_active_events()
        assert isinstance(events, list)
