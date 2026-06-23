"""
IronShield Plugin — GOST
Path: plugins/tunnels/gost/service.py
Purpose: GO Simple Tunnel — TCP forwarding between Iran and Foreign servers.
"""

from __future__ import annotations

import json
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
from ironshield.utils.system import run_command, service_is_active, systemctl, port_is_open

logger = get_service_logger("gost")

GOST_BIN = Path("/usr/local/bin/gost")
GOST_CONFIG = Path("/opt/ironshield/configs/tunnels/gost.json")
SYSTEMD_SERVICE = "ironshield-gost"
GITHUB_REPO = "ginuerzh/gost"


class GOSTService(BaseService):
    """GOST tunnel plugin — reliable TCP forwarding."""

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="gost",
            display_name="GOST",
            version="3.0.0",
            author="ginuerzh",
            source_url="https://github.com/ginuerzh/gost",
            license="MIT",
            roles=[ServerRole.IRAN, ServerRole.FOREIGN],
            category=PluginCategory.TUNNEL_RELIABLE,
            priority=3,
            description="GO Simple Tunnel — reliable TCP forwarding",
            ufw_ports=[{"port": 8080, "protocol": "tcp", "description": "GOST tunnel"}],
            auto_update_enabled=True,
            auto_update_source="github_release",
            auto_update_repo=GITHUB_REPO,
        )

    def install(self) -> Result:
        """Download GOST binary from GitHub and set up service."""
        logger.info("Installing GOST...")

        result = self._download_binary()
        if not result.success:
            return result

        result = self._write_config()
        if not result.success:
            return result

        result = self._write_systemd_service()
        if not result.success:
            return result

        systemctl("enable", SYSTEMD_SERVICE)
        return Result.ok("GOST installed")

    def _download_binary(self) -> Result:
        """Download latest GOST binary from GitHub releases."""
        if GOST_BIN.exists():
            logger.info("GOST binary already exists, skipping download")
            return Result.ok()

        arch_cmd = "uname -m"
        _, arch, _ = run_command(arch_cmd)
        arch = "amd64" if "x86_64" in arch else "arm64"

        # Get latest release URL
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        code, out, err = run_command(f"curl -fsSL {api_url}", timeout=30)
        if code != 0:
            return Result.fail(f"Failed to fetch release info: {err}")

        try:
            data = json.loads(out)
            assets = data.get("assets", [])
            download_url = None
            for asset in assets:
                name = asset.get("name", "").lower()
                if "linux" in name and arch in name and name.endswith(".tar.gz"):
                    download_url = asset.get("browser_download_url")
                    break

            if not download_url:
                return Result.fail("Could not find suitable GOST binary for this platform")
        except (json.JSONDecodeError, KeyError) as e:
            return Result.fail(f"Failed to parse release info: {e}")

        # Download and extract
        tmp_dir = Path("/tmp/gost_install")
        tmp_dir.mkdir(exist_ok=True)

        code, _, err = run_command(
            f"curl -fsSL {download_url} -o /tmp/gost.tar.gz && "
            f"tar -xzf /tmp/gost.tar.gz -C {tmp_dir}",
            timeout=60,
        )
        if code != 0:
            return Result.fail(f"Download failed: {err}")

        # Find and install binary
        code, gost_path, _ = run_command(f"find {tmp_dir} -name 'gost' -type f")
        if code != 0 or not gost_path.strip():
            return Result.fail("GOST binary not found in archive")

        run_command(f"cp {gost_path.strip()} {GOST_BIN}")
        run_command(f"chmod +x {GOST_BIN}")
        run_command("rm -rf /tmp/gost.tar.gz /tmp/gost_install")

        return Result.ok()

    def _write_config(self) -> Result:
        """Write GOST JSON configuration."""
        local_port = self.config.get("local_port", 8080)
        remote_host = self.config.get("remote_host", "")
        remote_port = self.config.get("remote_port", 8080)

        if self.server_role == ServerRole.IRAN:
            # Iran: forward local traffic to foreign server
            config = {
                "services": [
                    {
                        "name": "ironshield-tunnel",
                        "addr": f":{local_port}",
                        "handler": {"type": "tcp"},
                        "listener": {"type": "tcp"},
                        "forwarder": {"nodes": [{"addr": f"{remote_host}:{remote_port}"}]},
                    }
                ]
            }
        else:
            # Foreign: listen and accept
            config = {
                "services": [
                    {
                        "name": "ironshield-receiver",
                        "addr": f":{local_port}",
                        "handler": {"type": "tcp"},
                        "listener": {"type": "tcp"},
                    }
                ]
            }

        try:
            GOST_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            GOST_CONFIG.write_text(json.dumps(config, indent=2))
            return Result.ok()
        except Exception as e:
            return Result.fail(f"Failed to write GOST config: {e}")

    def _write_systemd_service(self) -> Result:
        """Write systemd unit file for GOST."""
        unit = f"""[Unit]
Description=IronShield GOST Tunnel
After=network.target

[Service]
Type=simple
User=ironshield
ExecStart={GOST_BIN} -C {GOST_CONFIG}
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
        for path in [
            Path(f"/etc/systemd/system/{SYSTEMD_SERVICE}.service"),
            GOST_BIN,
            GOST_CONFIG,
        ]:
            if path.exists():
                path.unlink()
        run_command("systemctl daemon-reload")
        return Result.ok("GOST uninstalled")

    def start(self) -> Result:
        ok = systemctl("start", SYSTEMD_SERVICE)
        return Result.ok("GOST started") if ok else Result.fail("Failed to start GOST")

    def stop(self) -> Result:
        systemctl("stop", SYSTEMD_SERVICE)
        return Result.ok("GOST stopped")

    def status(self) -> ServiceStatus:
        if not GOST_BIN.exists():
            return ServiceStatus.NOT_INSTALLED
        return (
            ServiceStatus.RUNNING if service_is_active(SYSTEMD_SERVICE) else ServiceStatus.STOPPED
        )

    def health_check(self) -> HealthResult:
        local_port = self.config.get("local_port", 8080)
        checks = {
            "process": service_is_active(SYSTEMD_SERVICE),
            "port": port_is_open("127.0.0.1", local_port),
        }
        healthy = all(checks.values())
        return HealthResult(
            healthy=healthy,
            status=ServiceStatus.RUNNING if healthy else ServiceStatus.DEGRADED,
            checks=checks,
            message="GOST is healthy" if healthy else "GOST has issues",
        )

    def get_config(self) -> Dict[str, Any]:
        return {
            "local_port": self.config.get("local_port", 8080),
            "remote_host": self.config.get("remote_host", ""),
            "remote_port": self.config.get("remote_port", 8080),
        }

    def apply_config(self, config: Dict[str, Any]) -> Result:
        self.config.update(config)
        result = self._write_config()
        if not result.success:
            return result
        return self.restart()

    def get_logs(self, lines: int = 100) -> List[str]:
        code, out, _ = run_command(f"journalctl -u {SYSTEMD_SERVICE} -n {lines} --no-pager")
        return out.splitlines() if code == 0 else []

    def supports_benchmark(self) -> bool:
        return True

    def benchmark(self) -> BenchmarkResult:
        """Benchmark via the GOST tunnel."""
        remote_host = self.config.get("remote_host", "")
        if not remote_host:
            return BenchmarkResult(success=False, error="No remote host configured")

        ping_result = ping(remote_host, count=10)
        if not ping_result.success:
            return BenchmarkResult(success=False, error="Remote host unreachable")

        loss = measure_packet_loss(remote_host, cycles=20)
        throughput = measure_throughput(remote_host)

        result = BenchmarkResult(
            success=True,
            latency_ms=round(ping_result.avg_ms, 2),
            packet_loss_percent=round(loss, 2),
            throughput_mbps=throughput.download_mbps if throughput.success else None,
        )
        result.calculate_score()
        return result
