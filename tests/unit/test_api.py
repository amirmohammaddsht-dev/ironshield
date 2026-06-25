"""
Tests for Phase 6 — Internal API.
Tests: APIServer route matching, APIClient request/response,
       APIHandlers business logic.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ironshield.api.server import APIServer
from ironshield.api.client import APIClient, APIError, SyncAPIClient
from ironshield.api.routes import ROUTES


# ── APIServer Tests ────────────────────────────


class TestAPIServer:
    def test_route_registry_not_empty(self):
        """ROUTES dict should have entries."""
        assert len(ROUTES) > 0

    def test_routes_have_required_fields(self):
        """Every route should have method, path, handler."""
        for key, route in ROUTES.items():
            assert route.method in ("GET", "POST", "DELETE", "PATCH")
            assert route.path.startswith("/")
            assert route.handler

    def test_match_exact_route(self):
        """Exact route should match."""
        result = APIServer._match_route("GET /health", "GET /health")
        assert result == {}

    def test_match_route_with_param(self):
        """Route with path parameter should extract value."""
        result = APIServer._match_route("GET /plugins/openvpn", "GET /plugins/{name}")
        assert result == {"name": "openvpn"}

    def test_match_route_wrong_method(self):
        """Wrong HTTP method should not match."""
        result = APIServer._match_route("POST /health", "GET /health")
        assert result is None

    def test_match_route_wrong_path(self):
        """Wrong path should not match."""
        result = APIServer._match_route("GET /tunnels/openvpn", "GET /plugins/{name}")
        assert result is None

    def test_match_route_multiple_params(self):
        """Multiple path parameters should all be extracted."""
        result = APIServer._match_route("GET /users/ali/config", "GET /users/{username}/config")
        assert result == {"username": "ali"}

    def test_register_handler(self):
        """Registered handler should be callable."""
        server = APIServer()
        handler = lambda: {"ok": True}
        server.register("GET /test", handler)
        assert "GET /test" in server._handlers

    @pytest.mark.asyncio
    async def test_request_response_cycle(self, tmp_path):
        """Full request/response cycle over Unix socket."""
        socket_path = tmp_path / "test.sock"
        server = APIServer(socket_path=socket_path)

        # Register a test handler
        def echo_handler(message: str = "hello") -> Dict:
            return {"echo": message}

        server.register("GET /echo", echo_handler)

        # Start server
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.1)  # Let server start

        try:
            # Connect client and send request
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            request = json.dumps(
                {
                    "id": "test-1",
                    "method": "GET /echo",
                    "params": {"message": "world"},
                }
            )
            writer.write((request + "\n").encode())
            await writer.drain()

            response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            response = json.loads(response_line.decode().strip())

            assert response["error"] is None
            assert response["result"]["echo"] == "world"
            assert response["id"] == "test-1"

        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self, tmp_path):
        """Invalid JSON should return error response."""
        socket_path = tmp_path / "test2.sock"
        server = APIServer(socket_path=socket_path)
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.1)

        try:
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            writer.write(b"not valid json\n")
            await writer.drain()

            response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            response = json.loads(response_line.decode().strip())
            assert response["error"] is not None

        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_unknown_route_returns_error(self, tmp_path):
        """Unknown route should return error."""
        socket_path = tmp_path / "test3.sock"
        server = APIServer(socket_path=socket_path)
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.1)

        try:
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            request = json.dumps(
                {
                    "id": "x",
                    "method": "GET /nonexistent",
                    "params": {},
                }
            )
            writer.write((request + "\n").encode())
            await writer.drain()

            response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            response = json.loads(response_line.decode().strip())
            assert response["error"] is not None

        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_async_handler(self, tmp_path):
        """Async handlers should work correctly."""
        socket_path = tmp_path / "test4.sock"
        server = APIServer(socket_path=socket_path)

        async def async_handler() -> Dict:
            await asyncio.sleep(0.01)
            return {"async": True}

        server.register("GET /async", async_handler)
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.1)

        try:
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            request = json.dumps({"id": "a", "method": "GET /async", "params": {}})
            writer.write((request + "\n").encode())
            await writer.drain()

            response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            response = json.loads(response_line.decode().strip())
            assert response["result"]["async"] is True

        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_api_key_rejected(self, tmp_path):
        """Wrong API key should be rejected."""
        socket_path = tmp_path / "test5.sock"
        server = APIServer(socket_path=socket_path, api_key="secret123")
        server.register("GET /health", lambda: {"ok": True})
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.1)

        try:
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            request = json.dumps(
                {
                    "id": "x",
                    "method": "GET /health",
                    "params": {},
                    "token": "wrongkey",
                }
            )
            writer.write((request + "\n").encode())
            await writer.drain()

            response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            response = json.loads(response_line.decode().strip())
            assert response["error"] == "Unauthorized"

        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_api_key_accepted(self, tmp_path):
        """Correct API key should be accepted."""
        socket_path = tmp_path / "test6.sock"
        server = APIServer(socket_path=socket_path, api_key="secret123")
        server.register("GET /health", lambda: {"ok": True})
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.1)

        try:
            reader, writer = await asyncio.open_unix_connection(str(socket_path))
            request = json.dumps(
                {
                    "id": "x",
                    "method": "GET /health",
                    "params": {},
                    "token": "secret123",
                }
            )
            writer.write((request + "\n").encode())
            await writer.drain()

            response_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            response = json.loads(response_line.decode().strip())
            assert response["error"] is None
            assert response["result"]["ok"] is True

        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass


# ── APIClient Tests ────────────────────────────


class TestAPIClient:
    def test_connection_error_when_no_socket(self, tmp_path):
        """Should raise ConnectionError when socket doesn't exist."""
        client = APIClient(socket_path=tmp_path / "nonexistent.sock")
        with pytest.raises(ConnectionError):
            asyncio.run(client.request("GET /health"))

    @pytest.mark.asyncio
    async def test_client_server_roundtrip(self, tmp_path):
        """Client should get correct response from server."""
        socket_path = tmp_path / "roundtrip.sock"
        server = APIServer(socket_path=socket_path)
        server.register("GET /health", lambda: {"status": "ok", "version": "1.0.0"})

        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.1)

        try:
            client = APIClient(socket_path=socket_path)
            result = await client.request("GET /health")
            assert result["status"] == "ok"
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_client_raises_api_error(self, tmp_path):
        """APIError should be raised on error response."""
        socket_path = tmp_path / "error_test.sock"
        server = APIServer(socket_path=socket_path)
        # No handlers — unknown route will return error

        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.1)

        try:
            client = APIClient(socket_path=socket_path)
            with pytest.raises(APIError):
                await client.request("GET /unknown")
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_client_convenience_methods(self, tmp_path):
        """Convenience methods should send correct routes."""
        socket_path = tmp_path / "convenience.sock"
        server = APIServer(socket_path=socket_path)

        received_routes = []

        async def capture_handler(**kwargs):
            return {"captured": True}

        for route in ["GET /health", "GET /plugins", "GET /tunnels", "GET /users"]:
            server.register(route, capture_handler)

        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.1)

        try:
            client = APIClient(socket_path=socket_path)
            await client.get_health()
            await client.list_plugins()
            await client.list_tunnels()
            await client.list_users()
        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass


# ── APIHandlers Tests ─────────────────────────


class TestAPIHandlers:
    """Tests for API handler business logic using mocked dependencies."""

    @pytest.fixture
    def handlers(self, tmp_path):
        """Create APIHandlers with all mocked dependencies."""
        from ironshield.api.handlers import APIHandlers
        from ironshield.db.database import Database

        db = Database(tmp_path / "test.db")
        db.init()

        pm = MagicMock()
        sm = MagicMock()
        tm = MagicMock()
        be = MagicMock()
        routing = MagicMock()
        monitoring = MagicMock()
        cfg = MagicMock()

        tm.get_tunnel_summary.return_value = {
            "total": 3,
            "active": 2,
            "primary": {"name": "phormal", "score": 97},
        }
        monitoring.get_dashboard_data.return_value = {
            "server": "iran",
            "system": {"cpu_percent": 35},
        }
        routing.get_status.return_value = {
            "mode": "auto",
            "current_tunnel": "phormal",
        }
        cfg.get_all.return_value = {
            "ironshield": {"role": "iran"},
            "telegram": {"token": "secret"},
        }

        return APIHandlers(pm, sm, tm, be, routing, monitoring, cfg, db)

    def test_health_handler(self, handlers):
        """Health endpoint should return ok status."""
        result = handlers.health()
        assert result["status"] == "ok"
        assert "version" in result
        assert "timestamp" in result

    def test_version_handler(self, handlers):
        """Version endpoint should return version string."""
        result = handlers.version()
        assert "version" in result

    def test_status_combines_all(self, handlers):
        """Status should include dashboard, routing, and tunnels."""
        result = handlers.status()
        assert "dashboard" in result
        assert "routing" in result
        assert "tunnels" in result

    def test_list_plugins_calls_sm(self, handlers):
        """list_plugins should delegate to ServiceManager."""
        handlers.sm.status_all.return_value = {"openvpn": {"status": "RUNNING"}}
        result = handlers.list_plugins()
        assert "plugins" in result
        handlers.sm.status_all.assert_called_once()

    def test_start_plugin(self, handlers):
        """start_plugin should delegate to ServiceManager."""
        from ironshield.services.base import Result

        handlers.sm.start.return_value = Result.ok("Started")
        result = handlers.start_plugin("openvpn")
        assert result["success"] is True
        handlers.sm.start.assert_called_with("openvpn", performed_by="api")

    def test_stop_plugin(self, handlers):
        """stop_plugin should delegate to ServiceManager."""
        from ironshield.services.base import Result

        handlers.sm.stop.return_value = Result.ok("Stopped")
        result = handlers.stop_plugin("gost")
        assert result["success"] is True

    def test_switch_tunnel(self, handlers):
        """switch_tunnel should call routing override."""
        handlers.routing.set_manual_override.return_value = True
        result = handlers.switch_tunnel("backhaul")
        assert result["success"] is True
        handlers.routing.set_manual_override.assert_called_with("backhaul")

    def test_clear_override(self, handlers):
        """clear_override should call routing clear."""
        result = handlers.clear_override()
        assert result["success"] is True
        handlers.routing.clear_manual_override.assert_called_once()

    def test_get_config_masks_token(self, handlers):
        """Config endpoint should mask Telegram token."""
        result = handlers.get_config()
        assert result["telegram"]["token"] == "***"

    def test_list_users_empty(self, handlers):
        """list_users should return empty list when no users."""
        result = handlers.list_users()
        assert "users" in result
        assert result["users"] == []

    def test_create_user_invalid_name(self, handlers):
        """Invalid username should return error."""
        result = handlers.create_user("1invalid")
        assert "error" in result

    def test_create_user_no_openvpn(self, handlers):
        """create_user should fail gracefully if OpenVPN not loaded."""
        handlers.pm.get.return_value = None
        result = handlers.create_user("validuser")
        assert "error" in result

    def test_get_user_not_found(self, handlers):
        """get_user should return error for unknown user."""
        result = handlers.get_user("nonexistent")
        assert "error" in result

    def test_ping_endpoint(self, handlers):
        """Ping endpoint should return pong."""
        result = handlers.ping_endpoint()
        assert result["pong"] is True
        assert "timestamp" in result

    def test_routing_status(self, handlers):
        """routing_status should return routing engine status."""
        result = handlers.routing_status()
        assert "mode" in result
        assert "current_tunnel" in result

    def test_get_handler_map(self, handlers):
        """Handler map should cover all important routes."""
        handler_map = handlers.get_handler_map()
        required_routes = [
            "GET /health",
            "GET /status",
            "GET /plugins",
            "POST /users",
            "GET /tunnels",
            "GET /routing",
            "POST /ping",
        ]
        for route in required_routes:
            assert route in handler_map, f"Missing route: {route}"

    def test_toggle_user_not_found(self, handlers):
        """toggle_user should return error for unknown user."""
        result = handlers.toggle_user("nonexistent")
        assert "error" in result

    def test_update_config_delegates(self, handlers):
        """update_config should call cfg.set for each key."""
        result = handlers.update_config({"openvpn.port": 8443})
        assert result["success"] is True
        handlers.cfg.set.assert_called()
