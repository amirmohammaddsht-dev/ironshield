"""
IronShield - Benchmark Engine
Path: ironshield/core/benchmark_engine.py
Purpose: Schedules and runs latency, packet loss, throughput, and real-delay
         tests on all tunnel plugins. Reports results to TunnelManager.
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional

from ironshield.core.plugin_manager import PluginManager
from ironshield.core.tunnel_manager import TunnelManager
from ironshield.db.database import Database
from ironshield.services.base import BaseService, BenchmarkResult, PluginCategory
from ironshield.utils.logger import get_benchmark_logger
from ironshield.utils.network import (
    ping,
    measure_packet_loss,
    measure_throughput,
    measure_real_delay,
)

logger = get_benchmark_logger()

TUNNEL_CATEGORIES = {
    PluginCategory.TUNNEL_FAST,
    PluginCategory.TUNNEL_RELIABLE,
    PluginCategory.TUNNEL_OBFUSCATED,
    PluginCategory.EMERGENCY,
}


class BenchmarkSchedule:
    """Benchmark schedule configuration."""

    def __init__(
        self,
        quick_interval_minutes: int = 5,
        standard_interval_minutes: int = 30,
        full_interval_hours: int = 6,
    ):
        self.quick_interval_sec = quick_interval_minutes * 60
        self.standard_interval_sec = standard_interval_minutes * 60
        self.full_interval_sec = full_interval_hours * 3600


class BenchmarkEngine:
    """
    Runs benchmarks on all tunnel plugins on a schedule.

    Test types:
        quick:    Latency only (every 5 min)
        standard: Latency + packet loss (every 30 min)
        full:     All tests including throughput + real delay (every 6h)

    Results are passed to TunnelManager which updates scores in DB.
    """

    def __init__(
        self,
        plugin_manager: PluginManager,
        tunnel_manager: TunnelManager,
        db: Database,
        schedule: Optional[BenchmarkSchedule] = None,
        foreign_ip: str = "",
        agent_port: int = 8080,
    ):
        self.pm = plugin_manager
        self.tm = tunnel_manager
        self.db = db
        self.schedule = schedule or BenchmarkSchedule()
        self.foreign_ip = foreign_ip
        self.agent_port = agent_port
        self._running = False
        self._last_quick = 0.0
        self._last_standard = 0.0
        self._last_full = 0.0

        # Latency targets (configurable)
        self.latency_targets = [
            {"name": "Google DNS", "host": "8.8.8.8"},
            {"name": "Cloudflare DNS", "host": "1.1.1.1"},
        ]
        if foreign_ip:
            self.latency_targets.append({"name": "Foreign Server", "host": foreign_ip})

    # ── Scheduling Loop ───────────────────────

    async def start(self) -> None:
        """Start the async benchmark scheduling loop."""
        self._running = True
        logger.info("Benchmark engine started")

        while self._running:
            now = time.monotonic()
            try:
                if now - self._last_full >= self.schedule.full_interval_sec:
                    await self.run_full()
                    self._last_full = now
                    self._last_standard = now
                    self._last_quick = now

                elif now - self._last_standard >= self.schedule.standard_interval_sec:
                    await self.run_standard()
                    self._last_standard = now
                    self._last_quick = now

                elif now - self._last_quick >= self.schedule.quick_interval_sec:
                    await self.run_quick()
                    self._last_quick = now

            except Exception as e:
                logger.error(f"Benchmark loop error: {e}")

            await asyncio.sleep(10)

    def stop(self) -> None:
        """Stop the benchmark loop."""
        self._running = False
        logger.info("Benchmark engine stopped")

    # ── Test Runners ──────────────────────────

    async def run_quick(self) -> Dict[str, BenchmarkResult]:
        """Run quick benchmark (latency only) on all tunnels."""
        logger.info("Running quick benchmark (latency)")
        return await self._run_for_all_tunnels(test_type="quick")

    async def run_standard(self) -> Dict[str, BenchmarkResult]:
        """Run standard benchmark (latency + packet loss) on all tunnels."""
        logger.info("Running standard benchmark (latency + loss)")
        return await self._run_for_all_tunnels(test_type="standard")

    async def run_full(self) -> Dict[str, BenchmarkResult]:
        """Run full benchmark (all metrics) on all tunnels."""
        logger.info("Running full benchmark (all metrics)")
        return await self._run_for_all_tunnels(test_type="full")

    async def run_single(
        self, plugin_name: str, test_type: str = "full"
    ) -> Optional[BenchmarkResult]:
        """
        Run benchmark on a single tunnel plugin.

        Args:
            plugin_name: Plugin name
            test_type: 'quick', 'standard', or 'full'

        Returns:
            BenchmarkResult or None if plugin not found
        """
        plugin = self.pm.get(plugin_name)
        if plugin is None:
            logger.warning(f"Plugin not found for benchmark: {plugin_name}")
            return None

        return await self._benchmark_plugin(plugin, test_type)

    async def _run_for_all_tunnels(self, test_type: str) -> Dict[str, BenchmarkResult]:
        """Run benchmark for all tunnel plugins in parallel."""
        tunnel_plugins = [p for p in self.pm.all() if p.meta.category in TUNNEL_CATEGORIES]

        if not tunnel_plugins:
            logger.warning("No tunnel plugins available for benchmarking")
            return {}

        start_time = time.monotonic()
        tasks = {
            plugin.meta.name: self._benchmark_plugin(plugin, test_type) for plugin in tunnel_plugins
        }

        results = {}
        for name, coro in tasks.items():
            try:
                result = await coro
                results[name] = result
                if result.success:
                    self.tm.update_tunnel_score(name, result)
                    logger.info(
                        f"  {name}: latency={result.latency_ms}ms "
                        f"loss={result.packet_loss_percent}% "
                        f"score={result.score}"
                    )
            except Exception as e:
                logger.error(f"Benchmark failed for {name}: {e}")
                results[name] = BenchmarkResult(success=False, error=str(e))

        elapsed = time.monotonic() - start_time
        logger.info(f"Benchmark complete in {elapsed:.1f}s ({len(results)} tunnels)")
        return results

    async def _benchmark_plugin(self, plugin: BaseService, test_type: str) -> BenchmarkResult:
        """
        Run the appropriate benchmark tests for a plugin.

        If plugin has custom benchmark, use it.
        Otherwise use standard tests based on test_type.
        """
        # Use plugin's own benchmark if available
        if plugin.supports_benchmark():
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, plugin.benchmark)
            return result

        # Standard benchmark using network utilities
        remote_ip = self._get_remote_ip(plugin)
        if not remote_ip:
            return BenchmarkResult(success=False, error="No remote IP configured for benchmark")

        result = BenchmarkResult(success=True)

        # Quick: latency only
        loop = asyncio.get_event_loop()
        ping_result = await loop.run_in_executor(None, lambda: ping(remote_ip, count=10))
        if not ping_result.success:
            return BenchmarkResult(success=False, error="Host unreachable")

        result.latency_ms = round(ping_result.avg_ms, 2)

        # Standard: add packet loss
        if test_type in ("standard", "full"):
            loss = await loop.run_in_executor(
                None, lambda: measure_packet_loss(remote_ip, cycles=20)
            )
            result.packet_loss_percent = round(loss, 2)

        # Full: add throughput + real delay
        if test_type == "full":
            tp = await loop.run_in_executor(None, lambda: measure_throughput(remote_ip))
            if tp.success:
                result.throughput_mbps = tp.download_mbps

            if self.foreign_ip and self.agent_port:
                try:
                    delay_result = await measure_real_delay(self.foreign_ip, self.agent_port)
                    if delay_result.success:
                        result.real_delay_ms = round(delay_result.avg_ms, 2)
                except Exception as e:
                    logger.debug(f"Real delay test failed: {e}")

        result.calculate_score()
        return result

    def _get_remote_ip(self, plugin: BaseService) -> Optional[str]:
        """Extract the remote IP for a plugin from its config."""
        config = plugin.get_config()
        for key in ("remote_host", "peer_ip", "remote_ip", "server_ip"):
            if config.get(key):
                return config[key]
        return self.foreign_ip or None

    # ── Latency Targets ───────────────────────

    def add_latency_target(self, name: str, host: str) -> None:
        """Add a custom latency test target."""
        self.latency_targets.append({"name": name, "host": host})
        logger.info(f"Added latency target: {name} ({host})")

    def remove_latency_target(self, host: str) -> bool:
        """Remove a latency target by host."""
        original = len(self.latency_targets)
        self.latency_targets = [t for t in self.latency_targets if t["host"] != host]
        return len(self.latency_targets) < original

    async def measure_all_targets(self) -> Dict[str, float]:
        """Measure latency to all configured targets and return averages."""
        loop = asyncio.get_event_loop()
        results = {}
        for target in self.latency_targets:
            try:
                r = await loop.run_in_executor(None, lambda h=target["host"]: ping(h, count=5))
                if r.success:
                    results[target["name"]] = round(r.avg_ms, 2)
            except Exception as e:
                logger.debug(f"Latency target {target['name']} failed: {e}")
        return results
