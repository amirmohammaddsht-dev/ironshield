"""
IronShield - Monitoring Engine
Path: ironshield/core/monitoring.py
Purpose: Collects system and service metrics from both Iran and Foreign servers.
         Stores time-series data and generates reports for the Telegram bot.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from ironshield.core.plugin_manager import PluginManager
from ironshield.db.database import Database
from ironshield.db.models import SystemMetric, TrafficLog, User
from ironshield.utils.logger import get_logger
from ironshield.utils.system import get_system_info

logger = get_logger("monitoring")


class MonitoringEngine:
    """
    Collects and stores metrics from both Iran and Foreign servers.

    Responsibilities:
    - Collect system metrics every 60 seconds
    - Aggregate metrics into hourly/daily summaries
    - Parse OpenVPN status log for user traffic
    - Generate dashboard and report data for Telegram bot
    - Purge old realtime metrics to save disk space
    """

    def __init__(
        self,
        plugin_manager: PluginManager,
        db: Database,
        server_label: str = "iran",
        collect_interval: int = 60,
    ):
        self.pm = plugin_manager
        self.db = db
        self.server_label = server_label
        self.collect_interval = collect_interval
        self._running = False
        self._last_aggregate = 0.0
        self._aggregate_interval = 3600  # 1 hour

    # ── Main Loop ─────────────────────────────

    async def start(self) -> None:
        """Start the async monitoring collection loop."""
        self._running = True
        logger.info(
            f"Monitoring engine started "
            f"(server={self.server_label}, interval={self.collect_interval}s)"
        )

        while self._running:
            try:
                await self._collect_cycle()
            except Exception as e:
                logger.error(f"Monitoring collection error: {e}")
            await asyncio.sleep(self.collect_interval)

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        logger.info("Monitoring engine stopped")

    async def _collect_cycle(self) -> None:
        """One full collection cycle: metrics + optional aggregation."""
        await self._collect_system_metrics()
        await self._collect_plugin_metrics()
        await self._sync_user_traffic()

        # Hourly aggregation
        now = time.monotonic()
        if now - self._last_aggregate >= self._aggregate_interval:
            await self._aggregate_hourly()
            await self._purge_old_realtime()
            self._last_aggregate = now

    # ── System Metrics ────────────────────────

    async def _collect_system_metrics(self) -> None:
        """Collect CPU/RAM/Disk/Network metrics from the local server."""
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, get_system_info)

        try:
            with self.db.session() as s:
                s.add(
                    SystemMetric(
                        server=self.server_label,
                        resolution="realtime",
                        cpu_percent=round(info.cpu_percent, 2),
                        cpu_load_1m=round(info.cpu_load_1m, 2),
                        cpu_load_5m=round(info.cpu_load_5m, 2),
                        cpu_load_15m=round(info.cpu_load_15m, 2),
                        ram_total_gb=round(info.ram_total_gb, 2),
                        ram_used_gb=round(info.ram_used_gb, 2),
                        ram_percent=round(info.ram_percent, 2),
                        disk_total_gb=round(info.disk_total_gb, 2),
                        disk_used_gb=round(info.disk_used_gb, 2),
                        disk_percent=round(info.disk_percent, 2),
                        net_bytes_sent=info.net_bytes_sent,
                        net_bytes_recv=info.net_bytes_recv,
                    )
                )
        except Exception as e:
            logger.warning(f"Failed to store system metric: {e}")

    # ── Plugin Metrics ────────────────────────

    async def _collect_plugin_metrics(self) -> None:
        """Collect metrics from each loaded plugin."""
        loop = asyncio.get_event_loop()
        for plugin in self.pm.all():
            try:
                metrics = await loop.run_in_executor(None, plugin.get_metrics)
                logger.debug(f"Plugin metrics [{plugin.meta.name}]: {metrics}")
            except Exception as e:
                logger.debug(f"Plugin metric collection failed [{plugin.meta.name}]: {e}")

    # ── User Traffic Sync ─────────────────────

    async def _sync_user_traffic(self) -> None:
        """
        Parse OpenVPN status log and sync traffic to User records.
        Updates both traffic_used_bytes and creates TrafficLog entries.
        """
        openvpn = self.pm.get("openvpn")
        if openvpn is None or not openvpn.is_running():
            return

        try:
            loop = asyncio.get_event_loop()
            connections = await loop.run_in_executor(None, openvpn.get_active_connections)

            with self.db.session() as s:
                for conn in connections:
                    username = conn.get("username", "")
                    if not username:
                        continue

                    user = s.query(User).filter_by(username=username).first()
                    if user is None:
                        continue

                    bytes_recv = conn.get("bytes_recv", 0)
                    bytes_sent = conn.get("bytes_sent", 0)
                    total = bytes_recv + bytes_sent

                    # Update cumulative traffic
                    user.traffic_used_bytes = max(user.traffic_used_bytes, total)
                    user.last_connected_at = datetime.now(timezone.utc)

                    # Log entry
                    s.add(
                        TrafficLog(
                            user_id=user.id,
                            bytes_sent=bytes_sent,
                            bytes_received=bytes_recv,
                            client_ip=conn.get("real_ip", ""),
                        )
                    )

        except Exception as e:
            logger.warning(f"User traffic sync failed: {e}")

    # ── Aggregation ───────────────────────────

    async def _aggregate_hourly(self) -> None:
        """
        Aggregate last hour's realtime metrics into a single hourly record.
        This keeps the DB compact over time.
        """
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)

        try:
            with self.db.session() as s:
                metrics = (
                    s.query(SystemMetric)
                    .filter(
                        SystemMetric.server == self.server_label,
                        SystemMetric.resolution == "realtime",
                        SystemMetric.recorded_at >= one_hour_ago,
                    )
                    .all()
                )

                if not metrics:
                    return

                def avg(values):
                    vals = [v for v in values if v is not None]
                    return round(sum(vals) / len(vals), 2) if vals else None

                s.add(
                    SystemMetric(
                        server=self.server_label,
                        resolution="hourly",
                        cpu_percent=avg([m.cpu_percent for m in metrics]),
                        cpu_load_1m=avg([m.cpu_load_1m for m in metrics]),
                        cpu_load_5m=avg([m.cpu_load_5m for m in metrics]),
                        cpu_load_15m=avg([m.cpu_load_15m for m in metrics]),
                        ram_percent=avg([m.ram_percent for m in metrics]),
                        ram_used_gb=avg([m.ram_used_gb for m in metrics]),
                        ram_total_gb=metrics[0].ram_total_gb,
                        disk_percent=avg([m.disk_percent for m in metrics]),
                        disk_used_gb=avg([m.disk_used_gb for m in metrics]),
                        disk_total_gb=metrics[0].disk_total_gb,
                    )
                )

            logger.debug("Hourly metric aggregation complete")

        except Exception as e:
            logger.warning(f"Hourly aggregation failed: {e}")

    async def _purge_old_realtime(self) -> None:
        """Remove realtime metrics older than 24 hours to save space."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        try:
            with self.db.session() as s:
                deleted = (
                    s.query(SystemMetric)
                    .filter(
                        SystemMetric.resolution == "realtime",
                        SystemMetric.recorded_at < cutoff,
                    )
                    .delete()
                )
                if deleted:
                    logger.info(f"Purged {deleted} old realtime metrics")
        except Exception as e:
            logger.warning(f"Metric purge failed: {e}")

    # ── Dashboard Data ────────────────────────

    def get_dashboard_data(self) -> Dict[str, Any]:
        """
        Return a complete dashboard snapshot for the Telegram bot.
        Combines system metrics, tunnel status, and user counts.
        """
        system = self._get_latest_system_metrics()
        user_stats = self._get_user_stats()
        plugin_status = self._get_plugin_status_summary()

        return {
            "server": self.server_label,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system": system,
            "plugins": plugin_status,
            "users": user_stats,
        }

    def _get_latest_system_metrics(self) -> Optional[Dict]:
        """Get most recent system metrics from DB."""
        try:
            with self.db.session() as s:
                m = (
                    s.query(SystemMetric)
                    .filter_by(server=self.server_label)
                    .order_by(SystemMetric.recorded_at.desc())
                    .first()
                )
                if m:
                    return {
                        "cpu_percent": m.cpu_percent,
                        "ram_percent": m.ram_percent,
                        "ram_used_gb": m.ram_used_gb,
                        "ram_total_gb": m.ram_total_gb,
                        "disk_percent": m.disk_percent,
                        "disk_used_gb": m.disk_used_gb,
                        "disk_total_gb": m.disk_total_gb,
                        "net_bytes_sent": m.net_bytes_sent,
                        "net_bytes_recv": m.net_bytes_recv,
                        "recorded_at": m.recorded_at.isoformat(),
                    }
        except Exception:
            pass
        return None

    def _get_user_stats(self) -> Dict[str, Any]:
        """Return user statistics from DB."""
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        try:
            with self.db.session() as s:
                total = s.query(User).count()
                active = s.query(User).filter_by(is_active=True).count()
                expired = s.query(User).filter(User.expire_at < now).count()
                active_today = s.query(User).filter(User.last_connected_at >= today_start).count()
                return {
                    "total": total,
                    "active": active,
                    "expired": expired,
                    "active_today": active_today,
                }
        except Exception as e:
            logger.warning(f"User stats query failed: {e}")
            return {"total": 0, "active": 0, "expired": 0, "active_today": 0}

    def _get_plugin_status_summary(self) -> List[Dict]:
        """Return plugin status list."""
        result = []
        for plugin in self.pm.all():
            try:
                result.append(
                    {
                        "name": plugin.meta.name,
                        "display_name": plugin.meta.display_name,
                        "status": plugin.status().value,
                        "version": plugin.meta.version,
                    }
                )
            except Exception:
                result.append(
                    {
                        "name": plugin.meta.name,
                        "status": "ERROR",
                    }
                )
        return result

    # ── Reports ───────────────────────────────

    def generate_daily_report(self) -> Dict[str, Any]:
        """
        Generate a daily summary report.
        Called at 08:00 daily and sent to Telegram bot.
        """
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)

        try:
            with self.db.session() as s:
                metrics = (
                    s.query(SystemMetric)
                    .filter(
                        SystemMetric.server == self.server_label,
                        SystemMetric.resolution == "hourly",
                        SystemMetric.recorded_at >= yesterday,
                    )
                    .all()
                )

                def avg(values):
                    vals = [v for v in values if v is not None]
                    return round(sum(vals) / len(vals), 1) if vals else 0

                return {
                    "date": yesterday.strftime("%Y-%m-%d"),
                    "server": self.server_label,
                    "avg_cpu": avg([m.cpu_percent for m in metrics]),
                    "max_cpu": max((m.cpu_percent or 0) for m in metrics) if metrics else 0,
                    "avg_ram": avg([m.ram_percent for m in metrics]),
                    "avg_disk": avg([m.disk_percent for m in metrics]),
                    "metric_count": len(metrics),
                    "users": self._get_user_stats(),
                }
        except Exception as e:
            logger.warning(f"Daily report generation failed: {e}")
            return {"error": str(e)}

    def generate_weekly_report(self) -> Dict[str, Any]:
        """Generate a weekly summary report."""
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)

        try:
            with self.db.session() as s:
                metrics = (
                    s.query(SystemMetric)
                    .filter(
                        SystemMetric.server == self.server_label,
                        SystemMetric.resolution.in_(["hourly", "daily"]),
                        SystemMetric.recorded_at >= week_ago,
                    )
                    .all()
                )

                def avg(values):
                    vals = [v for v in values if v is not None]
                    return round(sum(vals) / len(vals), 1) if vals else 0

                return {
                    "period": "7 days",
                    "server": self.server_label,
                    "avg_cpu": avg([m.cpu_percent for m in metrics]),
                    "avg_ram": avg([m.ram_percent for m in metrics]),
                    "avg_disk": avg([m.disk_percent for m in metrics]),
                    "users": self._get_user_stats(),
                }
        except Exception as e:
            logger.warning(f"Weekly report generation failed: {e}")
            return {"error": str(e)}
