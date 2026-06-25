"""
IronShield - Service Manager
Path: ironshield/core/service_manager.py
Purpose: Manages install, start, stop, restart, and status of all plugins.
         Integrates with PluginManager and UFW management.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ironshield.core.plugin_manager import PluginManager
from ironshield.core.config_engine import ConfigEngine
from ironshield.db.database import Database
from ironshield.db.models import AuditLog
from ironshield.services.base import BaseService, Result, ServerRole, ServiceStatus
from ironshield.utils.logger import get_logger
from ironshield.utils.system import run_command

logger = get_logger("service_manager")


class UFWManager:
    """Manages UFW firewall rules for IronShield plugins."""

    def __init__(self, foreign_ip: str = "", iran_ip: str = ""):
        self.foreign_ip = foreign_ip
        self.iran_ip = iran_ip

    def enable(self) -> bool:
        """Enable UFW with default deny-incoming policy."""
        commands = [
            "ufw --force reset",
            "ufw default deny incoming",
            "ufw default allow outgoing",
            "ufw allow 22/tcp comment 'SSH'",
            "ufw --force enable",
        ]
        for cmd in commands:
            code, _, err = run_command(f"sudo {cmd}")
            if code != 0:
                logger.error(f"UFW command failed [{cmd}]: {err}")
                return False
        logger.info("UFW enabled with default deny-incoming policy")
        return True

    def open_port(
        self,
        port: int,
        protocol: str = "tcp",
        from_ip: Optional[str] = None,
        comment: str = "",
    ) -> bool:
        """Open a port in UFW, optionally restricted to a source IP."""
        if from_ip:
            cmd = f"sudo ufw allow from {from_ip} to any port {port} proto {protocol}"
        else:
            cmd = f"sudo ufw allow {port}/{protocol}"

        if comment:
            cmd += f" comment '{comment}'"

        code, _, err = run_command(cmd)
        if code != 0:
            logger.warning(f"UFW open port failed [{port}/{protocol}]: {err}")
            return False
        logger.info(f"UFW: opened {port}/{protocol}" + (f" from {from_ip}" if from_ip else ""))
        return True

    def close_port(self, port: int, protocol: str = "tcp") -> bool:
        """Close a port in UFW."""
        code, _, err = run_command(f"sudo ufw delete allow {port}/{protocol}")
        if code != 0:
            logger.warning(f"UFW close port failed [{port}/{protocol}]: {err}")
        return code == 0

    def apply_plugin_rules(self, plugin: BaseService, server_role: ServerRole) -> None:
        """Apply UFW rules for a specific plugin based on server role."""
        peer_ip = self.foreign_ip if server_role == ServerRole.IRAN else self.iran_ip

        for rule in plugin.get_ufw_rules():
            port = rule.get("port")
            proto = rule.get("protocol", "tcp")
            description = rule.get("description", plugin.meta.name)

            # Tunnel ports: only allow from peer server
            is_tunnel = (
                plugin.meta.category.value.startswith("tunnel")
                or plugin.meta.category.value == "emergency"
            )

            if is_tunnel and peer_ip:
                self.open_port(port, proto, from_ip=peer_ip, comment=description)
            else:
                self.open_port(port, proto, comment=description)

    def remove_plugin_rules(self, plugin: BaseService) -> None:
        """Remove UFW rules for a specific plugin."""
        for rule in plugin.get_ufw_rules():
            self.close_port(rule.get("port"), rule.get("protocol", "tcp"))

    def get_status(self) -> str:
        """Get current UFW status."""
        _, out, _ = run_command("sudo ufw status verbose")
        return out


class ServiceManager:
    """
    Central manager for all IronShield plugin lifecycles.

    Responsibilities:
    - Install, start, stop, restart all plugins
    - Manage UFW rules per plugin
    - Record all actions in audit log
    - Provide status summary for monitoring
    """

    def __init__(
        self,
        plugin_manager: PluginManager,
        config_engine: ConfigEngine,
        db: Database,
        server_role: ServerRole,
    ):
        self.pm = plugin_manager
        self.cfg = config_engine
        self.db = db
        self.server_role = server_role
        self.ufw = UFWManager(
            foreign_ip=config_engine.get("server.foreign.ip", ""),
            iran_ip=config_engine.get("server.iran.ip", ""),
        )

    # ── Install ──────────────────────────────

    def install_plugin(self, name: str, performed_by: str = "system") -> Result:
        """
        Install a single plugin and open its UFW ports.

        Args:
            name: Plugin name
            performed_by: Actor (for audit log)

        Returns:
            Result
        """
        plugin = self.pm.get(name)
        if plugin is None:
            return Result.fail(f"Plugin not found: {name}")

        logger.info(f"Installing plugin: {name}")
        result = plugin.install()

        if result.success:
            self.ufw.apply_plugin_rules(plugin, self.server_role)
            self._audit(
                action=f"install_{name}",
                performed_by=performed_by,
                success=True,
            )
            logger.info(f"Plugin installed successfully: {name}")
        else:
            self._audit(
                action=f"install_{name}",
                performed_by=performed_by,
                success=False,
                error=result.error,
            )

        return result

    def install_all(self, performed_by: str = "system") -> Dict[str, Result]:
        """Install all plugins in priority order."""
        results = {}
        plugins = sorted(self.pm.all(), key=lambda p: p.meta.priority)

        for plugin in plugins:
            results[plugin.meta.name] = self.install_plugin(plugin.meta.name, performed_by)

        success_count = sum(1 for r in results.values() if r.success)
        logger.info(f"Install complete: {success_count}/{len(results)} plugins installed")
        return results

    # ── Lifecycle ────────────────────────────

    def start(self, name: str, performed_by: str = "system") -> Result:
        """Start a plugin service."""
        plugin = self._get_plugin(name)
        if plugin is None:
            return Result.fail(f"Plugin not found: {name}")

        result = plugin.start()
        self._audit(f"start_{name}", performed_by, result.success, result.error)
        return result

    def stop(self, name: str, performed_by: str = "system") -> Result:
        """Stop a plugin service."""
        plugin = self._get_plugin(name)
        if plugin is None:
            return Result.fail(f"Plugin not found: {name}")

        result = plugin.stop()
        self._audit(f"stop_{name}", performed_by, result.success, result.error)
        return result

    def restart(self, name: str, performed_by: str = "system") -> Result:
        """Restart a plugin service."""
        plugin = self._get_plugin(name)
        if plugin is None:
            return Result.fail(f"Plugin not found: {name}")

        result = plugin.restart()
        self._audit(f"restart_{name}", performed_by, result.success, result.error)
        return result

    def start_all(self) -> Dict[str, Result]:
        """Start all plugins sorted by priority."""
        results = {}
        for plugin in sorted(self.pm.all(), key=lambda p: p.meta.priority):
            results[plugin.meta.name] = self.start(plugin.meta.name)
        return results

    def stop_all(self) -> Dict[str, Result]:
        """Stop all plugins."""
        results = {}
        for plugin in self.pm.all():
            results[plugin.meta.name] = self.stop(plugin.meta.name)
        return results

    # ── Status ───────────────────────────────

    def status(self, name: str) -> Optional[ServiceStatus]:
        """Get status of a specific plugin."""
        plugin = self._get_plugin(name)
        return plugin.status() if plugin else None

    def status_all(self) -> Dict[str, Dict]:
        """Return status summary for all plugins."""
        summary = {}
        for name, plugin in self.pm._registry.items():
            try:
                status = plugin.status()
                summary[name] = {
                    "display_name": plugin.meta.display_name,
                    "version": plugin.meta.version,
                    "category": plugin.meta.category.value,
                    "priority": plugin.meta.priority,
                    "status": status.value,
                    "is_running": plugin.is_running(),
                    "is_required": plugin.meta.required,
                }
            except Exception as e:
                summary[name] = {"status": "ERROR", "error": str(e)}
        return summary

    def get_logs(self, name: str, lines: int = 100) -> List[str]:
        """Get recent logs from a plugin."""
        plugin = self._get_plugin(name)
        if plugin is None:
            return [f"Plugin not found: {name}"]
        return plugin.get_logs(lines)

    # ── Update ───────────────────────────────

    def update_plugin(self, name: str, performed_by: str = "system") -> Result:
        """Update a plugin to its latest version."""
        plugin = self._get_plugin(name)
        if plugin is None:
            return Result.fail(f"Plugin not found: {name}")

        result = plugin.update()
        self._audit(f"update_{name}", performed_by, result.success, result.error)
        return result

    # ── Remove ───────────────────────────────

    def uninstall_plugin(self, name: str, performed_by: str = "system") -> Result:
        """Uninstall a plugin and remove its UFW rules."""
        plugin = self._get_plugin(name)
        if plugin is None:
            return Result.fail(f"Plugin not found: {name}")

        result = plugin.uninstall()
        if result.success:
            self.ufw.remove_plugin_rules(plugin)
            self._audit(f"uninstall_{name}", performed_by, True)
        else:
            self._audit(f"uninstall_{name}", performed_by, False, result.error)

        return result

    # ── Helpers ──────────────────────────────

    def _get_plugin(self, name: str) -> Optional[BaseService]:
        """Get plugin by name with error logging."""
        plugin = self.pm.get(name)
        if plugin is None:
            logger.warning(f"Plugin not found: {name}")
        return plugin

    def _audit(
        self,
        action: str,
        performed_by: str,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """Record an action in the audit log."""
        try:
            with self.db.session() as s:
                s.add(
                    AuditLog(
                        performed_by=performed_by
                        if performed_by in ("admin_bot", "admin_cli", "system", "api")
                        else "system",
                        action=action,
                        resource_type="plugin",
                        success=success,
                        error_message=error,
                    )
                )
        except Exception as e:
            logger.warning(f"Failed to write audit log: {e}")
