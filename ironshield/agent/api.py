"""
IronShield - Agent REST API
Path: ironshield/agent/api.py
Purpose: Lightweight HTTP server that runs on the Foreign server.
         Responds to requests from the Iran server Core Engine over the tunnel.
         Provides system metrics, service status, and service control endpoints.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from ironshield.agent.collector import AgentCollector
from ironshield.utils.logger import get_logger
from ironshield.utils.security import constant_time_compare
from ironshield.version import __version__

logger = get_logger("agent.api")

# Agent binds only to localhost — tunnel provides the network path
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class AgentAPIServer:
    """
    Minimal async HTTP server for the IronShield Agent.

    Uses raw asyncio streams (no framework dependency) to keep the
    foreign server footprint small.

    Security:
    - Binds to 127.0.0.1 only — accessible only via tunnel
    - Optional API key authentication via X-Agent-Key header
    - Iran server IP allowlist (checked by UFW at network level)

    Endpoints:
        GET  /health           — Liveness check
        GET  /metrics          — System resource metrics
        GET  /services         — All service statuses
        GET  /services/{name}  — Single service status
        GET  /logs/{name}      — Service logs
        POST /services/{name}/start    — Start service
        POST /services/{name}/stop     — Stop service
        POST /services/{name}/restart  — Restart service
        POST /ping             — Real-delay measurement endpoint
    """

    def __init__(
        self,
        collector: AgentCollector,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        api_key: Optional[str] = None,
    ):
        if api_key == "":
            raise ValueError(
                "api_key must not be an empty string; use None to disable authentication"
            )
        self.collector = collector
        self.host = host
        self.port = port
        self.api_key = api_key
        self._server: Optional[asyncio.AbstractServer] = None

    # ── Lifecycle ─────────────────────────────

    async def start(self) -> None:
        """Start the HTTP server."""
        self._server = await asyncio.start_server(
            self._handle_connection,
            self.host,
            self.port,
        )
        logger.info(f"Agent API server started on {self.host}:{self.port}")
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        logger.info("Agent API server stopped")

    # ── Connection Handler ────────────────────

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single HTTP connection."""
        try:
            raw = await asyncio.wait_for(reader.read(8192), timeout=10.0)
            if not raw:
                return

            method, path, headers, body = self._parse_request(raw)
            status, response_body = await self._route(method, path, headers, body)
            await self._send_response(writer, status, response_body)

        except asyncio.TimeoutError:
            await self._send_response(writer, 408, {"error": "Request timeout"})
        except Exception as e:
            logger.warning(f"Request handling error: {e}")
            await self._send_response(writer, 500, {"error": str(e)})
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # ── Request Parsing ───────────────────────

    @staticmethod
    def _parse_request(
        raw: bytes,
    ) -> Tuple[str, str, Dict[str, str], bytes]:
        """
        Parse raw HTTP request bytes into components.

        Returns:
            (method, path, headers, body)
        """
        try:
            # Split headers from body
            if b"\r\n\r\n" in raw:
                header_part, body = raw.split(b"\r\n\r\n", 1)
            else:
                header_part, body = raw, b""

            lines = header_part.decode("utf-8", errors="replace").split("\r\n")
            request_line = lines[0].split(" ")
            method = request_line[0].upper() if len(request_line) > 0 else "GET"
            path = request_line[1].split("?")[0] if len(request_line) > 1 else "/"

            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    key, _, value = line.partition(":")
                    headers[key.strip().lower()] = value.strip()

            return method, path, headers, body
        except Exception:
            return "GET", "/", {}, b""

    # ── Router ────────────────────────────────

    async def _route(
        self,
        method: str,
        path: str,
        headers: Dict[str, str],
        body: bytes,
    ) -> Tuple[int, Dict[str, Any]]:
        """Route request to appropriate handler."""

        # API key check
        if self.api_key is not None:
            provided = headers.get("x-agent-key", "")
            if not constant_time_compare(provided, self.api_key):
                return 401, {"error": "Unauthorized"}

        # Route matching
        if method == "GET" and path == "/health":
            return 200, self._handle_health()

        if method == "GET" and path == "/metrics":
            return 200, self._handle_metrics()

        if method == "GET" and path == "/services":
            return 200, self._handle_services()

        if method == "GET" and path.startswith("/services/") and "/logs" not in path:
            name = path.removeprefix("/services/")
            return self._handle_service_detail(name)

        if method == "GET" and path.startswith("/logs/"):
            name = path.removeprefix("/logs/")
            return 200, self._handle_logs(name)

        if method == "POST" and path == "/ping":
            return 200, self._handle_ping()

        if method == "POST" and path.startswith("/services/"):
            parts = path.removeprefix("/services/").split("/")
            if len(parts) == 2:
                name, action = parts
                return self._handle_service_action(name, action)

        if method == "GET" and path == "/snapshot":
            return 200, self.collector.get_full_snapshot()

        return 404, {"error": f"Not found: {method} {path}"}

    # ── Handlers ──────────────────────────────

    def _handle_health(self) -> Dict[str, Any]:
        """GET /health — Liveness check."""
        return {
            "status": "ok",
            "agent_version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "server": "foreign",
        }

    def _handle_metrics(self) -> Dict[str, Any]:
        """GET /metrics — System resource metrics."""
        snapshot = self.collector.get_system_metrics()
        return snapshot.to_dict()

    def _handle_services(self) -> Dict[str, Any]:
        """GET /services — All service statuses."""
        services = self.collector.get_service_status()
        return {
            "services": [s.to_dict() for s in services],
            "total": len(services),
            "running": sum(1 for s in services if s.is_running),
        }

    def _handle_service_detail(self, name: str) -> Tuple[int, Dict[str, Any]]:
        """GET /services/{name} — Single service detail."""
        svc = self.collector.get_service_by_name(name)
        if svc is None:
            return 404, {"error": f"Service not found: {name}"}
        return 200, svc.to_dict()

    def _handle_logs(self, name: str, lines: int = 50) -> Dict[str, Any]:
        """GET /logs/{name} — Service log lines."""
        log_lines = self.collector.get_service_logs(name, lines=lines)
        return {
            "service": name,
            "lines": log_lines,
            "count": len(log_lines),
        }

    def _handle_ping(self) -> Dict[str, Any]:
        """POST /ping — Real-delay measurement endpoint."""
        return {
            "pong": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _handle_service_action(self, name: str, action: str) -> Tuple[int, Dict[str, Any]]:
        """POST /services/{name}/{start|stop|restart}."""
        if action not in ("start", "stop", "restart"):
            return 400, {"error": f"Invalid action: {action}"}

        if action == "start":
            result = self.collector.start_service(name)
        elif action == "stop":
            result = self.collector.stop_service(name)
        else:
            result = self.collector.restart_service(name)

        status = 200 if result.get("success") else 500
        return status, result

    # ── Response Sender ───────────────────────

    @staticmethod
    async def _send_response(
        writer: asyncio.StreamWriter,
        status: int,
        body: Dict[str, Any],
    ) -> None:
        """Send an HTTP JSON response."""
        status_texts = {
            200: "OK",
            201: "Created",
            400: "Bad Request",
            401: "Unauthorized",
            404: "Not Found",
            408: "Request Timeout",
            500: "Internal Server Error",
        }
        status_text = status_texts.get(status, "Unknown")
        body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")

        response = (
            f"HTTP/1.1 {status} {status_text}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body_bytes)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode("utf-8") + body_bytes

        writer.write(response)
        await writer.drain()


class AgentHTTPClient:
    """
    HTTP client for the Iran server to query the Foreign Agent API.

    Used by the Core Engine (TunnelManager, MonitoringEngine) to collect
    metrics and control services on the foreign server.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        api_key: Optional[str] = None,
        timeout: float = 10.0,
    ):
        self.base_url = f"http://{host}:{port}"
        self.api_key = api_key
        self.timeout = timeout

    async def _get(self, path: str) -> Dict[str, Any]:
        """Make a GET request to the agent."""
        import httpx

        headers = {}
        if self.api_key:
            headers["X-Agent-Key"] = self.api_key

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(f"{self.base_url}{path}", headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def _post(self, path: str, body: Optional[Dict] = None) -> Dict[str, Any]:
        """Make a POST request to the agent."""
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-Agent-Key"] = self.api_key

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}{path}",
                json=body or {},
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def health(self) -> Dict[str, Any]:
        """Check agent health."""
        return await self._get("/health")

    async def get_metrics(self) -> Dict[str, Any]:
        """Get foreign server system metrics."""
        return await self._get("/metrics")

    async def get_services(self) -> Dict[str, Any]:
        """Get all service statuses."""
        return await self._get("/services")

    async def get_service(self, name: str) -> Dict[str, Any]:
        """Get a specific service status."""
        return await self._get(f"/services/{name}")

    async def get_logs(self, name: str) -> Dict[str, Any]:
        """Get service logs."""
        return await self._get(f"/logs/{name}")

    async def start_service(self, name: str) -> Dict[str, Any]:
        """Start a service on the foreign server."""
        return await self._post(f"/services/{name}/start")

    async def stop_service(self, name: str) -> Dict[str, Any]:
        """Stop a service on the foreign server."""
        return await self._post(f"/services/{name}/stop")

    async def restart_service(self, name: str) -> Dict[str, Any]:
        """Restart a service on the foreign server."""
        return await self._post(f"/services/{name}/restart")

    async def ping(self) -> Dict[str, Any]:
        """Real-delay ping endpoint."""
        return await self._post("/ping")

    async def get_snapshot(self) -> Dict[str, Any]:
        """Get full system + services snapshot."""
        return await self._get("/snapshot")

    async def is_reachable(self) -> bool:
        """Check if the agent is reachable."""
        try:
            result = await self.health()
            return result.get("status") == "ok"
        except Exception:
            return False
