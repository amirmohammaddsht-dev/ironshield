"""
IronShield Plugin — Phormal
Path: plugins/tunnels/phormal/service.py
Purpose: Phormal tunnel — Bridge (SIT/IPv6) and Relay (QUIC+obfs) modes.
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
from ironshield.utils.network import ping, measure_packet_loss, measure_throughput
from ironshield.utils.system import run_command, service_is_active, systemctl

logger = get_service_logger("phormal")

PHORMAL_BIN = Path("/usr/local/bin/phormal")
PHORMAL_SCRIPT = "https://raw.githubusercontent.com/Schmi7zz/Phormal/main/phormal.sh"
SYSTEMD_SERVICE = "ironshield-phormal"


class PhormalService(BaseService):
    """
    Phormal tunnel plugin.
    Bridge mode: fastest, SIT IPv6 private link.
    Relay mode: QUIC + Salamander obfuscation — best for filtered networks.
    """

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="phormal",
            display_name="Phormal Tunnel",
            version="1.0.0",
            author="Schmi7zz",
            source_url="https://github.com/Schmi7zz/Phormal",
            license="GPL-3.0",
            roles=[ServerRole.IRAN, ServerRole.FOREIGN],
            category=PluginCategory.TUNNEL_FAST,
            priority=1,
            description="Fast tunnel: Bridge (SIT) + Relay (QUIC+obfs)",
            ufw_ports=[{"port": 8531, "protocol": "udp", "description": "Phormal link"}],
        )

    def install(self) -> Result:
        """Download and install Phormal via its install script."""
        logger.info("Installing Phormal...")

        code, _, err = run_command(f"curl -fsSL {PHORMAL_SCRIPT} | bash", timeout=120)
        if code != 0:
            return Result.fail(f"Phormal installation failed: {err}")

        if not PHORMAL_BIN.exists():
            return Result.fail("Phormal binary not found after installation")

        result = self._write_systemd_service()
        if not result.success:
            return result

        systemctl("enable", SYSTEMD_SERVICE)
        logger.info("Phormal installed successfully")
        return Result.ok("Phormal installed")

    def _write_systemd_service(self) -> Result:
        """Write systemd unit file for Phormal."""
        mode = self.config.get("mode", "relay")
        peer_ip = self.config.get("peer_ip", "")
        port = self.config.get("port", 8531)

        unit = f"""[Unit]
Description=IronShield Phormal Tunnel
After=network.target

[Service]
Type=simple
User=ironshield
ExecStart={PHORMAL_BIN} {mode} --peer {peer_ip} --port {port}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        try:
            Path(f"/etc/systemd/system/{SYSTEMD_SERVICE}.service").write_text(unit)
            run_command("systemctl daemon-reload")
            return Result.ok()
        except Exception as e:
            return Result.fail(f"Failed to write systemd unit: {e}")

    def uninstall(self) -> Result:
        systemctl("stop", SYSTEMD_SERVICE)
        systemctl("disable", SYSTEMD_SERVICE)
        service_file = Path(f"/etc/systemd/system/{SYSTEMD_SERVICE}.service")
        if service_file.exists():
            service_file.unlink()
        if PHORMAL_BIN.exists():
            PHORMAL_BIN.unlink()
        run_command("systemctl daemon-reload")
        return Result.ok("Phormal uninstalled")

    def start(self) -> Result:
        ok = systemctl("start", SYSTEMD_SERVICE)
        return Result.ok("Phormal started") if ok else Result.fail("Failed to start Phormal")

    def stop(self) -> Result:
        systemctl("stop", SYSTEMD_SERVICE)
        return Result.ok("Phormal stopped")

    def status(self) -> ServiceStatus:
        if not PHORMAL_BIN.exists():
            return ServiceStatus.NOT_INSTALLED
        return (
            ServiceStatus.RUNNING if service_is_active(SYSTEMD_SERVICE) else ServiceStatus.STOPPED
        )

    def health_check(self) -> HealthResult:
        checks = {"process": service_is_active(SYSTEMD_SERVICE)}

        peer_ip = self.config.get("peer_ip", "")
        if peer_ip:
            result = ping(peer_ip, count=3)
            checks["tunnel_ping"] = result.success and result.packet_loss < 50
        else:
            checks["tunnel_ping"] = False

        healthy = all(checks.values())
        return HealthResult(
            healthy=healthy,
            status=ServiceStatus.RUNNING if healthy else ServiceStatus.DEGRADED,
            checks=checks,
            message="Phormal is healthy" if healthy else "Phormal has issues",
        )

    def get_config(self) -> Dict[str, Any]:
        return {
            "mode": self.config.get("mode", "relay"),
            "peer_ip": self.config.get("peer_ip", ""),
            "port": self.config.get("port", 8531),
        }

    def apply_config(self, config: Dict[str, Any]) -> Result:
        self.config.update(config)
        result = self._write_systemd_service()
        if not result.success:
            return result
        return self.restart()

    def get_logs(self, lines: int = 100) -> List[str]:
        code, out, _ = run_command(f"journalctl -u {SYSTEMD_SERVICE} -n {lines} --no-pager")
        return out.splitlines() if code == 0 else []

    def supports_benchmark(self) -> bool:
        return True

    def benchmark(self) -> BenchmarkResult:
        """Run latency, packet loss, and throughput tests via the tunnel."""
        peer_ip = self.config.get("peer_ip", "")
        if not peer_ip:
            return BenchmarkResult(success=False, error="No peer IP configured")

        ping_result = ping(peer_ip, count=10)
        if not ping_result.success:
            return BenchmarkResult(success=False, error="Tunnel peer unreachable")

        loss = measure_packet_loss(peer_ip, cycles=20)
        throughput = measure_throughput(peer_ip)

        result = BenchmarkResult(
            success=True,
            latency_ms=round(ping_result.avg_ms, 2),
            packet_loss_percent=round(loss, 2),
            throughput_mbps=throughput.download_mbps if throughput.success else None,
        )
        result.calculate_score()
        return result

    def update(self) -> Result:
        """Re-run Phormal install script to get latest version."""
        was_running = self.is_running()
        if was_running:
            self.stop()
        code, _, err = run_command(f"curl -fsSL {PHORMAL_SCRIPT} | bash", timeout=120)
        if was_running:
            self.start()
        if code != 0:
            return Result.fail(f"Update failed: {err}")
        return Result.ok("Phormal updated")
