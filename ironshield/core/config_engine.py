"""
IronShield - Configuration Engine
Path: ironshield/core/config_engine.py
Purpose: Load, validate, apply, and rollback YAML configuration files.
         Generates service configs from Jinja2 templates.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ironshield.utils.logger import get_logger

logger = get_logger("config_engine")

CONFIG_ROOT = Path("/opt/ironshield/configs")
TEMPLATES_ROOT = Path(__file__).parent.parent.parent / "configs" / "templates"
BACKUP_DIR = CONFIG_ROOT / "backups"
HISTORY_FILE = CONFIG_ROOT / "history.yaml"
MAIN_CONFIG = CONFIG_ROOT / "main.yaml"

DEFAULT_CONFIG: Dict[str, Any] = {
    "ironshield": {
        "version": "1.0.0",
        "role": "iran",
        "language": "fa",
    },
    "server": {
        "iran": {"ip": "", "hostname": ""},
        "foreign": {"ip": "", "hostname": ""},
    },
    "security": {
        "user": "ironshield",
        "keys_dir": "/opt/ironshield/keys",
        "db_encryption": True,
    },
    "ufw": {
        "enabled": True,
        "default_incoming": "deny",
        "default_outgoing": "allow",
        "ssh_port": 22,
    },
    "openvpn": {
        "enabled": True,
        "port": 443,
        "port_fallback": 80,
        "protocol": "tcp",
        "network": "10.8.0.0",
        "netmask": "255.255.255.0",
        "cipher": "AES-256-GCM",
        "max_clients": 100,
        "dns_primary": "1.1.1.1",
        "dns_secondary": "8.8.8.8",
    },
    "tunnels": {
        "phormal": {"enabled": True, "mode": "relay", "port": 8531},
        "gost": {"enabled": True, "local_port": 8080, "remote_port": 8080},
        "frp": {"enabled": True, "server_port": 7000},
        "backhaul": {"enabled": True, "local_port": 3080},
        "vxlan": {"enabled": True, "vni": 100, "port": 4789},
        "storm_dns": {"enabled": True, "role": "client"},
    },
    "telegram": {
        "token": "",
        "admin_ids": [],
        "proxy": {"enabled": True, "type": "socks5", "host": "127.0.0.1", "port": 18000},
    },
    "benchmark": {
        "targets": {
            "default": "google_dns",
            "list": [
                {"name": "google_dns", "host": "8.8.8.8", "protocol": "icmp"},
                {"name": "cloudflare_dns", "host": "1.1.1.1", "protocol": "icmp"},
            ],
        },
        "schedule": {
            "quick_interval_minutes": 5,
            "standard_interval_minutes": 30,
            "full_interval_hours": 6,
        },
        "scoring": {
            "latency_weight": 0.25,
            "real_delay_weight": 0.30,
            "loss_weight": 0.30,
            "throughput_weight": 0.15,
        },
    },
    "routing": {
        "mode": "auto",
        "cooldown_minutes": 10,
        "min_score_difference": 10,
        "stability_bonus": 5,
        "consecutive_failures": 3,
        "pattern_learning": True,
    },
}


class ConfigEngine:
    """
    Manages all IronShield configuration.

    Responsibilities:
    - Load config from YAML files
    - Merge with defaults
    - Validate values
    - Generate service configs from Jinja2 templates
    - Track change history
    - Rollback to previous config
    """

    def __init__(
        self,
        config_root: Optional[Path] = None,
        templates_root: Optional[Path] = None,
    ):
        self._root = config_root or CONFIG_ROOT
        self._templates_root = templates_root or TEMPLATES_ROOT
        self._config: Dict[str, Any] = {}
        self._jinja = self._setup_jinja()

    def _setup_jinja(self) -> Environment:
        """Set up Jinja2 environment for template rendering."""
        if self._templates_root.exists():
            return Environment(
                loader=FileSystemLoader(str(self._templates_root)),
                undefined=StrictUndefined,
                trim_blocks=True,
                lstrip_blocks=True,
            )
        # Fallback: no templates directory (testing)
        return Environment(undefined=StrictUndefined)

    # ── Load & Save ──────────────────────────

    def load(self) -> Dict[str, Any]:
        """
        Load configuration from disk and merge with defaults.

        Returns:
            Merged configuration dict
        """
        main_cfg_path = self._root / "main.yaml"

        if main_cfg_path.exists():
            try:
                with open(main_cfg_path) as f:
                    file_cfg = yaml.safe_load(f) or {}
                self._config = self._deep_merge(DEFAULT_CONFIG, file_cfg)
                logger.info(f"Configuration loaded from {main_cfg_path}")
            except yaml.YAMLError as e:
                logger.error(f"Failed to parse config file: {e}")
                logger.warning("Falling back to default configuration")
                self._config = dict(DEFAULT_CONFIG)
        else:
            logger.info("No config file found — using defaults")
            self._config = dict(DEFAULT_CONFIG)

        return self._config

    def save(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """
        Save configuration to disk.

        Args:
            config: Config to save (uses current if None)

        Returns:
            bool: True on success
        """
        cfg = config or self._config
        main_cfg_path = self._root / "main.yaml"

        try:
            main_cfg_path.parent.mkdir(parents=True, exist_ok=True)
            with open(main_cfg_path, "w") as f:
                yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, indent=2)
            logger.info("Configuration saved")
            return True
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return False

    def init_default(self, role: str, iran_ip: str = "", foreign_ip: str = "") -> bool:
        """
        Write default configuration for a fresh installation.

        Args:
            role: Server role ('iran' or 'foreign')
            iran_ip: Iran server IP address
            foreign_ip: Foreign server IP address

        Returns:
            bool: True on success
        """
        cfg = dict(DEFAULT_CONFIG)
        cfg["ironshield"]["role"] = role
        cfg["server"]["iran"]["ip"] = iran_ip
        cfg["server"]["foreign"]["ip"] = foreign_ip

        self._config = cfg
        return self.save()

    # ── Get & Set ────────────────────────────

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get a config value by dot-notation path.

        Args:
            key_path: Dot-separated path like 'openvpn.port'
            default: Default if key not found

        Returns:
            Config value
        """
        parts = key_path.split(".")
        current = self._config
        for part in parts:
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def set(
        self,
        key_path: str,
        value: Any,
        performed_by: str = "system",
        notes: str = "",
    ) -> bool:
        """
        Set a config value and record in history.

        Args:
            key_path: Dot-separated path like 'openvpn.port'
            value: New value
            performed_by: Who made the change (for audit)
            notes: Optional notes about the change

        Returns:
            bool: True on success
        """
        old_value = self.get(key_path)

        # Set value in nested dict
        parts = key_path.split(".")
        current = self._config
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value

        # Record in history
        self._record_change(
            key_path=key_path,
            old_value=old_value,
            new_value=value,
            performed_by=performed_by,
            notes=notes,
        )

        return self.save()

    def get_all(self) -> Dict[str, Any]:
        """Return full configuration dict."""
        return dict(self._config)

    # ── Validation ───────────────────────────

    def validate(self, config: Optional[Dict[str, Any]] = None) -> List[str]:
        """
        Validate configuration values.

        Args:
            config: Config to validate (uses current if None)

        Returns:
            List of error messages (empty = valid)
        """
        cfg = config or self._config
        errors = []

        # Role
        role = cfg.get("ironshield", {}).get("role", "")
        if role not in ("iran", "foreign"):
            errors.append(f"Invalid role: '{role}' — must be 'iran' or 'foreign'")

        # OpenVPN port
        port = cfg.get("openvpn", {}).get("port", 0)
        if not (1 <= int(port) <= 65535):
            errors.append(f"Invalid OpenVPN port: {port}")

        # Telegram token (if provided)
        token = cfg.get("telegram", {}).get("token", "")
        if token and not self._validate_telegram_token(token):
            errors.append("Invalid Telegram bot token format")

        # Server IPs
        iran_ip = cfg.get("server", {}).get("iran", {}).get("ip", "")
        foreign_ip = cfg.get("server", {}).get("foreign", {}).get("ip", "")
        if role == "iran" and not foreign_ip:
            errors.append("Foreign server IP is required on Iran server")
        if role == "foreign" and not iran_ip:
            errors.append("Iran server IP is required on foreign server")

        # Benchmark weights must sum to 1.0
        scoring = cfg.get("benchmark", {}).get("scoring", {})
        if scoring:
            total = sum(
                [
                    scoring.get("latency_weight", 0),
                    scoring.get("real_delay_weight", 0),
                    scoring.get("loss_weight", 0),
                    scoring.get("throughput_weight", 0),
                ]
            )
            if abs(total - 1.0) > 0.01:
                errors.append(f"Benchmark scoring weights must sum to 1.0 (got {total:.2f})")

        return errors

    @staticmethod
    def _validate_telegram_token(token: str) -> bool:
        """Basic Telegram token format check."""
        import re

        return bool(re.match(r"^\d+:[A-Za-z0-9_-]{30,}$", token.strip()))

    # ── Template Rendering ───────────────────

    def render_template(self, template_name: str, context: Dict[str, Any]) -> str:
        """
        Render a Jinja2 template with given context.

        Args:
            template_name: Relative path under configs/templates/
            context: Template variables

        Returns:
            Rendered string
        """
        try:
            template = self._jinja.get_template(template_name)
            return template.render(**context, **self._config)
        except Exception as e:
            logger.error(f"Template render error [{template_name}]: {e}")
            raise

    def generate_openvpn_server_config(self) -> str:
        """Generate OpenVPN server.conf from current config."""
        return self.render_template(
            "openvpn/server.conf.j2",
            {"openvpn": self.get("openvpn"), "security": self.get("security")},
        )

    def generate_openvpn_client_config(self, username: str, certs: Dict[str, str]) -> str:
        """Generate .ovpn client config file."""
        return self.render_template(
            "openvpn/client.conf.j2",
            {
                "username": username,
                "openvpn": self.get("openvpn"),
                "server": self.get("server"),
                **certs,
            },
        )

    # ── Backup & Rollback ────────────────────

    def backup(self, label: str = "") -> Optional[Path]:
        """
        Create a timestamped backup of current config.

        Args:
            label: Optional label for the backup

        Returns:
            Path to backup file, or None on failure
        """
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            suffix = f"_{label}" if label else ""
            backup_path = BACKUP_DIR / f"main_{ts}{suffix}.yaml"
            shutil.copy2(self._root / "main.yaml", backup_path)
            logger.info(f"Config backed up: {backup_path}")
            return backup_path
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            return None

    def rollback(self, backup_path: Optional[Path] = None) -> bool:
        """
        Restore configuration from a backup.

        Args:
            backup_path: Specific backup to restore (uses latest if None)

        Returns:
            bool: True on success
        """
        if backup_path is None:
            backup_path = self._get_latest_backup()

        if backup_path is None or not backup_path.exists():
            logger.error("No backup found to rollback to")
            return False

        try:
            shutil.copy2(backup_path, self._root / "main.yaml")
            self.load()
            logger.info(f"Config rolled back from: {backup_path}")
            return True
        except Exception as e:
            logger.error(f"Rollback failed: {e}")
            return False

    def list_backups(self) -> List[Path]:
        """List all available config backups."""
        if not BACKUP_DIR.exists():
            return []
        return sorted(BACKUP_DIR.glob("main_*.yaml"), reverse=True)

    def _get_latest_backup(self) -> Optional[Path]:
        """Get the most recent backup file."""
        backups = self.list_backups()
        return backups[0] if backups else None

    # ── History ──────────────────────────────

    def _record_change(
        self,
        key_path: str,
        old_value: Any,
        new_value: Any,
        performed_by: str,
        notes: str = "",
    ) -> None:
        """Append a change record to history file."""
        history_path = self._root / "history.yaml"
        try:
            history = []
            if history_path.exists():
                with open(history_path) as f:
                    data = yaml.safe_load(f) or {}
                history = data.get("changes", [])

            history.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "key": key_path,
                    "old_value": str(old_value),
                    "new_value": str(new_value),
                    "performed_by": performed_by,
                    "notes": notes,
                    "status": "success",
                }
            )

            # Keep only last 100 changes
            history = history[-100:]

            with open(history_path, "w") as f:
                yaml.dump({"changes": history}, f, default_flow_style=False, allow_unicode=True)
        except Exception as e:
            logger.warning(f"Failed to record config change history: {e}")

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent config change history."""
        history_path = self._root / "history.yaml"
        if not history_path.exists():
            return []
        try:
            with open(history_path) as f:
                data = yaml.safe_load(f) or {}
            changes = data.get("changes", [])
            return list(reversed(changes[-limit:]))
        except Exception:
            return []

    # ── Helpers ──────────────────────────────

    @staticmethod
    def _deep_merge(base: Dict, override: Dict) -> Dict:
        """
        Deep merge two dicts. Override values take precedence.
        Nested dicts are merged recursively.
        """
        result = dict(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = ConfigEngine._deep_merge(result[key], value)
            else:
                result[key] = value
        return result


# ── Singleton ────────────────────────────────

_config_engine: Optional[ConfigEngine] = None


def get_config_engine() -> ConfigEngine:
    """Get the global ConfigEngine singleton."""
    global _config_engine
    if _config_engine is None:
        _config_engine = ConfigEngine()
        _config_engine.load()
    return _config_engine
