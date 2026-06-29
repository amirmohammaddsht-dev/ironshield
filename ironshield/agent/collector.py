"""
IronShield - Agent Metric Collector
Path: ironshield/agent/collector.py
Purpose: Collects system and service metrics from the Foreign server.
         Data is served to the Iran server via Agent REST API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psutil

from ironshield.utils.logger import get_logger
from ironshield.utils.system import run_command, service_is_active

logger = get_logger("agent.collector")


@dataclass
class SystemSnapshot:
    """Point-in-time snapshot of server resources."""

    server: str = "foreign"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # CPU
    cpu_percent: float = 0.0
    cpu_load_1m: float = 0.0
    cpu_load_5m: float = 0.0
    cpu_load_15m: float = 0.0

    # Memory
    ram_total_gb: float = 0.0
    ram_used_gb: float = 0.0
    ram_percent: float = 0.0

    # Disk
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_percent: float = 0.0

    # Network (bytes since boot)
    net_bytes_sent: int = 0
    net_bytes_recv: int = 0

    # Uptime
    uptime_hours: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON API response."""
        return {
            "server": self.server,
            "timestamp": self.timestamp,
            "cpu_percent": round(self.cpu_percent, 2),
            "cpu_load_1m": round(self.cpu_load_1m, 2),
            "cpu_load_5m": round(self.cpu_load_5m, 2),
            "cpu_load_15m": round(self.cpu_load_15m, 2),
            "ram_total_gb": round(self.ram_total_gb, 2),
            "ram_used_gb": round(self.ram_used_gb, 2),
            "ram_percent": round(self.ram_percent, 2),
            "disk_total_gb": round(self.disk_total_gb, 2),
            "disk_used_gb": round(self.disk_used_gb, 2),
            "disk_percent": round(self.disk_percent, 2),
            "net_bytes_sent": self.net_bytes_sent,
            "net_bytes_recv": self.net_bytes_recv,
            "uptime_hours": round(self.uptime_hours, 2),
        }


@dataclass
class ServiceSnapshot:
    """Status snapshot of a single service on the foreign server."""

    name: str
    display_name: str
    systemd_service: str
    is_running: bool = False
    port_open: bool = False
    version: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "is_running": self.is_running,
            "port_open": self.port_open,
            "version": self.version,
            "status": "RUNNING" if self.is_running else "STOPPED",
        }


class AgentCollector:
    """
    Collects metrics and service status from the Foreign server.

    Caches results to avoid hammering the system on every API request.
    Cache TTL: 15 seconds for system metrics, 30 seconds for service status.
    """

    SYSTEM_CACHE_TTL = 15  # seconds
    SERVICE_CACHE_TTL = 30  # seconds

    # Services expected to run on the Foreign server
    FOREIGN_SERVICES = [
        {"name": "phormal", "display": "Phormal Tunnel", "systemd": "ironshield-phormal"},
        {"name": "gost", "display": "GOST", "systemd": "ironshield-gost"},
        {"name": "frp", "display": "FRP Server", "systemd": "ironshield-frp"},
        {"name": "backhaul", "display": "Backhaul", "systemd": "ironshield-backhaul"},
        {"name": "vxlan", "display": "VXLAN", "systemd": "ironshield-vxlan"},
        {"name": "storm_dns", "display": "Storm-DNS", "systemd": "ironshield-storm-dns"},
    ]

    def __init__(self):
        self._system_cache: Optional[SystemSnapshot] = None
        self._system_cache_time: float = 0.0
        self._service_cache: Optional[List[ServiceSnapshot]] = None
        self._service_cache_time: float = 0.0

    # ── System Metrics ────────────────────────

    def get_system_metrics(self, force: bool = False) -> SystemSnapshot:
        """
        Get current system resource metrics.

        Args:
            force: Bypass cache and collect fresh data

        Returns:
            SystemSnapshot with current metrics
        """
        now = time.monotonic()
        if (
            not force
            and self._system_cache is not None
            and (now - self._system_cache_time) < self.SYSTEM_CACHE_TTL
        ):
            return self._system_cache

        snapshot = self._collect_system_metrics()
        self._system_cache = snapshot
        self._system_cache_time = now
        return snapshot

    def _collect_system_metrics(self) -> SystemSnapshot:
        """Collect fresh system metrics from the OS."""
        try:
            cpu = psutil.cpu_percent(interval=0.5)
            load = psutil.getloadavg()
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            net = psutil.net_io_counters()
            boot_time = psutil.boot_time()
            uptime = (time.time() - boot_time) / 3600

            return SystemSnapshot(
                cpu_percent=cpu,
                cpu_load_1m=load[0],
                cpu_load_5m=load[1],
                cpu_load_15m=load[2],
                ram_total_gb=ram.total / (1024**3),
                ram_used_gb=ram.used / (1024**3),
                ram_percent=ram.percent,
                disk_total_gb=disk.total / (1024**3),
                disk_used_gb=disk.used / (1024**3),
                disk_percent=disk.percent,
                net_bytes_sent=net.bytes_sent,
                net_bytes_recv=net.bytes_recv,
                uptime_hours=uptime,
            )
        except Exception as e:
            logger.error(f"Failed to collect system metrics: {e}")
            return SystemSnapshot()

    # ── Service Status ────────────────────────

    def get_service_status(self, force: bool = False) -> List[ServiceSnapshot]:
        """
        Get status of all expected Foreign server services.

        Args:
            force: Bypass cache

        Returns:
            List of ServiceSnapshot objects
        """
        now = time.monotonic()
        if (
            not force
            and self._service_cache is not None
            and (now - self._service_cache_time) < self.SERVICE_CACHE_TTL
        ):
            return self._service_cache

        snapshots = self._collect_service_status()
        self._service_cache = snapshots
        self._service_cache_time = now
        return snapshots

    def _collect_service_status(self) -> List[ServiceSnapshot]:
        """Collect fresh service status from systemd."""
        snapshots = []
        for svc_def in self.FOREIGN_SERVICES:
            is_running = service_is_active(svc_def["systemd"])
            snapshot = ServiceSnapshot(
                name=svc_def["name"],
                display_name=svc_def["display"],
                systemd_service=svc_def["systemd"],
                is_running=is_running,
            )
            snapshots.append(snapshot)
        return snapshots

    def get_service_by_name(self, name: str) -> Optional[ServiceSnapshot]:
        """Get status of a specific service by plugin name."""
        for svc in self.get_service_status():
            if svc.name == name:
                return svc
        return None

    # ── Log Collection ────────────────────────

    def get_service_logs(self, service_name: str, lines: int = 50) -> List[str]:
        """
        Fetch recent journald logs for a service.

        Args:
            service_name: Plugin name (e.g. 'gost')
            lines: Number of log lines to return

        Returns:
            List of log line strings
        """
        systemd_name = self._get_systemd_name(service_name)
        if systemd_name is None:
            return [f"Unknown service: {service_name}"]

        code, out, _ = run_command(
            f"journalctl -u {systemd_name} -n {lines} --no-pager --output=short-iso",
            timeout=10,
        )
        return out.splitlines() if code == 0 else []

    # ── Service Control ───────────────────────

    def start_service(self, service_name: str) -> Dict[str, Any]:
        """Start a service on the foreign server."""
        return self._control_service(service_name, "start")

    def stop_service(self, service_name: str) -> Dict[str, Any]:
        """Stop a service on the foreign server."""
        return self._control_service(service_name, "stop")

    def restart_service(self, service_name: str) -> Dict[str, Any]:
        """Restart a service on the foreign server."""
        return self._control_service(service_name, "restart")

    def _control_service(self, service_name: str, action: str) -> Dict[str, Any]:
        """Execute a systemctl action on a service."""
        systemd_name = self._get_systemd_name(service_name)
        if systemd_name is None:
            return {"success": False, "error": f"Unknown service: {service_name}"}

        code, _, err = run_command(f"sudo systemctl {action} {systemd_name}")
        if code == 0:
            logger.info(f"Service {action}: {service_name}")
            # Invalidate cache
            self._service_cache = None
            return {"success": True, "message": f"{service_name} {action}ed"}
        else:
            logger.warning(f"Service {action} failed for {service_name}: {err}")
            return {"success": False, "error": err}

    # ── Helpers ──────────────────────────────

    def _get_systemd_name(self, plugin_name: str) -> Optional[str]:
        """Get systemd service name for a plugin."""
        for svc in self.FOREIGN_SERVICES:
            if svc["name"] == plugin_name:
                return svc["systemd"]
        return None

    def get_full_snapshot(self) -> Dict[str, Any]:
        """
        Get a complete snapshot for the Agent API health endpoint.
        Includes system metrics + all service statuses.
        """
        system = self.get_system_metrics()
        services = self.get_service_status()

        return {
            "agent_version": "1.0.0",
            "system": system.to_dict(),
            "services": [s.to_dict() for s in services],
        }
