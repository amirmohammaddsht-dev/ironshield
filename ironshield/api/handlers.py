"""
IronShield - API Handler Implementations
Path: ironshield/api/handlers.py
Purpose: Implements all API route handlers.
         Connects the API layer to Core Engines (ServiceManager, TunnelManager, etc.)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ironshield.core.benchmark_engine import BenchmarkEngine
from ironshield.core.config_engine import ConfigEngine
from ironshield.core.monitoring import MonitoringEngine
from ironshield.core.plugin_manager import PluginManager
from ironshield.core.service_manager import ServiceManager
from ironshield.core.smart_routing import SmartRoutingEngine
from ironshield.core.tunnel_manager import TunnelManager
from ironshield.db.database import Database
from ironshield.db.models import User
from ironshield.utils.logger import get_logger
from ironshield.version import __version__

logger = get_logger("api.handlers")


class APIHandlers:
    """
    All API request handlers.

    Each method corresponds to one or more API routes.
    Handlers receive keyword arguments from route params.
    Handlers return dicts that are JSON-serialized and sent back.
    """

    def __init__(
        self,
        plugin_manager: PluginManager,
        service_manager: ServiceManager,
        tunnel_manager: TunnelManager,
        benchmark_engine: BenchmarkEngine,
        routing_engine: SmartRoutingEngine,
        monitoring_engine: MonitoringEngine,
        config_engine: ConfigEngine,
        db: Database,
    ):
        self.pm = plugin_manager
        self.sm = service_manager
        self.tm = tunnel_manager
        self.be = benchmark_engine
        self.routing = routing_engine
        self.monitoring = monitoring_engine
        self.cfg = config_engine
        self.db = db

    # ── System ────────────────────────────────

    def health(self) -> Dict:
        """GET /health"""
        return {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": __version__,
        }

    def status(self) -> Dict:
        """GET /status — full system snapshot"""
        dashboard = self.monitoring.get_dashboard_data()
        routing = self.routing.get_status()
        tunnels = self.tm.get_tunnel_summary()
        return {
            "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "dashboard": dashboard,
            "routing": routing,
            "tunnels": tunnels,
        }

    def version(self) -> Dict:
        """GET /version"""
        return {"version": __version__}

    # ── Plugins ───────────────────────────────

    def list_plugins(self) -> Dict:
        """GET /plugins"""
        return {"plugins": self.sm.status_all()}

    def get_plugin(self, name: str) -> Dict:
        """GET /plugins/{name}"""
        plugin = self.pm.get(name)
        if plugin is None:
            return {"error": f"Plugin not found: {name}"}
        return {
            "name": name,
            "display_name": plugin.meta.display_name,
            "version": plugin.meta.version,
            "status": plugin.status().value,
            "category": plugin.meta.category.value,
            "priority": plugin.meta.priority,
            "config": plugin.get_config(),
        }

    def start_plugin(self, name: str) -> Dict:
        """POST /plugins/{name}/start"""
        result = self.sm.start(name, performed_by="api")
        return {"success": result.success, "message": result.message, "error": result.error}

    def stop_plugin(self, name: str) -> Dict:
        """POST /plugins/{name}/stop"""
        result = self.sm.stop(name, performed_by="api")
        return {"success": result.success, "message": result.message, "error": result.error}

    def restart_plugin(self, name: str) -> Dict:
        """POST /plugins/{name}/restart"""
        result = self.sm.restart(name, performed_by="api")
        return {"success": result.success, "message": result.message, "error": result.error}

    def get_plugin_logs(self, name: str, lines: int = 100) -> Dict:
        """GET /plugins/{name}/logs"""
        logs = self.sm.get_logs(name, int(lines))
        return {"plugin": name, "lines": logs}

    def update_plugin(self, name: str) -> Dict:
        """POST /plugins/{name}/update"""
        result = self.sm.update_plugin(name, performed_by="api")
        return {"success": result.success, "message": result.message, "error": result.error}

    # ── Tunnels ───────────────────────────────

    def list_tunnels(self) -> Dict:
        """GET /tunnels"""
        return self.tm.get_tunnel_summary()

    def ranked_tunnels(self) -> Dict:
        """GET /tunnels/ranked"""
        return {"tunnels": self.tm.get_ranked_tunnels()}

    def switch_tunnel(self, name: str) -> Dict:
        """POST /tunnels/switch/{name}"""
        success = self.routing.set_manual_override(name)
        return {
            "success": success,
            "message": f"Switched to {name}" if success else f"Tunnel not found: {name}",
        }

    def clear_override(self) -> Dict:
        """DELETE /tunnels/override"""
        self.routing.clear_manual_override()
        return {"success": True, "message": "Auto-routing restored"}

    # ── Benchmark ─────────────────────────────

    async def benchmark_quick(self) -> Dict:
        """POST /benchmark/quick"""
        results = await self.be.run_quick()
        return {
            "type": "quick",
            "results": {
                name: {
                    "success": r.success,
                    "latency_ms": r.latency_ms,
                    "score": r.score,
                    "error": r.error,
                }
                for name, r in results.items()
            },
        }

    async def benchmark_full(self) -> Dict:
        """POST /benchmark/full"""
        results = await self.be.run_full()
        return {
            "type": "full",
            "results": {
                name: {
                    "success": r.success,
                    "latency_ms": r.latency_ms,
                    "real_delay_ms": r.real_delay_ms,
                    "packet_loss_percent": r.packet_loss_percent,
                    "throughput_mbps": r.throughput_mbps,
                    "score": r.score,
                    "error": r.error,
                }
                for name, r in results.items()
            },
        }

    async def benchmark_single(self, name: str) -> Dict:
        """POST /benchmark/{name}"""
        result = await self.be.run_single(name, test_type="full")
        if result is None:
            return {"error": f"Plugin not found: {name}"}
        return {
            "plugin": name,
            "success": result.success,
            "latency_ms": result.latency_ms,
            "real_delay_ms": result.real_delay_ms,
            "packet_loss_percent": result.packet_loss_percent,
            "throughput_mbps": result.throughput_mbps,
            "score": result.score,
            "error": result.error,
        }

    # ── Users ─────────────────────────────────

    def list_users(self) -> Dict:
        """GET /users"""
        try:
            with self.db.session() as s:
                users = s.query(User).all()
                return {
                    "users": [
                        {
                            "id": u.id,
                            "username": u.username,
                            "is_active": u.is_active,
                            "traffic_used_gb": round(u.traffic_used_gb, 2),
                            "traffic_limit_gb": u.traffic_limit_gb,
                            "traffic_remaining_gb": (
                                round(u.traffic_remaining_gb, 2)
                                if u.traffic_remaining_gb is not None
                                else None
                            ),
                            "expire_at": (u.expire_at.isoformat() if u.expire_at else None),
                            "days_until_expiry": u.days_until_expiry,
                            "last_connected_at": (
                                u.last_connected_at.isoformat() if u.last_connected_at else None
                            ),
                        }
                        for u in users
                    ]
                }
        except Exception as e:
            return {"error": str(e)}

    def create_user(
        self,
        username: str,
        traffic_limit_gb: Optional[float] = None,
        expire_days: int = 30,
    ) -> Dict:
        """POST /users"""
        from datetime import timedelta
        from ironshield.utils.validators import is_valid_username

        if not is_valid_username(username):
            return {"error": f"Invalid username: {username}"}

        # Check duplicate
        with self.db.session() as s:
            existing = s.query(User).filter_by(username=username).first()
            if existing:
                return {"error": f"User already exists: {username}"}

        # Create OpenVPN certificate
        openvpn = self.pm.get("openvpn")
        if openvpn is None:
            return {"error": "OpenVPN plugin not loaded"}

        result = openvpn.add_user(username=username, expire_days=expire_days)
        if not result.success:
            return {"error": result.error}

        # Save to DB
        try:
            with self.db.session() as s:
                traffic_bytes = int(traffic_limit_gb * (1024**3)) if traffic_limit_gb else None
                user = User(
                    username=username,
                    traffic_limit_bytes=traffic_bytes,
                    expire_at=(datetime.now(timezone.utc) + timedelta(days=expire_days)),
                )
                s.add(user)
                s.flush()
                user_id = user.id

            return {
                "success": True,
                "user_id": user_id,
                "username": username,
                "ovpn_content": result.data.get("ovpn_content"),
                "expires_at": result.data.get("expires_at"),
            }
        except Exception as e:
            return {"error": str(e)}

    def get_user(self, username: str) -> Dict:
        """GET /users/{username}"""
        try:
            with self.db.session() as s:
                user = s.query(User).filter_by(username=username).first()
                if user is None:
                    return {"error": f"User not found: {username}"}
                return {
                    "id": user.id,
                    "username": user.username,
                    "is_active": user.is_active,
                    "is_blocked": user.is_blocked,
                    "traffic_used_gb": round(user.traffic_used_gb, 2),
                    "traffic_limit_gb": user.traffic_limit_gb,
                    "traffic_remaining_gb": (
                        round(user.traffic_remaining_gb, 2)
                        if user.traffic_remaining_gb is not None
                        else None
                    ),
                    "traffic_percent": user.traffic_percent_used,
                    "expire_at": (user.expire_at.isoformat() if user.expire_at else None),
                    "days_until_expiry": user.days_until_expiry,
                    "is_expired": user.is_expired,
                    "is_over_quota": user.is_over_quota,
                    "last_connected_at": (
                        user.last_connected_at.isoformat() if user.last_connected_at else None
                    ),
                    "created_at": user.created_at.isoformat(),
                }
        except Exception as e:
            return {"error": str(e)}

    def delete_user(self, username: str) -> Dict:
        """DELETE /users/{username}"""
        openvpn = self.pm.get("openvpn")
        if openvpn:
            openvpn.remove_user(username)

        try:
            with self.db.session() as s:
                user = s.query(User).filter_by(username=username).first()
                if user:
                    s.delete(user)
            return {"success": True, "message": f"User {username} deleted"}
        except Exception as e:
            return {"error": str(e)}

    def toggle_user(self, username: str) -> Dict:
        """POST /users/{username}/toggle"""
        try:
            with self.db.session() as s:
                user = s.query(User).filter_by(username=username).first()
                if user is None:
                    return {"error": f"User not found: {username}"}
                user.is_active = not user.is_active
                new_status = user.is_active
            return {
                "success": True,
                "username": username,
                "is_active": new_status,
            }
        except Exception as e:
            return {"error": str(e)}

    def get_user_config(self, username: str) -> Dict:
        """GET /users/{username}/config"""
        openvpn = self.pm.get("openvpn")
        if openvpn is None:
            return {"error": "OpenVPN not available"}
        config = openvpn.get_user_config(username)
        if config is None:
            return {"error": f"Config not found for: {username}"}
        return {"username": username, "ovpn_content": config}

    # ── Metrics ───────────────────────────────

    def get_metrics(self) -> Dict:
        """GET /metrics"""
        return self.monitoring.get_dashboard_data()

    def server_metrics(self) -> Dict:
        """GET /metrics/server"""
        metrics = self.monitoring._get_latest_system_metrics()
        return metrics or {"error": "No metrics available yet"}

    # ── Config ────────────────────────────────

    def get_config(self) -> Dict:
        """GET /config"""
        cfg = self.cfg.get_all()
        # Remove sensitive values
        safe_cfg = dict(cfg)
        if "telegram" in safe_cfg:
            safe_cfg["telegram"] = dict(safe_cfg["telegram"])
            safe_cfg["telegram"]["token"] = "***" if safe_cfg["telegram"].get("token") else ""
        return safe_cfg

    def update_config(self, updates: Dict[str, Any]) -> Dict:
        """PATCH /config"""
        errors = []
        for key, value in updates.items():
            self.cfg.set(key, value, performed_by="api")
        if errors:
            return {"success": False, "errors": errors}
        return {"success": True, "updated": list(updates.keys())}

    # ── Routing ───────────────────────────────

    def routing_status(self) -> Dict:
        """GET /routing"""
        return self.routing.get_status()

    def routing_history(self) -> Dict:
        """GET /routing/history"""
        return {"decisions": self.routing.get_recent_decisions()}

    # ── Agent / Ping ──────────────────────────

    def agent_health(self) -> Dict:
        """GET /agent/health — foreign server agent health"""
        return self.health()

    def agent_metrics(self) -> Dict:
        """GET /agent/metrics — foreign server metrics"""
        return self.server_metrics()

    def ping_endpoint(self, **kwargs) -> Dict:
        """POST /ping — used for real-delay measurement"""
        return {"pong": True, "timestamp": datetime.now(timezone.utc).isoformat()}

    # ── Handler Map ───────────────────────────

    def get_handler_map(self) -> Dict:
        """Return all handlers mapped by route string for APIServer registration."""
        return {
            "GET /health": self.health,
            "GET /status": self.status,
            "GET /version": self.version,
            "GET /plugins": self.list_plugins,
            "GET /plugins/{name}": self.get_plugin,
            "POST /plugins/{name}/start": self.start_plugin,
            "POST /plugins/{name}/stop": self.stop_plugin,
            "POST /plugins/{name}/restart": self.restart_plugin,
            "GET /plugins/{name}/logs": self.get_plugin_logs,
            "POST /plugins/{name}/update": self.update_plugin,
            "GET /tunnels": self.list_tunnels,
            "GET /tunnels/ranked": self.ranked_tunnels,
            "POST /tunnels/switch/{name}": self.switch_tunnel,
            "DELETE /tunnels/override": self.clear_override,
            "POST /benchmark/quick": self.benchmark_quick,
            "POST /benchmark/full": self.benchmark_full,
            "POST /benchmark/{name}": self.benchmark_single,
            "GET /users": self.list_users,
            "POST /users": self.create_user,
            "GET /users/{username}": self.get_user,
            "DELETE /users/{username}": self.delete_user,
            "POST /users/{username}/toggle": self.toggle_user,
            "GET /users/{username}/config": self.get_user_config,
            "GET /metrics": self.get_metrics,
            "GET /metrics/server": self.server_metrics,
            "GET /config": self.get_config,
            "PATCH /config": self.update_config,
            "GET /routing": self.routing_status,
            "GET /routing/history": self.routing_history,
            "GET /agent/health": self.agent_health,
            "GET /agent/metrics": self.agent_metrics,
            "POST /ping": self.ping_endpoint,
        }
