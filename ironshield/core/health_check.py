"""
IronShield - Health Check Engine
Path: ironshield/core/health_check.py
Purpose: Continuously monitors all services, tunnels, and system resources.
         Reports failures to Failover Engine.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional


from ironshield.core.plugin_manager import PluginManager
from ironshield.db.database import Database
from ironshield.db.models import SystemMetric
from ironshield.services.base import HealthResult, ServiceStatus
from ironshield.utils.logger import get_logger
from ironshield.utils.system import get_system_info

logger = get_logger("health_check")


@dataclass
class AlertThresholds:
    """System resource alert thresholds."""

    cpu_warning: float = 80.0
    cpu_critical: float = 95.0
    ram_warning: float = 85.0
    ram_critical: float = 95.0
    disk_warning: float = 85.0
    disk_critical: float = 95.0
    failure_threshold: int = 3  # Consecutive failures before FAILED
    recovery_threshold: int = 2  # Consecutive successes to recover


@dataclass
class ServiceHealthState:
    """Tracks consecutive failure/recovery counts for a service."""

    name: str
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_status: Optional[ServiceStatus] = None
    last_checked: Optional[float] = None
    alerted: bool = False


class HealthCheckEngine:
    """
    Monitors all IronShield services and system resources.

    Features:
    - Parallel health checks on all plugins
    - System resource monitoring (CPU/RAM/Disk)
    - Consecutive failure/recovery tracking (prevents false alerts)
    - Pluggable callbacks for failure and recovery events
    - Stores metrics in DB time series
    """

    def __init__(
        self,
        plugin_manager: PluginManager,
        db: Database,
        server_label: str = "iran",
        thresholds: Optional[AlertThresholds] = None,
        on_service_failure: Optional[Callable] = None,
        on_service_recovery: Optional[Callable] = None,
        on_system_alert: Optional[Callable] = None,
    ):
        self.pm = plugin_manager
        self.db = db
        self.server_label = server_label
        self.thresholds = thresholds or AlertThresholds()
        self.on_service_failure = on_service_failure
        self.on_service_recovery = on_service_recovery
        self.on_system_alert = on_system_alert

        self._running = False
        self._service_states: Dict[str, ServiceHealthState] = {}
        self._check_interval = 30  # seconds
        self._metric_interval = 60  # seconds
        self._last_metric_time = 0.0

    # ── Main Loop ────────────────────────────

    async def start(self, check_interval: int = 30) -> None:
        """
        Start the async health monitoring loop.

        Args:
            check_interval: Seconds between health checks
        """
        self._running = True
        self._check_interval = check_interval
        logger.info(
            f"Health check engine started (interval: {check_interval}s, "
            f"server: {self.server_label})"
        )

        while self._running:
            try:
                await asyncio.gather(
                    self._check_all_services(),
                    self._check_system_resources(),
                    return_exceptions=True,
                )
            except Exception as e:
                logger.error(f"Health check loop error: {e}")

            await asyncio.sleep(self._check_interval)

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        logger.info("Health check engine stopped")

    # ── Service Checks ────────────────────────

    async def _check_all_services(self) -> None:
        """Run health checks on all loaded plugins in parallel."""
        plugins = self.pm.all()
        tasks = [self._check_service(p.meta.name) for p in plugins]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_service(self, plugin_name: str) -> None:
        """Check a single plugin and update its health state."""
        plugin = self.pm.get(plugin_name)
        if plugin is None:
            return

        # Initialize state if new
        if plugin_name not in self._service_states:
            self._service_states[plugin_name] = ServiceHealthState(name=plugin_name)

        state = self._service_states[plugin_name]

        try:
            loop = asyncio.get_event_loop()
            health: HealthResult = await loop.run_in_executor(None, plugin.health_check)
            state.last_checked = time.monotonic()

            if health.healthy:
                self._handle_service_healthy(state, health)
            else:
                self._handle_service_unhealthy(state, health, plugin_name)

        except Exception as e:
            logger.warning(f"Health check exception for {plugin_name}: {e}")
            state.consecutive_failures += 1

    def _handle_service_healthy(self, state: ServiceHealthState, health: HealthResult) -> None:
        """Process a healthy service check result."""
        state.consecutive_failures = 0
        state.consecutive_successes += 1

        # Check if recovering from failure
        if state.alerted and state.consecutive_successes >= self.thresholds.recovery_threshold:
            state.alerted = False
            state.last_status = ServiceStatus.RUNNING
            logger.info(f"Service recovered: {state.name}")

            if self.on_service_recovery:
                try:
                    self.on_service_recovery(service_name=state.name, health=health)
                except Exception as e:
                    logger.warning(f"Recovery callback error: {e}")

    def _handle_service_unhealthy(
        self, state: ServiceHealthState, health: HealthResult, plugin_name: str
    ) -> None:
        """Process an unhealthy service check result."""
        state.consecutive_failures += 1
        state.consecutive_successes = 0
        state.last_status = health.status

        logger.warning(
            f"Service unhealthy: {plugin_name} "
            f"(consecutive: {state.consecutive_failures}/{self.thresholds.failure_threshold})"
        )

        # Only alert after consecutive_failures threshold
        if state.consecutive_failures >= self.thresholds.failure_threshold and not state.alerted:
            state.alerted = True
            logger.error(f"Service FAILED: {plugin_name}")

            if self.on_service_failure:
                try:
                    self.on_service_failure(
                        service_name=plugin_name,
                        health=health,
                        consecutive_failures=state.consecutive_failures,
                    )
                except Exception as e:
                    logger.warning(f"Failure callback error: {e}")

    # ── System Resource Checks ────────────────

    async def _check_system_resources(self) -> None:
        """Check CPU, RAM, Disk and store metrics."""
        now = time.monotonic()

        # Collect metrics every minute
        if now - self._last_metric_time < self._metric_interval:
            return

        self._last_metric_time = now

        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, get_system_info)

            # Store in DB
            self._store_system_metric(info)

            # Check thresholds and alert
            self._check_cpu_threshold(info.cpu_percent)
            self._check_ram_threshold(info.ram_percent)
            self._check_disk_threshold(info.disk_percent)

        except Exception as e:
            logger.error(f"System resource check error: {e}")

    def _store_system_metric(self, info) -> None:
        """Store system metrics in DB time series."""
        try:
            with self.db.session() as s:
                s.add(
                    SystemMetric(
                        server=self.server_label,
                        resolution="realtime",
                        cpu_percent=info.cpu_percent,
                        cpu_load_1m=info.cpu_load_1m,
                        cpu_load_5m=info.cpu_load_5m,
                        cpu_load_15m=info.cpu_load_15m,
                        ram_total_gb=info.ram_total_gb,
                        ram_used_gb=info.ram_used_gb,
                        ram_percent=info.ram_percent,
                        disk_total_gb=info.disk_total_gb,
                        disk_used_gb=info.disk_used_gb,
                        disk_percent=info.disk_percent,
                        net_bytes_sent=info.net_bytes_sent,
                        net_bytes_recv=info.net_bytes_recv,
                    )
                )
        except Exception as e:
            logger.warning(f"Failed to store system metric: {e}")

    def _check_cpu_threshold(self, cpu_percent: float) -> None:
        """Alert if CPU exceeds thresholds."""
        if cpu_percent >= self.thresholds.cpu_critical:
            self._send_system_alert("CPU", cpu_percent, "CRITICAL")
        elif cpu_percent >= self.thresholds.cpu_warning:
            self._send_system_alert("CPU", cpu_percent, "WARNING")

    def _check_ram_threshold(self, ram_percent: float) -> None:
        """Alert if RAM exceeds thresholds."""
        if ram_percent >= self.thresholds.ram_critical:
            self._send_system_alert("RAM", ram_percent, "CRITICAL")
        elif ram_percent >= self.thresholds.ram_warning:
            self._send_system_alert("RAM", ram_percent, "WARNING")

    def _check_disk_threshold(self, disk_percent: float) -> None:
        """Alert if Disk exceeds thresholds."""
        if disk_percent >= self.thresholds.disk_critical:
            self._send_system_alert("Disk", disk_percent, "CRITICAL")
        elif disk_percent >= self.thresholds.disk_warning:
            self._send_system_alert("Disk", disk_percent, "WARNING")

    def _send_system_alert(self, resource: str, value: float, level: str) -> None:
        """Send a system resource alert."""
        logger.warning(f"System {level}: {resource} at {value:.1f}%")
        if self.on_system_alert:
            try:
                self.on_system_alert(resource=resource, value=value, level=level)
            except Exception as e:
                logger.warning(f"System alert callback error: {e}")

    # ── Status Query ─────────────────────────

    def get_all_health(self) -> Dict[str, Dict]:
        """Return health status summary for all services."""
        summary = {}
        for name, state in self._service_states.items():
            summary[name] = {
                "healthy": not state.alerted,
                "consecutive_failures": state.consecutive_failures,
                "consecutive_successes": state.consecutive_successes,
                "last_status": state.last_status.value if state.last_status else "UNKNOWN",
                "alerted": state.alerted,
            }
        return summary

    def get_latest_system_metrics(self) -> Optional[Dict]:
        """Return the most recent system metrics from DB."""
        try:
            with self.db.session() as s:
                metric = (
                    s.query(SystemMetric)
                    .filter_by(server=self.server_label)
                    .order_by(SystemMetric.recorded_at.desc())
                    .first()
                )
                if metric:
                    return {
                        "server": metric.server,
                        "cpu_percent": metric.cpu_percent,
                        "ram_percent": metric.ram_percent,
                        "disk_percent": metric.disk_percent,
                        "net_bytes_sent": metric.net_bytes_sent,
                        "net_bytes_recv": metric.net_bytes_recv,
                        "recorded_at": metric.recorded_at.isoformat(),
                    }
        except Exception:
            pass
        return None
