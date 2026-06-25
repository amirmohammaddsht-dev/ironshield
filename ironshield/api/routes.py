"""
IronShield - Internal API Routes
Path: ironshield/api/routes.py
Purpose: Defines all API endpoints for CLI ↔ Core and Iran ↔ Foreign communication.
         Used by both the Unix Socket server (local) and HTTP server (over tunnel).
"""

from __future__ import annotations

from typing import Dict


class Route:
    """A single API route definition."""

    def __init__(self, method: str, path: str, handler: str, description: str = ""):
        self.method = method.upper()
        self.path = path
        self.handler = handler
        self.description = description

    def __repr__(self) -> str:
        return f"<Route {self.method} {self.path}>"


# ── Route Registry ────────────────────────────

ROUTES: Dict[str, Dict[str, Route]] = {
    # System
    "GET /health": Route("GET", "/health", "health", "Health check ping"),
    "GET /status": Route("GET", "/status", "status", "Full system status"),
    "GET /version": Route("GET", "/version", "version", "IronShield version"),
    # Plugins
    "GET /plugins": Route("GET", "/plugins", "list_plugins", "List all plugins"),
    "GET /plugins/{name}": Route("GET", "/plugins/{name}", "get_plugin", "Plugin details"),
    "POST /plugins/{name}/start": Route(
        "POST", "/plugins/{name}/start", "start_plugin", "Start a plugin"
    ),
    "POST /plugins/{name}/stop": Route(
        "POST", "/plugins/{name}/stop", "stop_plugin", "Stop a plugin"
    ),
    "POST /plugins/{name}/restart": Route(
        "POST", "/plugins/{name}/restart", "restart_plugin", "Restart a plugin"
    ),
    "GET /plugins/{name}/logs": Route(
        "GET", "/plugins/{name}/logs", "get_plugin_logs", "Get plugin logs"
    ),
    "POST /plugins/{name}/update": Route(
        "POST", "/plugins/{name}/update", "update_plugin", "Update a plugin"
    ),
    # Tunnels
    "GET /tunnels": Route("GET", "/tunnels", "list_tunnels", "List tunnel status"),
    "GET /tunnels/ranked": Route("GET", "/tunnels/ranked", "ranked_tunnels", "Ranked tunnel list"),
    "POST /tunnels/switch/{name}": Route(
        "POST", "/tunnels/switch/{name}", "switch_tunnel", "Manual tunnel switch"
    ),
    "DELETE /tunnels/override": Route(
        "DELETE", "/tunnels/override", "clear_override", "Clear manual override"
    ),
    # Benchmark
    "POST /benchmark/quick": Route(
        "POST", "/benchmark/quick", "benchmark_quick", "Quick benchmark"
    ),
    "POST /benchmark/full": Route("POST", "/benchmark/full", "benchmark_full", "Full benchmark"),
    "POST /benchmark/{name}": Route(
        "POST", "/benchmark/{name}", "benchmark_single", "Benchmark one tunnel"
    ),
    # Users (OpenVPN)
    "GET /users": Route("GET", "/users", "list_users", "List VPN users"),
    "POST /users": Route("POST", "/users", "create_user", "Create VPN user"),
    "GET /users/{username}": Route("GET", "/users/{username}", "get_user", "Get user details"),
    "DELETE /users/{username}": Route(
        "DELETE", "/users/{username}", "delete_user", "Delete/revoke user"
    ),
    "POST /users/{username}/toggle": Route(
        "POST", "/users/{username}/toggle", "toggle_user", "Enable/disable user"
    ),
    "GET /users/{username}/config": Route(
        "GET", "/users/{username}/config", "get_user_config", "Get .ovpn config"
    ),
    # Monitoring
    "GET /metrics": Route("GET", "/metrics", "get_metrics", "System metrics"),
    "GET /metrics/server": Route(
        "GET", "/metrics/server", "server_metrics", "Server resource metrics"
    ),
    # Config
    "GET /config": Route("GET", "/config", "get_config", "Get current config"),
    "PATCH /config": Route("PATCH", "/config", "update_config", "Update config values"),
    # Routing
    "GET /routing": Route("GET", "/routing", "routing_status", "Smart routing status"),
    "GET /routing/history": Route(
        "GET", "/routing/history", "routing_history", "Routing decision history"
    ),
    # Agent (foreign server only)
    "GET /agent/health": Route("GET", "/agent/health", "agent_health", "Agent health"),
    "GET /agent/metrics": Route("GET", "/agent/metrics", "agent_metrics", "Agent metrics"),
    "POST /ping": Route("POST", "/ping", "ping_endpoint", "Real-delay ping endpoint"),
}
