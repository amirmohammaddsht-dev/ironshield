"""
IronShield - Base Service Class
Path: ironshield/services/base.py
Purpose: Abstract base class that every plugin must implement.
         Defines the full contract for install, lifecycle, health, benchmark, and monitoring.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any

from ironshield.utils.logger import get_logger

logger = get_logger("base_service")


# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────


class ServiceStatus(str, Enum):
    """All possible states of a service."""

    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    INSTALLING = "INSTALLING"
    CONFIGURING = "CONFIGURING"
    RESTARTING = "RESTARTING"
    NOT_INSTALLED = "NOT_INSTALLED"
    DISABLED = "DISABLED"
    UNKNOWN = "UNKNOWN"


class ServerRole(str, Enum):
    """Which server a plugin runs on."""

    IRAN = "iran"
    FOREIGN = "foreign"
    BOTH = "both"


class PluginCategory(str, Enum):
    """Plugin category for routing priority."""

    VPN = "vpn"
    TUNNEL_FAST = "tunnel_fast"
    TUNNEL_RELIABLE = "tunnel_reliable"
    TUNNEL_OBFUSCATED = "tunnel_obfuscated"
    DNS = "dns"
    EMERGENCY = "emergency"


# ─────────────────────────────────────────────
# Result dataclasses
# ─────────────────────────────────────────────


@dataclass
class Result:
    """Generic operation result."""

    success: bool
    message: str = ""
    error: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, message: str = "OK", **data) -> "Result":
        return cls(success=True, message=message, data=data)

    @classmethod
    def fail(cls, error: str, message: str = "") -> "Result":
        return cls(success=False, message=message, error=error)


@dataclass
class HealthResult:
    """Result of a health check."""

    healthy: bool
    status: ServiceStatus
    checks: Dict[str, bool] = field(default_factory=dict)
    message: str = ""
    error: Optional[str] = None


@dataclass
class BenchmarkResult:
    """Result of a benchmark test for a tunnel plugin."""

    success: bool
    latency_ms: Optional[float] = None
    real_delay_ms: Optional[float] = None
    packet_loss_percent: Optional[float] = None
    throughput_mbps: Optional[float] = None
    score: Optional[float] = None
    error: Optional[str] = None

    def calculate_score(
        self,
        latency_weight: float = 0.25,
        real_delay_weight: float = 0.30,
        loss_weight: float = 0.30,
        throughput_weight: float = 0.15,
    ) -> float:
        """
        Calculate composite score from benchmark results.

        Score formula:
            score = (latency_score * 0.25)
                  + (real_delay_score * 0.30)
                  + (packet_loss_score * 0.30)
                  + (throughput_score * 0.15)
        """
        latency_score = self._score_latency(
            self.latency_ms if self.latency_ms is not None else 9999
        )
        delay_score = self._score_real_delay(
            self.real_delay_ms if self.real_delay_ms is not None else 9999
        )
        loss_score = self._score_packet_loss(
            self.packet_loss_percent if self.packet_loss_percent is not None else 100
        )
        throughput_score = self._score_throughput(
            self.throughput_mbps if self.throughput_mbps is not None else 0
        )

        score = (
            latency_score * latency_weight
            + delay_score * real_delay_weight
            + loss_score * loss_weight
            + throughput_score * throughput_weight
        )

        self.score = round(score, 2)
        return self.score

    @staticmethod
    def _score_latency(ms: float) -> float:
        if ms < 50:
            return 100
        if ms < 100:
            return 80
        if ms < 200:
            return 60
        if ms < 500:
            return 30
        return 10

    @staticmethod
    def _score_real_delay(ms: float) -> float:
        if ms < 60:
            return 100
        if ms < 100:
            return 85
        if ms < 150:
            return 70
        if ms < 300:
            return 45
        if ms < 500:
            return 20
        return 5

    @staticmethod
    def _score_packet_loss(percent: float) -> float:
        if percent == 0:
            return 100
        if percent < 1:
            return 80
        if percent < 3:
            return 50
        if percent < 5:
            return 20
        return 0

    @staticmethod
    def _score_throughput(mbps: float) -> float:
        if mbps > 100:
            return 100
        if mbps > 50:
            return 80
        if mbps > 20:
            return 60
        if mbps > 10:
            return 40
        return 20


# ─────────────────────────────────────────────
# Plugin Metadata
# ─────────────────────────────────────────────


@dataclass
class PluginMeta:
    """
    Static metadata about a plugin.
    Loaded from plugin.yaml at startup.
    """

    name: str
    display_name: str
    version: str
    author: str
    source_url: str
    license: str
    roles: List[ServerRole]
    category: PluginCategory
    priority: int
    required: bool = False
    min_ironshield_version: str = "1.0.0"
    description: str = ""

    # Auto-update configuration
    auto_update_enabled: bool = True
    auto_update_source: str = "github_release"  # github_release | apt | url
    auto_update_repo: Optional[str] = None
    auto_update_url: Optional[str] = None

    # UFW ports opened by this plugin
    ufw_ports: List[Dict[str, Any]] = field(default_factory=list)

    def supports_role(self, role: ServerRole) -> bool:
        """Check if this plugin supports a given server role."""
        return role in self.roles or ServerRole.BOTH in self.roles


# ─────────────────────────────────────────────
# Abstract Base Service
# ─────────────────────────────────────────────


class BaseService(abc.ABC):
    """
    Abstract base class for all IronShield plugins.

    Every plugin (tunnel, VPN, DNS) must extend this class
    and implement all abstract methods.

    Plugin directory structure:
        plugins/<category>/<name>/
            plugin.yaml     ← metadata (parsed into PluginMeta)
            install.sh      ← installation script
            uninstall.sh    ← removal script
            update.sh       ← update script
            service.py      ← this class implementation

    Usage:
        class MyTunnel(BaseService):
            @property
            def meta(self) -> PluginMeta:
                return PluginMeta(name="my_tunnel", ...)

            def install(self) -> Result:
                ...
    """

    def __init__(self, server_role: ServerRole, config: Dict[str, Any]):
        """
        Args:
            server_role: Which server this instance runs on (iran/foreign)
            config: Plugin configuration from configs/tunnels/<name>.yaml
        """
        self.server_role = server_role
        self.config = config
        self.logger = get_logger(f"plugin.{self.meta.name}")

    # ── Metadata ─────────────────────────────

    @property
    @abc.abstractmethod
    def meta(self) -> PluginMeta:
        """Return static plugin metadata."""
        ...

    # ── Lifecycle ────────────────────────────

    @abc.abstractmethod
    def install(self) -> Result:
        """
        Download, install, and register the service.
        Must be idempotent — safe to call multiple times.
        """
        ...

    @abc.abstractmethod
    def uninstall(self) -> Result:
        """Remove the service and all its files."""
        ...

    @abc.abstractmethod
    def start(self) -> Result:
        """Start the service."""
        ...

    @abc.abstractmethod
    def stop(self) -> Result:
        """Stop the service."""
        ...

    def restart(self) -> Result:
        """
        Restart the service.
        Default implementation: stop → start.
        Override for plugin-specific restart logic.
        """
        stop_result = self.stop()
        if not stop_result.success:
            self.logger.warning(f"Stop failed during restart: {stop_result.error}")
        return self.start()

    @abc.abstractmethod
    def status(self) -> ServiceStatus:
        """Return the current status of the service."""
        ...

    # ── Health Check ─────────────────────────

    @abc.abstractmethod
    def health_check(self) -> HealthResult:
        """
        Perform a comprehensive health check.
        Should check: process running, port open, functional test.
        """
        ...

    # ── Configuration ─────────────────────────

    @abc.abstractmethod
    def get_config(self) -> Dict[str, Any]:
        """Return current effective configuration."""
        ...

    @abc.abstractmethod
    def apply_config(self, config: Dict[str, Any]) -> Result:
        """
        Apply new configuration.
        Should validate before applying.
        Restart service if needed.
        """
        ...

    def validate_config(self, config: Dict[str, Any]) -> Result:
        """
        Validate configuration before applying.
        Override to add plugin-specific validation.
        """
        return Result.ok("Configuration is valid")

    # ── Logs ──────────────────────────────────

    @abc.abstractmethod
    def get_logs(self, lines: int = 100) -> List[str]:
        """Return the last N lines of service logs."""
        ...

    # ── Benchmark (optional for non-tunnels) ──

    def benchmark(self) -> BenchmarkResult:
        """
        Run benchmark tests for this plugin.
        Default implementation returns an empty result.
        Override in tunnel plugins.
        """
        return BenchmarkResult(success=False, error="Benchmark not supported by this plugin")

    def supports_benchmark(self) -> bool:
        """Whether this plugin supports benchmarking."""
        return False

    # ── Update ────────────────────────────────

    def update(self) -> Result:
        """
        Update the plugin to the latest version.
        Default: run update.sh script.
        Override for custom update logic.
        """
        from ironshield.utils.system import run_command
        from pathlib import Path

        update_script = (
            Path(__file__).parent.parent.parent
            / "plugins"
            / self.meta.category.value
            / self.meta.name
            / "update.sh"
        )

        if not update_script.exists():
            return Result.fail(f"Update script not found: {update_script}")

        self.logger.info(f"Updating {self.meta.name}...")
        code, out, err = run_command(f"bash {update_script}", timeout=120)

        if code != 0:
            return Result.fail(f"Update failed: {err}")

        return Result.ok(f"{self.meta.name} updated successfully")

    def get_latest_version(self) -> Optional[str]:
        """
        Check for the latest available version.
        Returns None if unable to check.
        Override for plugin-specific version checking.
        """
        return None

    # ── UFW Management ───────────────────────

    def get_ufw_rules(self) -> List[Dict[str, Any]]:
        """
        Return list of UFW rules this plugin needs.
        Used by UFW Manager during install/uninstall.

        Format:
            [{"port": 443, "protocol": "tcp", "from_ip": None}]
        """
        return self.meta.ufw_ports

    # ── Monitoring Metrics ───────────────────

    def get_metrics(self) -> Dict[str, Any]:
        """
        Return current plugin-specific metrics.
        Override to provide custom monitoring data.
        """
        return {
            "plugin": self.meta.name,
            "status": self.status().value,
            "version": self.meta.version,
        }

    # ── Helpers ──────────────────────────────

    def is_installed(self) -> bool:
        """Check if the plugin is installed."""
        return self.status() != ServiceStatus.NOT_INSTALLED

    def is_running(self) -> bool:
        """Check if the plugin is currently running."""
        return self.status() == ServiceStatus.RUNNING

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.meta.name} role={self.server_role.value}>"
