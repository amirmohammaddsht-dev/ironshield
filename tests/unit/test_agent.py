"""
Tests for Phase 9 — Agent (Foreign Server).
Tests: AgentCollector caching, AgentAPIServer routing,
       request parsing, response formatting, service control.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from ironshield.agent.collector import AgentCollector, ServiceSnapshot, SystemSnapshot
from ironshield.agent.api import AgentAPIServer


# ── AgentCollector Tests ───────────────────────


class TestAgentCollector:
    @pytest.fixture
    def collector(self):
        return AgentCollector()

    def test_system_snapshot_to_dict(self):
        """SystemSnapshot.to_dict() should include all required keys."""
        snap = SystemSnapshot(
            cpu_percent=35.5,
            ram_percent=52.0,
            ram_used_gb=2.1,
            ram_total_gb=4.0,
            disk_percent=23.0,
            disk_used_gb=10.0,
            disk_total_gb=50.0,
            net_bytes_sent=1000000,
            net_bytes_recv=2000000,
            uptime_hours=48.5,
        )
        d = snap.to_dict()

        required_keys = [
            "server",
            "timestamp",
            "cpu_percent",
            "ram_percent",
            "ram_used_gb",
            "ram_total_gb",
            "disk_percent",
            "net_bytes_sent",
            "net_bytes_recv",
            "uptime_hours",
        ]
        for key in required_keys:
            assert key in d, f"Missing key: {key}"

        assert d["server"] == "foreign"
        assert d["cpu_percent"] == 35.5
        assert d["ram_percent"] == 52.0

    def test_service_snapshot_to_dict(self):
        """ServiceSnapshot.to_dict() should include status field."""
        snap = ServiceSnapshot(
            name="gost",
            display_name="GOST",
            systemd_service="ironshield-gost",
            is_running=True,
        )
        d = snap.to_dict()

        assert d["name"] == "gost"
        assert d["is_running"] is True
        assert d["status"] == "RUNNING"

    def test_service_snapshot_stopped(self):
        """Stopped service should have STOPPED status."""
        snap = ServiceSnapshot(
            name="frp",
            display_name="FRP",
            systemd_service="ironshield-frp",
            is_running=False,
        )
        d = snap.to_dict()
        assert d["status"] == "STOPPED"
        assert d["is_running"] is False

    def test_system_metrics_cache(self, collector):
        """Second call within TTL should return cached result."""
        snap1 = SystemSnapshot(cpu_percent=10.0)
        collector._system_cache = snap1
        collector._system_cache_time = time.monotonic()

        with patch.object(collector, "_collect_system_metrics") as mock_collect:
            result = collector.get_system_metrics()
            mock_collect.assert_not_called()

        assert result is snap1

    def test_system_metrics_cache_expired(self, collector):
        """Expired cache should trigger fresh collection."""
        snap1 = SystemSnapshot(cpu_percent=10.0)
        collector._system_cache = snap1
        collector._system_cache_time = time.monotonic() - 999  # expired

        fresh_snap = SystemSnapshot(cpu_percent=55.0)
        with patch.object(collector, "_collect_system_metrics", return_value=fresh_snap):
            result = collector.get_system_metrics()

        assert result is fresh_snap

    def test_system_metrics_force_refresh(self, collector):
        """force=True should bypass cache."""
        snap1 = SystemSnapshot(cpu_percent=10.0)
        collector._system_cache = snap1
        collector._system_cache_time = time.monotonic()

        fresh = SystemSnapshot(cpu_percent=99.0)
        with patch.object(collector, "_collect_system_metrics", return_value=fresh):
            result = collector.get_system_metrics(force=True)

        assert result is fresh

    def test_service_cache(self, collector):
        """Service status should be cached."""
        cached = [ServiceSnapshot("gost", "GOST", "ironshield-gost", True)]
        collector._service_cache = cached
        collector._service_cache_time = time.monotonic()

        with patch.object(collector, "_collect_service_status") as mock:
            result = collector.get_service_status()
            mock.assert_not_called()

        assert result is cached

    def test_service_cache_invalidated_on_control(self, collector):
        """Cache should be cleared after start/stop/restart."""
        collector._service_cache = []
        collector._service_cache_time = time.monotonic()

        with patch.object(collector, "_get_systemd_name", return_value="ironshield-gost"), patch(
            "ironshield.agent.collector.run_command", return_value=(0, "", "")
        ):
            collector.start_service("gost")

        assert collector._service_cache is None

    def test_get_service_by_name_found(self, collector):
        """Should return matching service snapshot."""
        snaps = [
            ServiceSnapshot("gost", "GOST", "ironshield-gost", True),
            ServiceSnapshot("frp", "FRP", "ironshield-frp", False),
        ]
        collector._service_cache = snaps
        collector._service_cache_time = time.monotonic()

        result = collector.get_service_by_name("gost")
        assert result is not None
        assert result.name == "gost"

    def test_get_service_by_name_not_found(self, collector):
        """Should return None for unknown service."""
        collector._service_cache = []
        collector._service_cache_time = time.monotonic()

        result = collector.get_service_by_name("nonexistent")
        assert result is None

    def test_get_systemd_name(self, collector):
        """Should map plugin name to systemd service name."""
        assert collector._get_systemd_name("gost") == "ironshield-gost"
        assert collector._get_systemd_name("frp") == "ironshield-frp"
        assert collector._get_systemd_name("storm_dns") == "ironshield-storm-dns"
        assert collector._get_systemd_name("unknown") is None

    def test_control_unknown_service(self, collector):
        """Control of unknown service should return error."""
        result = collector.start_service("nonexistent_plugin")
        assert result["success"] is False
        assert "Unknown service" in result["error"]

    def test_foreign_services_list(self, collector):
        """Should have expected services defined."""
        names = [s["name"] for s in collector.FOREIGN_SERVICES]
        for expected in ("phormal", "gost", "frp", "backhaul", "storm_dns"):
            assert expected in names

    def test_get_full_snapshot_structure(self, collector):
        """Full snapshot should include system and services."""
        with (
            patch.object(
                collector, "get_system_metrics", return_value=SystemSnapshot(cpu_percent=35.0)
            ),
            patch.object(collector, "get_service_status", return_value=[]),
        ):
            snapshot = collector.get_full_snapshot()

        assert "system" in snapshot
        assert "services" in snapshot
        assert "agent_version" in snapshot
        assert isinstance(snapshot["services"], list)


# ── AgentAPIServer Tests ───────────────────────


class TestAgentAPIServer:
    @pytest.fixture
    def collector(self):
        """Mock collector with preset data."""
        c = MagicMock()
        c.get_system_metrics.return_value = SystemSnapshot(
            cpu_percent=35.0,
            ram_percent=50.0,
            disk_percent=20.0,
        )
        c.get_service_status.return_value = [
            ServiceSnapshot("gost", "GOST", "ironshield-gost", True),
        ]
        c.get_service_by_name.return_value = ServiceSnapshot(
            "gost", "GOST", "ironshield-gost", True
        )
        c.get_service_logs.return_value = ["log line 1", "log line 2"]
        c.get_full_snapshot.return_value = {
            "agent_version": "1.0.0",
            "system": {},
            "services": [],
        }
        c.start_service.return_value = {"success": True, "message": "gost started"}
        c.stop_service.return_value = {"success": True, "message": "gost stopped"}
        c.restart_service.return_value = {"success": True, "message": "gost restarted"}
        return c

    @pytest.fixture
    def server(self, collector):
        return AgentAPIServer(collector=collector, host="127.0.0.1", port=9999)

    def test_parse_request_get(self, server):
        """Should parse GET request correctly."""
        raw = b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n"
        method, path, headers, body = server._parse_request(raw)
        assert method == "GET"
        assert path == "/health"
        assert "host" in headers

    def test_parse_request_post_with_body(self, server):
        """Should parse POST request with body."""
        raw = b"POST /ping HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}"
        method, path, headers, body = server._parse_request(raw)
        assert method == "POST"
        assert path == "/ping"
        assert body == b"{}"

    def test_parse_request_with_query_string(self, server):
        """Should strip query string from path."""
        raw = b"GET /services?format=json HTTP/1.1\r\n\r\n"
        _, path, _, _ = server._parse_request(raw)
        assert path == "/services"

    def test_parse_invalid_request(self, server):
        """Should handle malformed requests gracefully."""
        raw = b"not an http request"
        method, path, headers, body = server._parse_request(raw)
        assert isinstance(method, str)
        assert isinstance(path, str)

    @pytest.mark.asyncio
    async def test_route_health(self, server):
        """GET /health should return ok status."""
        status, body = await server._route("GET", "/health", {}, b"")
        assert status == 200
        assert body["status"] == "ok"
        assert "timestamp" in body

    @pytest.mark.asyncio
    async def test_route_metrics(self, server):
        """GET /metrics should return system metrics."""
        status, body = await server._route("GET", "/metrics", {}, b"")
        assert status == 200
        assert "cpu_percent" in body

    @pytest.mark.asyncio
    async def test_route_services(self, server):
        """GET /services should return service list."""
        status, body = await server._route("GET", "/services", {}, b"")
        assert status == 200
        assert "services" in body
        assert "total" in body
        assert "running" in body

    @pytest.mark.asyncio
    async def test_route_service_detail(self, server):
        """GET /services/{name} should return single service."""
        status, body = await server._route("GET", "/services/gost", {}, b"")
        assert status == 200
        assert body["name"] == "gost"

    @pytest.mark.asyncio
    async def test_route_service_not_found(self, server, collector):
        """GET /services/{name} with unknown name should return 404."""
        collector.get_service_by_name.return_value = None
        status, body = await server._route("GET", "/services/unknown", {}, b"")
        assert status == 404
        assert "error" in body

    @pytest.mark.asyncio
    async def test_route_logs(self, server):
        """GET /logs/{name} should return log lines."""
        status, body = await server._route("GET", "/logs/gost", {}, b"")
        assert status == 200
        assert "lines" in body
        assert "service" in body

    @pytest.mark.asyncio
    async def test_route_ping(self, server):
        """POST /ping should return pong."""
        status, body = await server._route("POST", "/ping", {}, b"")
        assert status == 200
        assert body["pong"] is True

    @pytest.mark.asyncio
    async def test_route_service_start(self, server):
        """POST /services/{name}/start should start service."""
        status, body = await server._route("POST", "/services/gost/start", {}, b"")
        assert status == 200
        assert body["success"] is True

    @pytest.mark.asyncio
    async def test_route_service_stop(self, server):
        """POST /services/{name}/stop should stop service."""
        status, body = await server._route("POST", "/services/gost/stop", {}, b"")
        assert status == 200
        assert body["success"] is True

    @pytest.mark.asyncio
    async def test_route_service_restart(self, server):
        """POST /services/{name}/restart should restart service."""
        status, body = await server._route("POST", "/services/gost/restart", {}, b"")
        assert status == 200
        assert body["success"] is True

    @pytest.mark.asyncio
    async def test_route_invalid_action(self, server):
        """Invalid action should return 400."""
        status, body = await server._route("POST", "/services/gost/delete", {}, b"")
        assert status == 400
        assert "error" in body

    @pytest.mark.asyncio
    async def test_route_not_found(self, server):
        """Unknown route should return 404."""
        status, body = await server._route("GET", "/nonexistent", {}, b"")
        assert status == 404

    @pytest.mark.asyncio
    async def test_api_key_authentication(self, collector):
        """Requests with wrong API key should be rejected."""
        server = AgentAPIServer(collector=collector, api_key="secret123")
        status, body = await server._route("GET", "/health", {"x-agent-key": "wrongkey"}, b"")
        assert status == 401
        assert body["error"] == "Unauthorized"

    @pytest.mark.asyncio
    async def test_api_key_correct(self, collector):
        """Correct API key should be accepted."""
        server = AgentAPIServer(collector=collector, api_key="secret123")
        status, body = await server._route("GET", "/health", {"x-agent-key": "secret123"}, b"")
        assert status == 200

    @pytest.mark.asyncio
    async def test_no_api_key_required(self, server):
        """Without api_key set, all requests should pass auth."""
        status, body = await server._route("GET", "/health", {}, b"")
        assert status == 200

    @pytest.mark.asyncio
    async def test_snapshot_endpoint(self, server):
        """GET /snapshot should return full snapshot."""
        status, body = await server._route("GET", "/snapshot", {}, b"")
        assert status == 200
        assert "agent_version" in body

    @pytest.mark.asyncio
    async def test_full_http_server_cycle(self):
        """Full HTTP request/response cycle over TCP."""
        collector = MagicMock()
        collector.get_system_metrics.return_value = SystemSnapshot(cpu_percent=42.0)

        server = AgentAPIServer(collector=collector, host="127.0.0.1", port=19876)
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.1)

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", 19876)
            request = b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n"
            writer.write(request)
            await writer.drain()

            response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
            response_str = response.decode("utf-8", errors="replace")

            assert "HTTP/1.1 200 OK" in response_str
            assert "application/json" in response_str

            body_start = response_str.find("\r\n\r\n") + 4
            body = json.loads(response_str[body_start:])
            assert body["status"] == "ok"

            writer.close()
            await writer.wait_closed()

        finally:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
