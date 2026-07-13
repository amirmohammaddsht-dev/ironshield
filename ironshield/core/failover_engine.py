"""
IronShield - Failover Engine
Path: ironshield/core/failover_engine.py
Purpose: Receives failure signals from Health Check Engine and takes action.
         Auto-restarts services, switches tunnels, activates emergency mode.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Dict, List, Optional

from ironshield.core.plugin_manager import PluginManager
from ironshield.core.smart_routing import SmartRoutingEngine
from ironshield.db.database import Database
from ironshield.db.models import FailoverEvent
from ironshield.services.base import HealthResult
from ironshield.utils.logger import get_logger

logger = get_logger("failover_engine")

# Recovery check intervals in seconds (exponential backoff)
RECOVERY_INTERVALS = [60, 120, 300, 600, 1800, 3600]


class FailoverAction(str, Enum):
    RESTART = "restart"
    SWITCH_TUNNEL = "switch_tunnel"
    ACTIVATE_EMERGENCY = "activate_emergency"
    CLEAN_DISK = "clean_disk"
    NOTIFY_ONLY = "notify_only"


class FailoverEngine:
    """
    Handles service and tunnel failures automatically.

    When a failure is reported:
    1. Classify failure type and severity
    2. Execute appropriate action (restart/switch/emergency)
    3. Start recovery monitoring loop
    4. Notify admin via callback
    5. Record event in DB audit log
    """

    def __init__(
        self,
        plugin_manager: PluginManager,
        routing_engine: SmartRoutingEngine,
        db: Database,
        max_restart_attempts: int = 3,
        on_alert: Optional[Callable] = None,
        on_recovery: Optional[Callable] = None,
    ):
        self.pm = plugin_manager
        self.routing = routing_engine
        self.db = db
        self.max_restart_attempts = max_restart_attempts
        self.on_alert = on_alert
        self.on_recovery = on_recovery

        self._restart_counts: Dict[str, int] = {}
        self._recovery_tasks: Dict[str, asyncio.Task] = {}
        self._active_events: Dict[str, int] = {}  # plugin_name → event_id

    # ── Entry Points ──────────────────────────

    async def handle_service_failure(
        self,
        service_name: str,
        health: HealthResult,
        consecutive_failures: int = 1,
    ) -> None:
        """
        Called by HealthCheckEngine when a service has consecutive failures.

        Args:
            service_name: Name of the failed service
            health: Latest health check result
            consecutive_failures: How many times in a row it failed
        """
        logger.error(
            f"Service failure: {service_name} "
            f"(status={health.status.value}, consecutive={consecutive_failures})"
        )

        # Record event
        event_id = self._record_event(
            event_type="service_failed",
            severity="CRITICAL",
            plugin_name=service_name,
            error_message=health.error or health.message,
        )
        self._active_events[service_name] = event_id

        # Attempt restart
        await self._attempt_restart(service_name, event_id)

    async def handle_tunnel_failure(self, tunnel_name: str) -> None:
        """
        Called when a tunnel plugin fails.
        Triggers Smart Routing Engine to switch to backup.

        Args:
            tunnel_name: Name of the failed tunnel
        """
        logger.error(f"Tunnel failure: {tunnel_name}")

        event_id = self._record_event(
            event_type="tunnel_failed",
            severity="CRITICAL",
            plugin_name=tunnel_name,
            action_taken="switching_to_backup",
        )
        self._active_events[tunnel_name] = event_id

        # Notify routing engine
        self.routing.report_tunnel_failure(tunnel_name)

        # Start recovery monitoring
        await self._start_recovery_monitor(tunnel_name, event_id)

        # Notify admin
        self._notify(
            title=f"🔴 Tunnel Failed: {tunnel_name}",
            body="Switching to backup tunnel automatically.",
            severity="CRITICAL",
        )

    async def handle_all_tunnels_failed(self) -> None:
        """Called when every non-emergency tunnel has failed."""
        logger.critical("ALL tunnels failed — activating Storm-DNS emergency")

        self._record_event(
            event_type="all_tunnels_failed",
            severity="EMERGENCY",
            action_taken="storm_dns_activated",
        )

        self._notify(
            title="🆘 EMERGENCY: All Tunnels Down",
            body="Storm-DNS emergency mode activated. Connectivity degraded.",
            severity="EMERGENCY",
        )

    async def handle_system_alert(self, resource: str, value: float, level: str) -> None:
        """
        Called when CPU/RAM/Disk exceeds thresholds.

        Args:
            resource: 'CPU', 'RAM', or 'Disk'
            value: Current percentage
            level: 'WARNING' or 'CRITICAL'
        """
        if level == "CRITICAL" and resource == "Disk":
            logger.warning("Disk critical — auto-cleaning old logs")
            await self._clean_old_logs()

        severity = "CRITICAL" if level == "CRITICAL" else "WARNING"
        self._record_event(
            event_type="system_critical",
            severity=severity,
            error_message=f"{resource} at {value:.1f}%",
        )

        self._notify(
            title=f"⚠️ System {level}: {resource}",
            body=f"{resource} usage: {value:.1f}%",
            severity=severity,
        )

    # ── Restart Logic ─────────────────────────

    async def _attempt_restart(self, service_name: str, event_id: int) -> None:
        """Try to restart a failed service up to max_restart_attempts times."""
        plugin = self.pm.get(service_name)
        if plugin is None:
            logger.warning(f"Cannot restart unknown plugin: {service_name}")
            return

        count = self._restart_counts.get(service_name, 0)

        if count >= self.max_restart_attempts:
            logger.error(
                f"Max restart attempts ({self.max_restart_attempts}) " f"reached for {service_name}"
            )
            self._notify(
                title=f"🔴 Service Failed: {service_name}",
                body=f"Failed to restart after {count} attempts. Manual intervention required.",
                severity="CRITICAL",
            )
            await self._start_recovery_monitor(service_name, event_id)
            return

        self._restart_counts[service_name] = count + 1
        logger.info(
            f"Restarting {service_name} " f"(attempt {count + 1}/{self.max_restart_attempts})..."
        )

        await asyncio.sleep(5)  # Brief wait before restart

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, plugin.restart)

        if result.success:
            logger.info(f"Service restarted successfully: {service_name}")
            self._restart_counts[service_name] = 0
            self._resolve_event(event_id, service_name)
            self._notify(
                title=f"✅ Service Recovered: {service_name}",
                body=f"Restarted successfully after {count + 1} attempt(s).",
                severity="INFO",
            )
        else:
            logger.warning(f"Restart failed for {service_name}: {result.error}")
            await asyncio.sleep(10)
            await self._attempt_restart(service_name, event_id)

    # ── Recovery Monitor ──────────────────────

    async def _start_recovery_monitor(self, plugin_name: str, event_id: int) -> None:
        """
        Start a background task that periodically checks if service recovered.
        Uses exponential backoff intervals.
        """
        # Cancel existing monitor if any
        if plugin_name in self._recovery_tasks:
            task = self._recovery_tasks[plugin_name]
            if not task.done():
                task.cancel()

        task = asyncio.create_task(self._recovery_loop(plugin_name, event_id))
        self._recovery_tasks[plugin_name] = task

    async def _recovery_loop(self, plugin_name: str, event_id: int) -> None:
        """Periodically check if a failed plugin has recovered."""
        plugin = self.pm.get(plugin_name)
        if plugin is None:
            return

        for interval in RECOVERY_INTERVALS:
            await asyncio.sleep(interval)

            try:
                loop = asyncio.get_event_loop()
                health = await loop.run_in_executor(None, plugin.health_check)

                if health.healthy:
                    logger.info(f"Recovery confirmed: {plugin_name}")
                    self._resolve_event(event_id, plugin_name)

                    if self.on_recovery:
                        self.on_recovery(
                            plugin_name=plugin_name,
                            downtime_seconds=self._get_downtime(event_id),
                        )
                    return

                logger.debug(f"Recovery check: {plugin_name} still unhealthy")

            except Exception as e:
                logger.warning(f"Recovery check error for {plugin_name}: {e}")

        logger.warning(f"Recovery monitor exhausted for {plugin_name} — giving up")

    # ── Disk Cleanup ─────────────────────────

    async def _clean_old_logs(self) -> None:
        """Remove log files older than 30 days to free disk space."""
        from ironshield.utils.system import run_command

        code, out, _ = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: run_command("find /opt/ironshield/logs -name '*.log' -mtime +30 -delete"),
        )
        if code == 0:
            logger.info("Old log files cleaned successfully")
        else:
            logger.warning("Log cleanup had issues")

    # ── DB Recording ─────────────────────────

    def _record_event(
        self,
        event_type: str,
        severity: str,
        plugin_name: Optional[str] = None,
        action_taken: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> int:
        """Record a failover event in DB and return its ID."""
        try:
            with self.db.session() as s:
                event = FailoverEvent(
                    event_type=event_type,
                    severity=severity,
                    plugin_name=plugin_name,
                    server=self._guess_server(plugin_name),
                    action_taken=action_taken,
                    error_message=error_message,
                    admin_notified=False,
                )
                s.add(event)
                s.flush()
                return event.id
        except Exception as e:
            logger.warning(f"Failed to record failover event: {e}")
            return -1

    def _resolve_event(self, event_id: int, plugin_name: str) -> None:
        """Mark a failover event as resolved."""
        if event_id < 0:
            return
        try:
            with self.db.session() as s:
                event = s.get(FailoverEvent, event_id)
                if event:
                    event.resolved_at = datetime.now(timezone.utc)
            # Clean up active event tracking
            self._active_events.pop(plugin_name, None)
        except Exception as e:
            logger.warning(f"Failed to resolve event {event_id}: {e}")

    def _get_downtime(self, event_id: int) -> int:
        """Calculate downtime in seconds for an event."""
        try:
            with self.db.session() as s:
                event = s.get(FailoverEvent, event_id)
                if event and event.downtime_seconds is not None:
                    return event.downtime_seconds
        except Exception:
            pass
        return 0

    @staticmethod
    def _guess_server(plugin_name: Optional[str]) -> str:
        """Guess which server a plugin runs on based on category."""
        vpn_plugins = {"openvpn"}
        if plugin_name in vpn_plugins:
            return "iran"
        return "both"

    # ── Notification ──────────────────────────

    def _notify(self, title: str, body: str, severity: str) -> None:
        """Send notification via registered callback."""
        if self.on_alert:
            try:
                self.on_alert(title=title, body=body, severity=severity)
            except Exception as e:
                logger.warning(f"Alert notification failed: {e}")

    # ── Status ────────────────────────────────

    def get_active_events(self) -> List[Dict]:
        """Return list of currently active (unresolved) failover events."""
        try:
            with self.db.session() as s:
                events = (
                    s.query(FailoverEvent)
                    .filter(FailoverEvent.resolved_at.is_(None))
                    .order_by(FailoverEvent.occurred_at.desc())
                    .limit(20)
                    .all()
                )
                return [
                    {
                        "id": e.id,
                        "type": e.event_type,
                        "severity": e.severity,
                        "plugin": e.plugin_name,
                        "action": e.action_taken,
                        "occurred_at": e.occurred_at.isoformat(),
                    }
                    for e in events
                ]
        except Exception:
            return []

    def get_event_history(self, limit: int = 20) -> List[Dict]:
        """Return recent failover event history."""
        try:
            with self.db.session() as s:
                events = (
                    s.query(FailoverEvent)
                    .order_by(FailoverEvent.occurred_at.desc())
                    .limit(limit)
                    .all()
                )
                return [
                    {
                        "id": e.id,
                        "type": e.event_type,
                        "severity": e.severity,
                        "plugin": e.plugin_name,
                        "downtime_seconds": e.downtime_seconds,
                        "resolved": e.resolved_at is not None,
                        "occurred_at": e.occurred_at.isoformat(),
                    }
                    for e in events
                ]
        except Exception:
            return []
