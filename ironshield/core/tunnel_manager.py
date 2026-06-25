"""
IronShield - Tunnel Manager
Path: ironshield/core/tunnel_manager.py
Purpose: Monitors tunnel quality, maintains scoring, and notifies Smart Routing Engine.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ironshield.core.plugin_manager import PluginManager
from ironshield.db.database import Database
from ironshield.db.models import Tunnel, TunnelMetric
from ironshield.services.base import BaseService, BenchmarkResult, PluginCategory, ServiceStatus
from ironshield.utils.logger import get_logger

logger = get_logger("tunnel_manager")

TUNNEL_CATEGORIES = {
    PluginCategory.TUNNEL_FAST,
    PluginCategory.TUNNEL_RELIABLE,
    PluginCategory.TUNNEL_OBFUSCATED,
    PluginCategory.EMERGENCY,
}


class TunnelManager:
    """
    Monitors all tunnel plugins and maintains quality scores.

    Responsibilities:
    - Maintain registry of active tunnels in DB
    - Perform periodic connectivity checks
    - Collect and store benchmark results
    - Provide ranked tunnel list to Smart Routing Engine
    - Detect tunnel failures and report to Failover Engine
    """

    def __init__(self, plugin_manager: PluginManager, db: Database):
        self.pm = plugin_manager
        self.db = db
        self._running = False
        self._check_interval = 30  # seconds

    # ── Setup ────────────────────────────────

    def sync_tunnels_to_db(self) -> None:
        """
        Sync loaded tunnel plugins to the Tunnel table in DB.
        Creates records for new tunnels, updates existing ones.
        """
        for plugin in self._get_tunnel_plugins():
            with self.db.session() as s:
                existing = s.query(Tunnel).filter_by(plugin_name=plugin.meta.name).first()
                if existing is None:
                    tunnel = Tunnel(
                        plugin_name=plugin.meta.name,
                        display_name=plugin.meta.display_name,
                        server_role=plugin.server_role.value,
                        is_enabled=True,
                        status="UNKNOWN",
                        priority=plugin.meta.priority,
                        is_emergency=plugin.meta.category == PluginCategory.EMERGENCY,
                    )
                    s.add(tunnel)
                    logger.info(f"Registered tunnel in DB: {plugin.meta.name}")

    # ── Monitoring Loop ───────────────────────

    async def start_monitoring(self, interval: int = 30) -> None:
        """
        Start the async monitoring loop.
        Checks all tunnels at regular intervals.

        Args:
            interval: Check interval in seconds
        """
        self._running = True
        self._check_interval = interval
        logger.info(f"Tunnel monitoring started (interval: {interval}s)")

        while self._running:
            try:
                await self._check_all_tunnels()
            except Exception as e:
                logger.error(f"Tunnel monitor error: {e}")
            await asyncio.sleep(self._check_interval)

    def stop_monitoring(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        logger.info("Tunnel monitoring stopped")

    async def _check_all_tunnels(self) -> None:
        """Check connectivity and status for all tunnel plugins."""
        tunnels = self._get_tunnel_plugins()
        tasks = [self._check_tunnel(plugin) for plugin in tunnels]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_tunnel(self, plugin: BaseService) -> None:
        """Check a single tunnel plugin status and update DB."""
        try:
            status = plugin.status()
            health = plugin.health_check()

            # Map health to tunnel status
            if status == ServiceStatus.RUNNING and health.healthy:
                tunnel_status = "ACTIVE"
            elif status == ServiceStatus.RUNNING and not health.healthy:
                tunnel_status = "DEGRADED"
            elif status == ServiceStatus.STOPPED:
                tunnel_status = "STANDBY"
            else:
                tunnel_status = "FAILED"

            self._update_tunnel_status(plugin.meta.name, tunnel_status)

        except Exception as e:
            logger.warning(f"Check failed for tunnel {plugin.meta.name}: {e}")
            self._update_tunnel_status(plugin.meta.name, "UNKNOWN")

    # ── Benchmark Integration ─────────────────

    def update_tunnel_score(self, plugin_name: str, benchmark_result: BenchmarkResult) -> None:
        """
        Update tunnel score in DB from benchmark result.
        Called by BenchmarkEngine after each test.

        Args:
            plugin_name: Plugin name
            benchmark_result: Latest benchmark result
        """
        if not benchmark_result.success:
            return

        with self.db.session() as s:
            tunnel = s.query(Tunnel).filter_by(plugin_name=plugin_name).first()
            if tunnel:
                tunnel.latency_ms = benchmark_result.latency_ms
                tunnel.real_delay_ms = benchmark_result.real_delay_ms
                tunnel.packet_loss_percent = benchmark_result.packet_loss_percent
                tunnel.throughput_mbps = benchmark_result.throughput_mbps
                tunnel.score = benchmark_result.score
                tunnel.last_checked_at = datetime.now(timezone.utc)

                # Store metric in time series
                metric = TunnelMetric(
                    tunnel_id=tunnel.id,
                    latency_ms=benchmark_result.latency_ms,
                    real_delay_small_ms=benchmark_result.real_delay_ms,
                    packet_loss_percent=benchmark_result.packet_loss_percent,
                    throughput_mbps=benchmark_result.throughput_mbps,
                    score=benchmark_result.score,
                    test_type="standard",
                    resolution="realtime",
                )
                s.add(metric)

    # ── Ranked List ───────────────────────────

    def get_ranked_tunnels(self) -> List[Dict]:
        """
        Return tunnels sorted by score (best first).
        Excludes emergency tunnels unless all others are failed.

        Returns:
            List of dicts with tunnel info and scores
        """
        with self.db.session() as s:
            tunnels = (
                s.query(Tunnel)
                .filter_by(is_enabled=True)
                .order_by(Tunnel.score.desc().nullslast())
                .all()
            )

            ranked = []
            for t in tunnels:
                ranked.append(
                    {
                        "name": t.plugin_name,
                        "display_name": t.display_name,
                        "status": t.status,
                        "score": t.score,
                        "latency_ms": t.latency_ms,
                        "real_delay_ms": t.real_delay_ms,
                        "packet_loss_percent": t.packet_loss_percent,
                        "throughput_mbps": t.throughput_mbps,
                        "priority": t.priority,
                        "is_emergency": t.is_emergency,
                        "last_checked": t.last_checked_at.isoformat()
                        if t.last_checked_at
                        else None,
                    }
                )

        return ranked

    def get_best_tunnel(self, exclude_emergency: bool = True) -> Optional[Dict]:
        """
        Get the highest-scoring active tunnel.

        Args:
            exclude_emergency: Skip emergency tunnels (Storm-DNS)

        Returns:
            Best tunnel dict or None
        """
        ranked = self.get_ranked_tunnels()
        for tunnel in ranked:
            if tunnel["status"] != "ACTIVE":
                continue
            if exclude_emergency and tunnel["is_emergency"]:
                continue
            return tunnel
        return None

    def get_backup_tunnel(self, primary_name: str) -> Optional[Dict]:
        """Get the best active tunnel that is not the primary."""
        ranked = self.get_ranked_tunnels()
        for tunnel in ranked:
            if tunnel["name"] == primary_name:
                continue
            if tunnel["status"] != "ACTIVE":
                continue
            if tunnel["is_emergency"]:
                continue
            return tunnel
        return None

    def get_emergency_tunnel(self) -> Optional[Dict]:
        """Get the emergency tunnel (Storm-DNS)."""
        ranked = self.get_ranked_tunnels()
        for tunnel in ranked:
            if tunnel["is_emergency"] and tunnel["status"] in ("ACTIVE", "STANDBY"):
                return tunnel
        return None

    def all_non_emergency_failed(self) -> bool:
        """Check if all non-emergency tunnels have failed."""
        ranked = self.get_ranked_tunnels()
        non_emergency = [t for t in ranked if not t["is_emergency"]]
        if not non_emergency:
            return True
        return all(t["status"] == "FAILED" for t in non_emergency)

    # ── Status Updates ────────────────────────

    def _update_tunnel_status(self, plugin_name: str, status: str) -> None:
        """Update tunnel status in DB."""
        try:
            with self.db.session() as s:
                tunnel = s.query(Tunnel).filter_by(plugin_name=plugin_name).first()
                if tunnel:
                    tunnel.status = status
                    tunnel.last_checked_at = datetime.now(timezone.utc)
        except Exception as e:
            logger.warning(f"Failed to update tunnel status [{plugin_name}]: {e}")

    def mark_as_primary(self, plugin_name: str) -> None:
        """Mark a tunnel as the current primary route."""
        with self.db.session() as s:
            # Clear all primary flags
            s.query(Tunnel).update({"is_primary": False})
            # Set new primary
            tunnel = s.query(Tunnel).filter_by(plugin_name=plugin_name).first()
            if tunnel:
                tunnel.is_primary = True
                tunnel.last_switched_at = datetime.now(timezone.utc)

    def mark_as_backup(self, plugin_name: str) -> None:
        """Mark a tunnel as the current backup route."""
        with self.db.session() as s:
            s.query(Tunnel).update({"is_backup": False})
            tunnel = s.query(Tunnel).filter_by(plugin_name=plugin_name).first()
            if tunnel:
                tunnel.is_backup = True

    # ── Helpers ──────────────────────────────

    def _get_tunnel_plugins(self) -> List[BaseService]:
        """Get all loaded plugins that are tunnel-type."""
        return [p for p in self.pm.all() if p.meta.category in TUNNEL_CATEGORIES]

    def get_tunnel_summary(self) -> Dict:
        """Return a summary suitable for Telegram bot display."""
        ranked = self.get_ranked_tunnels()
        active = [t for t in ranked if t["status"] == "ACTIVE"]
        failed = [t for t in ranked if t["status"] == "FAILED"]

        primary = next((t for t in ranked if t.get("is_primary")), None)
        backup = next((t for t in ranked if t.get("is_backup")), None)

        return {
            "total": len(ranked),
            "active": len(active),
            "failed": len(failed),
            "primary": primary,
            "backup": backup,
            "tunnels": ranked,
        }
