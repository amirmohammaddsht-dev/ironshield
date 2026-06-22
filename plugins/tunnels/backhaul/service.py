"""
IronShield Plugin — backhaul
Path: plugins/tunnels/backhaul/service.py
Purpose: Service implementation for backhaul.
         Full implementation in Phase 4 (Service implementations).
"""

from __future__ import annotations

from typing import Dict, List, Any

from ironshield.services.base import (
    BaseService,
    PluginMeta,
    PluginCategory,
    ServerRole,
    ServiceStatus,
    Result,
    HealthResult,
    BenchmarkResult,
)
from ironshield.utils.system import run_command, service_is_active, systemctl


class BackhaulService(BaseService):
    """
    backhaul plugin implementation.
    Stub — full implementation in Phase 4.
    """

    @property
    def meta(self) -> PluginMeta:
        # Loaded dynamically from plugin.yaml by PluginManager
        raise NotImplementedError("Meta loaded from plugin.yaml")

    def install(self) -> Result:
        return Result.fail("Not implemented yet — Phase 4")

    def uninstall(self) -> Result:
        return Result.fail("Not implemented yet — Phase 4")

    def start(self) -> Result:
        return Result.fail("Not implemented yet — Phase 4")

    def stop(self) -> Result:
        return Result.fail("Not implemented yet — Phase 4")

    def status(self) -> ServiceStatus:
        return ServiceStatus.NOT_INSTALLED

    def health_check(self) -> HealthResult:
        return HealthResult(
            healthy=False,
            status=ServiceStatus.NOT_INSTALLED,
            message="Not implemented yet — Phase 4",
        )

    def get_config(self) -> Dict[str, Any]:
        return self.config

    def apply_config(self, config: Dict[str, Any]) -> Result:
        return Result.fail("Not implemented yet — Phase 4")

    def get_logs(self, lines: int = 100) -> List[str]:
        return []
