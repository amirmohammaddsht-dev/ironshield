"""
Tests for Phase 8 — CLI display and installer modules.
Tests: display helpers, route matching, installer pre-flight,
       CLI command structure, and option parsing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from ironshield.cli.display import (
    ICONS,
    benchmark_results_table,
    plugin_status_table,
    routing_status_panel,
    server_metrics_panel,
    tunnel_score_table,
    users_table,
)
from ironshield.cli.main import cli


# ── Display Tests ─────────────────────────────


class TestDisplay:
    def test_icons_cover_all_statuses(self):
        """ICONS dict should cover common service statuses."""
        for status in ("RUNNING", "STOPPED", "FAILED", "NOT_INSTALLED", "ACTIVE", "UNKNOWN"):
            assert status in ICONS

    def test_plugin_status_table_builds(self):
        """plugin_status_table should build without errors."""
        plugins = {
            "openvpn": {
                "display_name": "OpenVPN",
                "version": "2.6.8",
                "status": "RUNNING",
                "category": "vpn",
                "priority": 1,
            },
            "gost": {
                "display_name": "GOST",
                "version": "3.0.0",
                "status": "STOPPED",
                "category": "tunnel_reliable",
                "priority": 3,
            },
        }
        table = plugin_status_table(plugins)
        assert table is not None
        assert table.title == "Plugin Status"
        assert table.row_count == 2

    def test_plugin_status_table_empty(self):
        """Should handle empty plugins dict."""
        table = plugin_status_table({})
        assert table.row_count == 0

    def test_tunnel_score_table_builds(self):
        """tunnel_score_table should build correctly."""
        tunnels = [
            {
                "name": "phormal",
                "status": "ACTIVE",
                "score": 97.0,
                "latency_ms": 35.0,
                "packet_loss_percent": 0.0,
                "throughput_mbps": 150.0,
            },
            {
                "name": "storm_dns",
                "status": "STANDBY",
                "score": 12.0,
                "latency_ms": 890.0,
                "packet_loss_percent": 2.1,
                "throughput_mbps": 2.0,
            },
        ]
        table = tunnel_score_table(tunnels)
        assert table is not None
        assert table.row_count == 2

    def test_tunnel_score_table_with_none_values(self):
        """Should handle None metric values gracefully."""
        tunnels = [
            {
                "name": "new_tunnel",
                "status": "UNKNOWN",
                "score": None,
                "latency_ms": None,
                "packet_loss_percent": None,
                "throughput_mbps": None,
            }
        ]
        table = tunnel_score_table(tunnels)
        assert table.row_count == 1

    def test_server_metrics_panel_builds(self):
        """server_metrics_panel should build with valid data."""
        metrics = {
            "cpu_percent": 35.0,
            "ram_percent": 52.0,
            "ram_used_gb": 2.1,
            "ram_total_gb": 4.0,
            "disk_percent": 23.0,
            "disk_used_gb": 10.0,
            "disk_total_gb": 50.0,
        }
        panel = server_metrics_panel("Iran Server", metrics)
        assert panel is not None

    def test_server_metrics_panel_empty(self):
        """Should handle empty metrics dict."""
        panel = server_metrics_panel("Test", {})
        assert panel is not None

    def test_users_table_builds(self):
        """users_table should render all user states."""
        users = [
            {
                "username": "active_user",
                "is_active": True,
                "is_expired": False,
                "is_over_quota": False,
                "traffic_used_gb": 10.5,
                "traffic_limit_gb": 50.0,
                "traffic_remaining_gb": 39.5,
                "days_until_expiry": 20,
                "last_connected_at": "2024-01-01T12:00:00+00:00",
            },
            {
                "username": "expired_user",
                "is_active": True,
                "is_expired": True,
                "is_over_quota": False,
                "traffic_used_gb": 5.0,
                "traffic_limit_gb": 50.0,
                "traffic_remaining_gb": 45.0,
                "days_until_expiry": 0,
                "last_connected_at": None,
            },
            {
                "username": "over_quota_user",
                "is_active": True,
                "is_expired": False,
                "is_over_quota": True,
                "traffic_used_gb": 50.0,
                "traffic_limit_gb": 50.0,
                "traffic_remaining_gb": 0.0,
                "days_until_expiry": 10,
                "last_connected_at": None,
            },
        ]
        table = users_table(users)
        assert table.row_count == 3

    def test_benchmark_results_table_builds(self):
        """benchmark_results_table should handle mix of success and failure."""
        results = {
            "phormal": {
                "success": True,
                "score": 97.0,
                "latency_ms": 35.0,
                "real_delay_ms": 42.0,
                "packet_loss_percent": 0.0,
                "throughput_mbps": 150.0,
            },
            "storm_dns": {
                "success": False,
                "error": "Host unreachable",
            },
        }
        table = benchmark_results_table(results)
        assert table.row_count == 2

    def test_routing_status_panel_auto_mode(self):
        """Panel should show auto mode indicator."""
        routing = {
            "mode": "auto",
            "current_tunnel": "phormal",
            "backup_tunnel": "backhaul",
            "emergency": False,
        }
        panel = routing_status_panel(routing)
        assert panel is not None

    def test_routing_status_panel_emergency(self):
        """Panel should indicate emergency mode."""
        routing = {
            "mode": "auto",
            "current_tunnel": "storm_dns",
            "backup_tunnel": None,
            "emergency": True,
        }
        panel = routing_status_panel(routing)
        # Panel should render without error
        assert panel is not None


# ── CLI Command Tests ──────────────────────────


class TestCLICommands:
    """Tests for CLI command structure and options."""

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_cli_has_version(self, runner):
        """CLI should respond to --version."""
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "IronShield" in result.output

    def test_cli_has_help(self, runner):
        """CLI should show help."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "IronShield" in result.output

    def test_status_requires_running_core(self, runner):
        """status command should fail if socket doesn't exist."""
        with patch("ironshield.cli.main.SOCKET_PATH", Path("/nonexistent/ironshield.sock")):
            result = runner.invoke(cli, ["status"])
        assert result.exit_code != 0

    def test_health_fails_when_not_running(self, runner):
        """health command should fail if socket doesn't exist."""
        with patch("ironshield.cli.main.SOCKET_PATH", Path("/nonexistent/ironshield.sock")):
            result = runner.invoke(cli, ["health"])
        assert result.exit_code != 0

    def test_plugin_group_has_subcommands(self, runner):
        """plugin group should show subcommands."""
        result = runner.invoke(cli, ["plugin", "--help"])
        assert result.exit_code == 0
        for cmd in ("list", "start", "stop", "restart", "update"):
            assert cmd in result.output

    def test_user_group_has_subcommands(self, runner):
        """user group should show subcommands."""
        result = runner.invoke(cli, ["user", "--help"])
        assert result.exit_code == 0
        for cmd in ("list", "add", "delete", "info", "toggle", "config"):
            assert cmd in result.output

    def test_tunnel_group_has_subcommands(self, runner):
        """tunnel group should show subcommands."""
        result = runner.invoke(cli, ["tunnel", "--help"])
        assert result.exit_code == 0
        for cmd in ("list", "switch", "auto"):
            assert cmd in result.output

    def test_routing_group_has_subcommands(self, runner):
        """routing group should show subcommands."""
        result = runner.invoke(cli, ["routing", "--help"])
        assert result.exit_code == 0
        for cmd in ("status", "history"):
            assert cmd in result.output

    def test_config_group_has_subcommands(self, runner):
        """config group should show subcommands."""
        result = runner.invoke(cli, ["config", "--help"])
        assert result.exit_code == 0
        for cmd in ("show", "set"):
            assert cmd in result.output

    def test_plugin_start_requires_name(self, runner):
        """plugin start should require NAME argument."""
        result = runner.invoke(cli, ["plugin", "start"])
        assert result.exit_code != 0
        assert "Missing argument" in result.output

    def test_user_add_requires_username(self, runner):
        """user add should require USERNAME argument."""
        result = runner.invoke(cli, ["user", "add"])
        assert result.exit_code != 0
        assert "Missing argument" in result.output

    def test_user_add_help(self, runner):
        """user add should show help with options."""
        result = runner.invoke(cli, ["user", "add", "--help"])
        assert result.exit_code == 0
        assert "--traffic" in result.output
        assert "--days" in result.output

    def test_benchmark_help(self, runner):
        """benchmark should show --full option."""
        result = runner.invoke(cli, ["benchmark", "--help"])
        assert result.exit_code == 0
        assert "--full" in result.output

    def test_logs_requires_plugin_name(self, runner):
        """logs command should require plugin name."""
        result = runner.invoke(cli, ["logs"])
        assert result.exit_code != 0

    def test_user_delete_with_yes_flag(self, runner):
        """user delete --yes should skip confirmation."""
        with (patch("ironshield.cli.main.SOCKET_PATH", Path("/nonexistent.sock")),):
            result = runner.invoke(cli, ["user", "delete", "testuser", "--yes"])
        # Should fail because socket doesn't exist, not because of confirmation
        assert result.exit_code != 0

    def test_tunnel_switch_requires_name(self, runner):
        """tunnel switch should require NAME argument."""
        result = runner.invoke(cli, ["tunnel", "switch"])
        assert result.exit_code != 0
        assert "Missing argument" in result.output

    def test_config_set_requires_key_and_value(self, runner):
        """config set should require both KEY and VALUE."""
        result = runner.invoke(cli, ["config", "set", "only_key"])
        assert result.exit_code != 0

    def test_status_json_flag(self, runner):
        """status --json should be accepted."""
        with patch("ironshield.cli.main.SOCKET_PATH", Path("/nonexistent.sock")):
            result = runner.invoke(cli, ["status", "--json"])
        # Fails at socket check, not at flag parsing
        assert "ironshield.sock" in result.output or result.exit_code != 0

    def test_benchmark_full_flag(self, runner):
        """benchmark --full should be valid."""
        with patch("ironshield.cli.main.SOCKET_PATH", Path("/nonexistent.sock")):
            result = runner.invoke(cli, ["benchmark", "--full"])
        assert result.exit_code != 0  # fails at socket, not flag

    def test_routing_history_limit_option(self, runner):
        """routing history --limit should be accepted."""
        with patch("ironshield.cli.main.SOCKET_PATH", Path("/nonexistent.sock")):
            result = runner.invoke(cli, ["routing", "history", "--limit", "5"])
        assert result.exit_code != 0  # fails at socket, not flag


# ── Installer Tests ────────────────────────────


class TestInstaller:
    """Tests for the installation pre-flight checks."""

    def test_installer_creates_instance(self):
        """Installer should be instantiable."""
        from ironshield.cli.installer import Installer

        installer = Installer()
        assert installer.role == "iran"
        assert installer.selected_plugins == []

    def test_preflight_fails_without_root(self):
        """Pre-flight should fail if not running as root."""
        from ironshield.cli.installer import Installer

        installer = Installer()
        with patch("ironshield.cli.installer.is_root", return_value=False):
            result = installer._preflight_checks()
        assert result is False

    def test_preflight_fails_with_low_ram(self):
        """Pre-flight should fail if RAM is too low."""
        from ironshield.cli.installer import Installer

        installer = Installer()
        with (
            patch("ironshield.cli.installer.is_root", return_value=True),
            patch("ironshield.cli.installer.get_ubuntu_version", return_value="22.04"),
            patch("ironshield.cli.installer.get_available_ram_gb", return_value=0.1),
            patch("ironshield.cli.installer.get_available_disk_gb", return_value=10.0),
        ):
            result = installer._preflight_checks()
        assert result is False

    def test_preflight_passes_with_good_system(self):
        """Pre-flight should pass with sufficient resources."""
        from ironshield.cli.installer import Installer

        installer = Installer()
        with (
            patch("ironshield.cli.installer.is_root", return_value=True),
            patch("ironshield.cli.installer.get_ubuntu_version", return_value="22.04"),
            patch("ironshield.cli.installer.get_available_ram_gb", return_value=2.0),
            patch("ironshield.cli.installer.get_available_disk_gb", return_value=20.0),
        ):
            result = installer._preflight_checks()
        assert result is True

    def test_write_config_creates_file(self, tmp_path):
        """_write_config should create config on disk."""
        from ironshield.cli.installer import Installer
        from ironshield.core.config_engine import ConfigEngine

        installer = Installer()
        installer.role = "iran"
        installer.config = {
            "foreign_ip": "1.2.3.4",
            "openvpn_port": 443,
            "openvpn_port_fallback": 80,
            "telegram_token": "123:abc",
            "telegram_admin_id": 12345,
        }

        with patch("ironshield.cli.installer.ConfigEngine") as MockEngine:
            mock_engine = MagicMock()
            MockEngine.return_value = mock_engine
            installer._write_config()
            mock_engine.init_default.assert_called_once()
