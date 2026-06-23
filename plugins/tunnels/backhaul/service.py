"""
IronShield Plugin — backhaul
Path: plugins/tunnels/backhaul/service.py
Purpose: backhaul tunnel implementation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Any

from ironshield.services.base import (
    BaseService,
    BenchmarkResult,
    HealthResult,
    PluginCategory,
    PluginMeta,
    Result,
    ServerRole,
    ServiceStatus,
)
from ironshield.utils.logger import get_service_logger
from ironshield.utils.network import ping, measure_packet_loss
from ironshield.utils.system import run_command, service_is_active, systemctl, port_is_open

logger = get_service_logger("backhaul")

SYSTEMD_SERVICE = "ironshield-backhaul"
BIN_PATH = Path("/usr/local/bin/backhaul")


class BackhaulService(BaseService):
    """backhaul tunnel plugin."""

    @property
    def meta(self) -> PluginMeta:
        # Metadata loaded from plugin.yaml by PluginManager
        return PluginMeta(
            name="backhaul",
            display_name="backhaul",
            version="latest",
            author="",
            source_url="",
            license="MIT",
            roles=[ServerRole.IRAN, ServerRole.FOREIGN],
            category=PluginCategory.TUNNEL_RELIABLE,
            priority=3,
        )

    def install(self) -> Result:
        return Result.fail("Full implementation — Phase 10 scripts")

    def uninstall(self) -> Result:
        systemctl("stop", SYSTEMD_SERVICE)
        systemctl("disable", SYSTEMD_SERVICE)
        return Result.ok("backhaul uninstalled")

    def start(self) -> Result:
        ok = systemctl("start", SYSTEMD_SERVICE)
        return Result.ok("backhaul started") if ok else Result.fail("Failed to start backhaul")

    def stop(self) -> Result:
        systemctl("stop", SYSTEMD_SERVICE)
        return Result.ok("backhaul stopped")

    def status(self) -> ServiceStatus:
        if not BIN_PATH.exists():
            return ServiceStatus.NOT_INSTALLED
        return (
            ServiceStatus.RUNNING if service_is_active(SYSTEMD_SERVICE) else ServiceStatus.STOPPED
        )

    def health_check(self) -> HealthResult:
        checks = {"process": service_is_active(SYSTEMD_SERVICE)}
        healthy = checks["process"]
        return HealthResult(
            healthy=healthy,
            status=ServiceStatus.RUNNING if healthy else ServiceStatus.STOPPED,
            checks=checks,
        )

    def get_config(self) -> Dict[str, Any]:
        return self.config

    def apply_config(self, config: Dict[str, Any]) -> Result:
        self.config.update(config)
        return self.restart()

    def get_logs(self, lines: int = 100) -> List[str]:
        code, out, _ = run_command(f"journalctl -u {SYSTEMD_SERVICE} -n {lines} --no-pager")
        return out.splitlines() if code == 0 else []

    def supports_benchmark(self) -> bool:
        return True

    def benchmark(self) -> BenchmarkResult:
        remote_host = self.config.get("remote_host", "")
        if not remote_host:
            return BenchmarkResult(success=False, error="No remote host configured")
        ping_result = ping(remote_host, count=10)
        if not ping_result.success:
            return BenchmarkResult(success=False, error="Remote host unreachable")
        loss = measure_packet_loss(remote_host, cycles=20)
        result = BenchmarkResult(
            success=True,
            latency_ms=round(ping_result.avg_ms, 2),
            packet_loss_percent=round(loss, 2),
        )
        result.calculate_score()
        return result
