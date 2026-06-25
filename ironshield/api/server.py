"""
IronShield - Internal API Server (Unix Socket)
Path: ironshield/api/server.py
Purpose: Lightweight JSON-RPC server over Unix Domain Socket.
         Handles requests from the CLI and Telegram bot to the Core Engine.
         Runs as a local service — no network exposure.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ironshield.utils.logger import get_logger

logger = get_logger("api.server")

SOCKET_PATH = Path("/opt/ironshield/ironshield.sock")
MAX_REQUEST_SIZE = 1024 * 1024  # 1MB


class APIServer:
    """
    Unix Domain Socket server for IronShield internal API.

    Protocol: newline-delimited JSON
    Request:  {"id": "...", "method": "GET /health", "params": {...}}
    Response: {"id": "...", "result": {...}, "error": null}

    Security:
    - Only accessible via Unix socket (no network)
    - Socket file owned by ironshield user, chmod 660
    - No authentication needed (OS-level access control)
    """

    def __init__(
        self,
        socket_path: Path = SOCKET_PATH,
        api_key: Optional[str] = None,
    ):
        self._socket_path = socket_path
        self._api_key = api_key
        self._handlers: Dict[str, Callable] = {}
        self._server: Optional[asyncio.AbstractServer] = None

    # ── Handler Registration ──────────────────

    def register(self, route: str, handler: Callable) -> None:
        """
        Register a handler for an API route.

        Args:
            route: Route string like 'GET /health'
            handler: Async or sync callable that returns a dict
        """
        self._handlers[route] = handler
        logger.debug(f"Registered handler: {route}")

    def register_many(self, handlers: Dict[str, Callable]) -> None:
        """Register multiple handlers at once."""
        for route, handler in handlers.items():
            self.register(route, handler)

    # ── Server Lifecycle ──────────────────────

    async def start(self) -> None:
        """Start the Unix socket server."""
        # Remove stale socket file
        if self._socket_path.exists():
            self._socket_path.unlink()

        self._socket_path.parent.mkdir(parents=True, exist_ok=True)

        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self._socket_path),
        )

        # Set socket permissions
        os.chmod(self._socket_path, 0o660)

        logger.info(f"API server started: {self._socket_path}")
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """Stop the server and clean up socket file."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        if self._socket_path.exists():
            self._socket_path.unlink()

        logger.info("API server stopped")

    # ── Request Handling ──────────────────────

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single client connection."""
        peer = writer.get_extra_info("peername", "unknown")
        logger.debug(f"New connection: {peer}")

        try:
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=30.0)
                except asyncio.TimeoutError:
                    break

                if not line:
                    break

                if len(line) > MAX_REQUEST_SIZE:
                    await self._send_error(writer, None, "Request too large")
                    continue

                await self._process_request(line, writer)

        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as e:
            logger.warning(f"Client handler error: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _process_request(self, raw: bytes, writer: asyncio.StreamWriter) -> None:
        """Parse and dispatch a single request."""
        request_id = None
        try:
            request = json.loads(raw.decode("utf-8").strip())
            request_id = request.get("id")
            method = request.get("method", "")
            params = request.get("params", {})

            # API key validation (if configured)
            if self._api_key:
                token = request.get("token", "")
                if token != self._api_key:
                    await self._send_error(writer, request_id, "Unauthorized")
                    return

            result = await self._dispatch(method, params)
            await self._send_response(writer, request_id, result)

        except json.JSONDecodeError:
            await self._send_error(writer, request_id, "Invalid JSON")
        except Exception as e:
            logger.error(f"Request processing error: {e}")
            await self._send_error(writer, request_id, str(e))

    async def _dispatch(self, method: str, params: Dict[str, Any]) -> Any:
        """Find and call the appropriate handler."""
        # Try exact match first
        if method in self._handlers:
            handler = self._handlers[method]
            if asyncio.iscoroutinefunction(handler):
                return await handler(**params)
            return handler(**params)

        # Try pattern matching (e.g. "GET /plugins/{name}")
        for route, handler in self._handlers.items():
            matched_params = self._match_route(method, route)
            if matched_params is not None:
                all_params = {**params, **matched_params}
                if asyncio.iscoroutinefunction(handler):
                    return await handler(**all_params)
                return handler(**all_params)

        raise ValueError(f"No handler for: {method}")

    @staticmethod
    def _match_route(method: str, pattern: str) -> Optional[Dict[str, str]]:
        """
        Match a method string against a route pattern with path parameters.

        Example:
            method  = "GET /plugins/openvpn"
            pattern = "GET /plugins/{name}"
            returns = {"name": "openvpn"}
        """
        method_parts = method.split()
        pattern_parts = pattern.split()

        if len(method_parts) != 2 or len(pattern_parts) != 2:
            return None

        if method_parts[0] != pattern_parts[0]:
            return None

        method_segments = method_parts[1].split("/")
        pattern_segments = pattern_parts[1].split("/")

        if len(method_segments) != len(pattern_segments):
            return None

        extracted = {}
        for m_seg, p_seg in zip(method_segments, pattern_segments):
            if p_seg.startswith("{") and p_seg.endswith("}"):
                param_name = p_seg[1:-1]
                extracted[param_name] = m_seg
            elif m_seg != p_seg:
                return None

        return extracted

    # ── Response Helpers ──────────────────────

    @staticmethod
    async def _send_response(
        writer: asyncio.StreamWriter,
        request_id: Optional[str],
        result: Any,
    ) -> None:
        """Send a successful response."""
        response = json.dumps(
            {
                "id": request_id,
                "result": result,
                "error": None,
            }
        )
        writer.write((response + "\n").encode("utf-8"))
        await writer.drain()

    @staticmethod
    async def _send_error(
        writer: asyncio.StreamWriter,
        request_id: Optional[str],
        error: str,
    ) -> None:
        """Send an error response."""
        response = json.dumps(
            {
                "id": request_id,
                "result": None,
                "error": error,
            }
        )
        writer.write((response + "\n").encode("utf-8"))
        await writer.drain()
