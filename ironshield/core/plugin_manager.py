"""
IronShield - Plugin Manager
Path: ironshield/core/plugin_manager.py
Purpose: Discover, load, validate, and manage all installed plugins.
         Reads plugin.yaml from each plugin directory and instantiates service classes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Dict, List, Optional, Type

import yaml

from ironshield.services.base import (
    BaseService,
    PluginMeta,
    PluginCategory,
    ServerRole,
    Result,
)
from ironshield.utils.logger import get_logger

logger = get_logger("plugin_manager")

# Root directory of the plugins folder
PLUGINS_ROOT = Path(__file__).parent.parent.parent / "plugins"

# Required files every plugin directory must have
REQUIRED_FILES = ["plugin.yaml", "install.sh", "uninstall.sh", "update.sh", "service.py"]


class PluginLoadError(Exception):
    """Raised when a plugin fails to load."""

    pass


class PluginManager:
    """
    Discovers and manages all IronShield plugins.

    Responsibilities:
    - Scan plugins/ directory for valid plugins
    - Parse plugin.yaml metadata
    - Dynamically import service.py classes
    - Provide a registry of loaded plugins
    - Filter plugins by server role

    Usage:
        pm = PluginManager(server_role=ServerRole.IRAN, config={...})
        pm.discover()

        openvpn = pm.get("openvpn")
        openvpn.install()

        tunnels = pm.get_by_category(PluginCategory.TUNNEL_FAST)
    """

    def __init__(self, server_role: ServerRole, global_config: Dict):
        """
        Args:
            server_role: The role of this server (iran/foreign)
            global_config: Full IronShield configuration dict
        """
        self.server_role = server_role
        self.global_config = global_config
        self._registry: Dict[str, BaseService] = {}
        self._meta_registry: Dict[str, PluginMeta] = {}

    # ── Discovery ────────────────────────────

    def discover(self) -> List[str]:
        """
        Scan the plugins/ directory and load all valid plugins
        that support this server's role.

        Returns:
            List of successfully loaded plugin names
        """
        loaded = []
        errors = []

        if not PLUGINS_ROOT.exists():
            logger.warning(f"Plugins directory not found: {PLUGINS_ROOT}")
            return []

        # Walk all subdirectories looking for plugin.yaml
        for plugin_dir in sorted(PLUGINS_ROOT.rglob("plugin.yaml")):
            plugin_path = plugin_dir.parent
            try:
                name = self._load_plugin(plugin_path)
                if name:
                    loaded.append(name)
            except PluginLoadError as e:
                errors.append(str(e))
                logger.warning(f"Skipped plugin at {plugin_path}: {e}")

        logger.info(f"Plugin discovery complete: {len(loaded)} loaded, {len(errors)} failed")
        for name in loaded:
            logger.info(f"  ✅ {name} ({self._meta_registry[name].category.value})")

        return loaded

    def _load_plugin(self, plugin_path: Path) -> Optional[str]:
        """
        Load a single plugin from its directory.

        Args:
            plugin_path: Path to the plugin directory

        Returns:
            Plugin name if loaded, None if skipped (wrong role)

        Raises:
            PluginLoadError: If the plugin is invalid or fails to import
        """
        # 1. Validate required files exist
        self._validate_plugin_files(plugin_path)

        # 2. Parse plugin.yaml
        meta = self._parse_plugin_yaml(plugin_path / "plugin.yaml")

        # 3. Skip if this plugin doesn't support our server role
        if not meta.supports_role(self.server_role):
            logger.debug(f"Skipping {meta.name}: not supported on {self.server_role.value}")
            return None

        # 4. Dynamically import service.py
        service_class = self._import_service_class(plugin_path, meta.name)

        # 5. Get plugin-specific config
        plugin_config = self._get_plugin_config(meta.name)

        # 6. Instantiate
        try:
            instance = service_class(
                server_role=self.server_role,
                config=plugin_config,
            )
        except Exception as e:
            raise PluginLoadError(f"Failed to instantiate {meta.name}: {e}") from e

        # 7. Register
        self._registry[meta.name] = instance
        self._meta_registry[meta.name] = meta

        return meta.name

    def _validate_plugin_files(self, plugin_path: Path) -> None:
        """Check that all required plugin files exist."""
        missing = []
        for fname in REQUIRED_FILES:
            if not (plugin_path / fname).exists():
                missing.append(fname)
        if missing:
            raise PluginLoadError(
                f"Missing required files in {plugin_path.name}: {', '.join(missing)}"
            )

    def _parse_plugin_yaml(self, yaml_path: Path) -> PluginMeta:
        """Parse plugin.yaml and return a PluginMeta instance."""
        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
        except Exception as e:
            raise PluginLoadError(f"Failed to parse plugin.yaml: {e}") from e

        try:
            roles = [ServerRole(r) for r in data.get("roles", ["both"])]
            category = PluginCategory(data.get("category", "tunnel_reliable"))

            return PluginMeta(
                name=data["name"],
                display_name=data.get("display_name", data["name"]),
                version=str(data.get("version", "unknown")),
                author=data.get("author", "unknown"),
                source_url=data.get("source", ""),
                license=data.get("license", "unknown"),
                roles=roles,
                category=category,
                priority=int(data.get("priority", 5)),
                required=bool(data.get("required", False)),
                min_ironshield_version=str(data.get("min_ironshield_version", "1.0.0")),
                description=data.get("description", ""),
                auto_update_enabled=data.get("auto_update", {}).get("enabled", True),
                auto_update_source=data.get("auto_update", {}).get("source_type", "github_release"),
                auto_update_repo=data.get("auto_update", {}).get("repo"),
                auto_update_url=data.get("auto_update", {}).get("url"),
                ufw_ports=data.get("dependencies", {}).get("ports", []),
            )
        except KeyError as e:
            raise PluginLoadError(f"Missing required field in plugin.yaml: {e}") from e

    def _import_service_class(self, plugin_path: Path, plugin_name: str) -> Type[BaseService]:
        """Dynamically import service.py and return the service class."""
        service_file = plugin_path / "service.py"
        module_name = f"ironshield_plugin_{plugin_name}"

        try:
            spec = importlib.util.spec_from_file_location(module_name, service_file)
            if spec is None or spec.loader is None:
                raise PluginLoadError(f"Cannot load spec for {service_file}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as e:
            raise PluginLoadError(f"Failed to import service.py for {plugin_name}: {e}") from e

        # Find a class that extends BaseService
        service_class = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, BaseService) and attr is not BaseService:
                service_class = attr
                break

        if service_class is None:
            raise PluginLoadError(f"No BaseService subclass found in {plugin_name}/service.py")

        return service_class

    def _get_plugin_config(self, plugin_name: str) -> Dict:
        """Extract plugin-specific config from global config."""
        tunnels_cfg = self.global_config.get("tunnels", {})
        services_cfg = self.global_config.get("services", {})
        return tunnels_cfg.get(plugin_name, services_cfg.get(plugin_name, {}))

    # ── Registry Access ──────────────────────

    def get(self, name: str) -> Optional[BaseService]:
        """Get a plugin instance by name."""
        return self._registry.get(name)

    def get_meta(self, name: str) -> Optional[PluginMeta]:
        """Get plugin metadata by name."""
        return self._meta_registry.get(name)

    def all(self) -> List[BaseService]:
        """Return all loaded plugin instances."""
        return list(self._registry.values())

    def all_names(self) -> List[str]:
        """Return names of all loaded plugins."""
        return list(self._registry.keys())

    def get_by_category(self, category: PluginCategory) -> List[BaseService]:
        """Return all plugins of a given category, sorted by priority."""
        plugins = [p for p in self._registry.values() if p.meta.category == category]
        return sorted(plugins, key=lambda p: p.meta.priority)

    def get_by_role(self, role: ServerRole) -> List[BaseService]:
        """Return all plugins that support a given role."""
        return [p for p in self._registry.values() if p.meta.supports_role(role)]

    def get_required(self) -> List[BaseService]:
        """Return all required plugins."""
        return [p for p in self._registry.values() if p.meta.required]

    def get_tunnels(self) -> List[BaseService]:
        """Return all tunnel plugins sorted by priority."""
        tunnel_categories = {
            PluginCategory.TUNNEL_FAST,
            PluginCategory.TUNNEL_RELIABLE,
            PluginCategory.TUNNEL_OBFUSCATED,
            PluginCategory.EMERGENCY,
        }
        plugins = [p for p in self._registry.values() if p.meta.category in tunnel_categories]
        return sorted(plugins, key=lambda p: p.meta.priority)

    def is_loaded(self, name: str) -> bool:
        """Check if a plugin is loaded."""
        return name in self._registry

    # ── Bulk Operations ──────────────────────

    def install_all(self) -> Dict[str, Result]:
        """Install all loaded plugins in priority order."""
        results = {}
        plugins = sorted(self._registry.values(), key=lambda p: p.meta.priority)
        for plugin in plugins:
            logger.info(f"Installing {plugin.meta.name}...")
            results[plugin.meta.name] = plugin.install()
        return results

    def start_all(self) -> Dict[str, Result]:
        """Start all loaded plugins."""
        results = {}
        for name, plugin in self._registry.items():
            results[name] = plugin.start()
        return results

    def stop_all(self) -> Dict[str, Result]:
        """Stop all loaded plugins."""
        results = {}
        for name, plugin in self._registry.items():
            results[name] = plugin.stop()
        return results

    def health_check_all(self) -> Dict[str, "HealthResult"]:  # noqa: F821
        """Run health checks on all loaded plugins."""
        from ironshield.services.base import HealthResult

        results = {}
        for name, plugin in self._registry.items():
            try:
                results[name] = plugin.health_check()
            except Exception as e:
                from ironshield.services.base import ServiceStatus

                results[name] = HealthResult(
                    healthy=False,
                    status=ServiceStatus.UNKNOWN,
                    error=str(e),
                )
        return results

    def get_status_summary(self) -> Dict[str, Dict]:
        """Return a summary of all plugin statuses."""
        summary = {}
        for name, plugin in self._registry.items():
            try:
                status = plugin.status()
                meta = plugin.meta
                summary[name] = {
                    "display_name": meta.display_name,
                    "version": meta.version,
                    "category": meta.category.value,
                    "priority": meta.priority,
                    "status": status.value,
                    "role": meta.roles,
                }
            except Exception as e:
                summary[name] = {"status": "ERROR", "error": str(e)}
        return summary

    def __len__(self) -> int:
        return len(self._registry)

    def __repr__(self) -> str:
        return (
            f"<PluginManager role={self.server_role.value} plugins={list(self._registry.keys())}>"
        )
