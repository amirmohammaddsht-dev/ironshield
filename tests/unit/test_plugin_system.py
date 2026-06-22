"""
Tests for IronShield Plugin System.
Tests: BaseService contract, PluginManager discovery, BenchmarkResult scoring.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from typing import Dict, List, Any
from unittest.mock import patch, MagicMock

from ironshield.services.base import (
    BaseService,
    PluginMeta,
    PluginCategory,
    ServerRole,
    ServiceStatus,
    Result,
    HealthResult,
    BenchmarkResult,
)
from ironshield.core.plugin_manager import PluginManager, PluginLoadError


# ─────────────────────────────────────────────
# Fixtures — minimal concrete plugin for testing
# ─────────────────────────────────────────────


class MockPlugin(BaseService):
    """Minimal concrete plugin for testing BaseService contract."""

    _status = ServiceStatus.RUNNING

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="mock_plugin",
            display_name="Mock Plugin",
            version="1.0.0",
            author="Test",
            source_url="https://example.com",
            license="MIT",
            roles=[ServerRole.IRAN, ServerRole.FOREIGN],
            category=PluginCategory.TUNNEL_RELIABLE,
            priority=5,
        )

    def install(self) -> Result:
        return Result.ok("Installed")

    def uninstall(self) -> Result:
        return Result.ok("Uninstalled")

    def start(self) -> Result:
        self._status = ServiceStatus.RUNNING
        return Result.ok("Started")

    def stop(self) -> Result:
        self._status = ServiceStatus.STOPPED
        return Result.ok("Stopped")

    def status(self) -> ServiceStatus:
        return self._status

    def health_check(self) -> HealthResult:
        return HealthResult(
            healthy=True,
            status=ServiceStatus.RUNNING,
            checks={"process": True, "port": True},
        )

    def get_config(self) -> Dict[str, Any]:
        return {"test": True}

    def apply_config(self, config: Dict[str, Any]) -> Result:
        return Result.ok("Config applied")

    def get_logs(self, lines: int = 100) -> List[str]:
        return ["log line 1", "log line 2"]


@pytest.fixture
def mock_plugin():
    return MockPlugin(server_role=ServerRole.IRAN, config={})


# ─────────────────────────────────────────────
# Result Tests
# ─────────────────────────────────────────────


class TestResult:
    def test_ok_result(self):
        r = Result.ok("Success")
        assert r.success is True
        assert r.message == "Success"
        assert r.error is None

    def test_fail_result(self):
        r = Result.fail("Something went wrong")
        assert r.success is False
        assert r.error == "Something went wrong"

    def test_ok_with_data(self):
        r = Result.ok("Done", key="value", count=42)
        assert r.data["key"] == "value"
        assert r.data["count"] == 42


# ─────────────────────────────────────────────
# BenchmarkResult Scoring Tests
# ─────────────────────────────────────────────


class TestBenchmarkScoring:
    def test_perfect_score(self):
        result = BenchmarkResult(
            success=True,
            latency_ms=30,
            real_delay_ms=40,
            packet_loss_percent=0,
            throughput_mbps=150,
        )
        score = result.calculate_score()
        assert score >= 95

    def test_poor_score(self):
        result = BenchmarkResult(
            success=True,
            latency_ms=900,
            real_delay_ms=1500,
            packet_loss_percent=10,
            throughput_mbps=1,
        )
        score = result.calculate_score()
        assert score < 30

    def test_storm_dns_like_score(self):
        """Storm-DNS should score very low."""
        result = BenchmarkResult(
            success=True,
            latency_ms=890,
            real_delay_ms=1200,
            packet_loss_percent=2.1,
            throughput_mbps=2,
        )
        score = result.calculate_score()
        assert score < 25

    def test_score_stored_on_result(self):
        result = BenchmarkResult(
            success=True,
            latency_ms=40,
            real_delay_ms=55,
            packet_loss_percent=0,
            throughput_mbps=100,
        )
        score = result.calculate_score()
        assert result.score == score

    def test_latency_scoring_boundaries(self):
        assert BenchmarkResult._score_latency(49) == 100
        assert BenchmarkResult._score_latency(99) == 80
        assert BenchmarkResult._score_latency(199) == 60
        assert BenchmarkResult._score_latency(499) == 30
        assert BenchmarkResult._score_latency(1000) == 10

    def test_packet_loss_scoring(self):
        assert BenchmarkResult._score_packet_loss(0) == 100
        assert BenchmarkResult._score_packet_loss(0.5) == 80
        assert BenchmarkResult._score_packet_loss(2) == 50
        assert BenchmarkResult._score_packet_loss(4) == 20
        assert BenchmarkResult._score_packet_loss(10) == 0


# ─────────────────────────────────────────────
# BaseService Tests
# ─────────────────────────────────────────────


class TestBaseService:
    def test_meta_properties(self, mock_plugin):
        assert mock_plugin.meta.name == "mock_plugin"
        assert mock_plugin.meta.version == "1.0.0"
        assert mock_plugin.meta.priority == 5

    def test_lifecycle(self, mock_plugin):
        result = mock_plugin.start()
        assert result.success is True
        assert mock_plugin.is_running() is True

        result = mock_plugin.stop()
        assert result.success is True
        assert mock_plugin.is_running() is False

    def test_restart_default_implementation(self, mock_plugin):
        mock_plugin.start()
        result = mock_plugin.restart()
        assert result.success is True
        assert mock_plugin.is_running() is True

    def test_health_check(self, mock_plugin):
        health = mock_plugin.health_check()
        assert health.healthy is True
        assert health.checks["process"] is True
        assert health.checks["port"] is True

    def test_get_logs(self, mock_plugin):
        logs = mock_plugin.get_logs(lines=10)
        assert isinstance(logs, list)
        assert len(logs) == 2

    def test_supports_role(self, mock_plugin):
        assert mock_plugin.meta.supports_role(ServerRole.IRAN) is True
        assert mock_plugin.meta.supports_role(ServerRole.FOREIGN) is True

    def test_benchmark_not_supported_by_default(self, mock_plugin):
        assert mock_plugin.supports_benchmark() is False
        result = mock_plugin.benchmark()
        assert result.success is False

    def test_repr(self, mock_plugin):
        r = repr(mock_plugin)
        assert "MockPlugin" in r
        assert "mock_plugin" in r

    def test_install_and_uninstall(self, mock_plugin):
        assert mock_plugin.install().success is True
        assert mock_plugin.uninstall().success is True

    def test_apply_config(self, mock_plugin):
        result = mock_plugin.apply_config({"new_key": "new_value"})
        assert result.success is True


# ─────────────────────────────────────────────
# PluginMeta Tests
# ─────────────────────────────────────────────


class TestPluginMeta:
    def test_supports_both_role(self):
        meta = PluginMeta(
            name="test",
            display_name="Test",
            version="1.0",
            author="Test",
            source_url="",
            license="MIT",
            roles=[ServerRole.BOTH],
            category=PluginCategory.TUNNEL_RELIABLE,
            priority=5,
        )
        assert meta.supports_role(ServerRole.IRAN) is True
        assert meta.supports_role(ServerRole.FOREIGN) is True

    def test_supports_single_role(self):
        meta = PluginMeta(
            name="test",
            display_name="Test",
            version="1.0",
            author="Test",
            source_url="",
            license="MIT",
            roles=[ServerRole.IRAN],
            category=PluginCategory.VPN,
            priority=1,
        )
        assert meta.supports_role(ServerRole.IRAN) is True
        assert meta.supports_role(ServerRole.FOREIGN) is False


# ─────────────────────────────────────────────
# PluginManager Tests
# ─────────────────────────────────────────────


class TestPluginManager:
    def test_empty_discovery_when_no_plugins_dir(self, tmp_path):
        """Should not crash when plugins dir doesn't exist."""
        with patch("ironshield.core.plugin_manager.PLUGINS_ROOT", tmp_path / "nonexistent"):
            pm = PluginManager(server_role=ServerRole.IRAN, global_config={})
            loaded = pm.discover()
            assert loaded == []

    def test_discover_valid_plugin(self, tmp_path):
        """Should load a valid plugin with all required files."""
        plugin_dir = tmp_path / "tunnels" / "test_tunnel"
        plugin_dir.mkdir(parents=True)

        # Write plugin.yaml
        (plugin_dir / "plugin.yaml").write_text(
            """
name: test_tunnel
display_name: Test Tunnel
version: "1.0.0"
author: Test
source: https://example.com
license: MIT
roles: [iran, foreign]
category: tunnel_reliable
priority: 5
"""
        )

        # Write service.py with a valid BaseService subclass
        (plugin_dir / "service.py").write_text(
            """
from ironshield.services.base import BaseService, PluginMeta, PluginCategory, ServerRole, ServiceStatus, Result, HealthResult
from typing import Dict, List, Any

class TestTunnelService(BaseService):
    @property
    def meta(self):
        return PluginMeta(
            name="test_tunnel", display_name="Test Tunnel", version="1.0.0",
            author="Test", source_url="", license="MIT",
            roles=[ServerRole.IRAN, ServerRole.FOREIGN],
            category=PluginCategory.TUNNEL_RELIABLE, priority=5,
        )
    def install(self): return Result.ok()
    def uninstall(self): return Result.ok()
    def start(self): return Result.ok()
    def stop(self): return Result.ok()
    def status(self): return ServiceStatus.RUNNING
    def health_check(self): return HealthResult(healthy=True, status=ServiceStatus.RUNNING)
    def get_config(self): return {}
    def apply_config(self, config): return Result.ok()
    def get_logs(self, lines=100): return []
"""
        )

        # Write required shell scripts
        for fname in ["install.sh", "uninstall.sh", "update.sh"]:
            (plugin_dir / fname).write_text("#!/bin/bash\necho ok\n")

        with patch("ironshield.core.plugin_manager.PLUGINS_ROOT", tmp_path):
            pm = PluginManager(server_role=ServerRole.IRAN, global_config={})
            loaded = pm.discover()

        assert "test_tunnel" in loaded
        assert pm.get("test_tunnel") is not None

    def test_skip_plugin_wrong_role(self, tmp_path):
        """Plugin with role=[foreign] should be skipped on Iran server."""
        plugin_dir = tmp_path / "tunnels" / "foreign_only"
        plugin_dir.mkdir(parents=True)

        (plugin_dir / "plugin.yaml").write_text(
            """
name: foreign_only
display_name: Foreign Only
version: "1.0.0"
author: Test
source: https://example.com
license: MIT
roles: [foreign]
category: tunnel_reliable
priority: 5
"""
        )
        for fname in ["install.sh", "uninstall.sh", "update.sh", "service.py"]:
            (plugin_dir / fname).write_text("")

        with patch("ironshield.core.plugin_manager.PLUGINS_ROOT", tmp_path):
            pm = PluginManager(server_role=ServerRole.IRAN, global_config={})
            loaded = pm.discover()

        assert "foreign_only" not in loaded

    def test_fail_missing_files(self, tmp_path):
        """Plugin missing required files should raise PluginLoadError."""
        plugin_dir = tmp_path / "tunnels" / "incomplete"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.yaml").write_text("name: incomplete\n")
        # Missing install.sh, service.py, etc.

        with patch("ironshield.core.plugin_manager.PLUGINS_ROOT", tmp_path):
            pm = PluginManager(server_role=ServerRole.IRAN, global_config={})
            loaded = pm.discover()

        assert "incomplete" not in loaded
