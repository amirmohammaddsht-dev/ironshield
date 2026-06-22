"""
IronShield - Plugin Updater
Path: ironshield/core/plugin_updater.py
Purpose: Check for and apply updates to installed plugins.
         Supports GitHub Releases, APT packages, and custom URLs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, List, Dict

import httpx

from ironshield.core.plugin_manager import PluginManager
from ironshield.services.base import PluginMeta, Result
from ironshield.utils.logger import get_logger
from ironshield.utils.system import run_command

logger = get_logger("plugin_updater")

GITHUB_API = "https://api.github.com"


@dataclass
class UpdateInfo:
    """Information about an available update."""

    plugin_name: str
    current_version: str
    latest_version: str
    update_available: bool
    changelog: Optional[str] = None
    download_url: Optional[str] = None


class PluginUpdater:
    """
    Checks and applies plugin updates from various sources.

    Supported update sources:
        - github_release: Downloads latest release binary from GitHub
        - apt: Uses apt-get to update a system package
        - url: Downloads from a fixed URL

    Usage:
        updater = PluginUpdater(plugin_manager)

        # Check for updates
        updates = await updater.check_all()

        # Apply a specific update
        result = updater.update_plugin("gost")

        # Apply all updates
        results = updater.update_all()
    """

    def __init__(self, plugin_manager: PluginManager):
        self.pm = plugin_manager

    # ── Check for Updates ────────────────────

    def check_all(self) -> List[UpdateInfo]:
        """
        Check all loaded plugins for available updates.

        Returns:
            List of UpdateInfo for plugins that have updates available
        """
        results = []
        for plugin in self.pm.all():
            info = self.check_plugin(plugin.meta)
            if info:
                results.append(info)
                if info.update_available:
                    logger.info(
                        f"Update available: {info.plugin_name} "
                        f"{info.current_version} → {info.latest_version}"
                    )
        return results

    def check_plugin(self, meta: PluginMeta) -> Optional[UpdateInfo]:
        """
        Check if a specific plugin has an update available.

        Args:
            meta: Plugin metadata

        Returns:
            UpdateInfo or None if check failed
        """
        if not meta.auto_update_enabled:
            return None

        source = meta.auto_update_source
        latest = None
        download_url = None
        changelog = None

        try:
            if source == "github_release" and meta.auto_update_repo:
                latest, download_url, changelog = self._check_github_release(
                    meta.auto_update_repo, meta.name
                )
            elif source == "apt":
                latest = self._check_apt_version(meta.name)
            elif source == "url" and meta.auto_update_url:
                latest = self._check_url_version(meta.auto_update_url)

        except Exception as e:
            logger.debug(f"Update check failed for {meta.name}: {e}")
            return None

        if latest is None:
            return None

        update_available = self._version_is_newer(latest, meta.version)

        return UpdateInfo(
            plugin_name=meta.name,
            current_version=meta.version,
            latest_version=latest,
            update_available=update_available,
            changelog=changelog,
            download_url=download_url,
        )

    def _check_github_release(
        self, repo: str, plugin_name: str
    ) -> tuple[str, Optional[str], Optional[str]]:
        """
        Query GitHub API for the latest release.

        Returns:
            (version, download_url, changelog)
        """
        url = f"{GITHUB_API}/repos/{repo}/releases/latest"
        try:
            response = httpx.get(url, timeout=10, follow_redirects=True)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise RuntimeError(f"GitHub API error: {e}") from e

        version = data.get("tag_name", "").lstrip("v")
        changelog = data.get("body", "")[:500] if data.get("body") else None

        # Find the most suitable asset for linux amd64
        download_url = None
        for asset in data.get("assets", []):
            name = asset.get("name", "").lower()
            if "linux" in name and ("amd64" in name or "x86_64" in name):
                download_url = asset.get("browser_download_url")
                break

        return version, download_url, changelog

    def _check_apt_version(self, package_name: str) -> Optional[str]:
        """Check latest available apt package version."""
        code, out, _ = run_command(
            f"apt-cache policy {package_name} | grep Candidate | awk '{{print $2}}'",
            timeout=15,
        )
        return out.strip() if code == 0 and out.strip() else None

    def _check_url_version(self, url: str) -> Optional[str]:
        """Check version from a URL (expects plain version string)."""
        try:
            response = httpx.get(url, timeout=10)
            return response.text.strip()
        except Exception:
            return None

    @staticmethod
    def _version_is_newer(latest: str, current: str) -> bool:
        """Compare semver strings. Returns True if latest > current."""
        try:

            def parse(v: str):
                # Extract numeric parts only
                nums = re.findall(r"\d+", v)
                return tuple(int(n) for n in nums[:3])

            return parse(latest) > parse(current)
        except Exception:
            return latest != current

    # ── Apply Updates ────────────────────────

    def update_plugin(self, plugin_name: str) -> Result:
        """
        Update a specific plugin to its latest version.

        Args:
            plugin_name: Plugin name

        Returns:
            Result indicating success or failure
        """
        plugin = self.pm.get(plugin_name)
        if plugin is None:
            return Result.fail(f"Plugin not found: {plugin_name}")

        logger.info(f"Updating {plugin_name} from {plugin.meta.version}...")

        # Stop service before update
        was_running = plugin.is_running()
        if was_running:
            plugin.stop()

        # Run plugin's update logic
        result = plugin.update()

        # Restart if it was running
        if was_running and result.success:
            plugin.start()

        if result.success:
            logger.info(f"Successfully updated {plugin_name}")
        else:
            logger.error(f"Update failed for {plugin_name}: {result.error}")
            # Restart even on failure to restore service
            if was_running:
                plugin.start()

        return result

    def update_all(self) -> Dict[str, Result]:
        """
        Update all plugins that have updates available.

        Returns:
            Dict mapping plugin name to Result
        """
        updates = self.check_all()
        available = [u for u in updates if u.update_available]

        if not available:
            logger.info("All plugins are up to date")
            return {}

        results = {}
        for update_info in available:
            results[update_info.plugin_name] = self.update_plugin(update_info.plugin_name)

        return results

    def get_update_summary(self) -> Dict:
        """
        Get a summary of update status for all plugins.
        Suitable for display in the Telegram bot.

        Returns:
            Dict with update information
        """
        updates = self.check_all()
        summary = {
            "total_plugins": len(self.pm.all()),
            "up_to_date": 0,
            "updates_available": [],
            "check_failed": [],
        }

        checked_names = {u.plugin_name for u in updates}

        for plugin in self.pm.all():
            if plugin.meta.name not in checked_names:
                summary["check_failed"].append(plugin.meta.name)

        for update in updates:
            if update.update_available:
                summary["updates_available"].append(
                    {
                        "name": update.plugin_name,
                        "current": update.current_version,
                        "latest": update.latest_version,
                    }
                )
            else:
                summary["up_to_date"] += 1

        return summary
