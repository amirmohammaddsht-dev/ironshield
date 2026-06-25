"""
IronShield - Internal API Client (Unix Socket)
Path: ironshield/api/client.py
Purpose: Client for communicating with the IronShield Core Engine
         via Unix Domain Socket. Used by CLI and Telegram bot.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from ironshield.utils.logger import get_logger

logger = get_logger("api.client")

SOCKET_PATH = Path("/opt/ironshield/ironshield.sock")
DEFAULT_TIMEOUT = 30.0


class APIError(Exception):
    """Raised when the API returns an error response."""

    pass


class APIClient:
    """
    Client for the IronShield Unix Socket API.

    Usage:
        client = APIClient()

        # Simple request
        result = await client.request("GET /health")

        # With parameters
        result = await client.request(
            "POST /users",
            params={"username": "ali", "expire_days": 30}
        )

        # Context manager
        async with APIClient() as client:
            status = await client.get_status()
    """

    def __init__(
        self,
        socket_path: Path = SOCKET_PATH,
        timeout: float = DEFAULT_TIMEOUT,
        api_key: Optional[str] = None,
    ):
        self._socket_path = socket_path
        self._timeout = timeout
        self._api_key = api_key

    # ── Context Manager ───────────────────────

    async def __aenter__(self) -> "APIClient":
        return self

    async def __aexit__(self, *args) -> None:
        pass

    # ── Core Request ──────────────────────────

    async def request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Send a request to the Core Engine and return the result.

        Args:
            method: Route string like 'GET /health' or 'POST /users'
            params: Optional parameters dict

        Returns:
            Result from the handler

        Raises:
            APIError: If the server returns an error
            ConnectionError: If the socket is not available
        """
        if not self._socket_path.exists():
            raise ConnectionError(
                f"IronShield Core is not running " f"(socket not found: {self._socket_path})"
            )

        request_id = str(uuid.uuid4())[:8]
        payload = {
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        if self._api_key:
            payload["token"] = self._api_key

        raw = (json.dumps(payload) + "\n").encode("utf-8")

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._socket_path)),
                timeout=self._timeout,
            )
        except (FileNotFoundError, ConnectionRefusedError) as e:
            raise ConnectionError(f"Cannot connect to Core Engine: {e}") from e

        try:
            writer.write(raw)
            await writer.drain()

            response_line = await asyncio.wait_for(reader.readline(), timeout=self._timeout)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        if not response_line:
            raise APIError("Empty response from Core Engine")

        try:
            response = json.loads(response_line.decode("utf-8").strip())
        except json.JSONDecodeError as e:
            raise APIError(f"Invalid JSON response: {e}") from e

        if response.get("error"):
            raise APIError(response["error"])

        return response.get("result")

    # ── Convenience Methods ───────────────────

    async def get_health(self) -> Dict:
        """Quick health check."""
        return await self.request("GET /health")

    async def get_status(self) -> Dict:
        """Full system status."""
        return await self.request("GET /status")

    async def list_plugins(self) -> Dict:
        """List all plugins and their statuses."""
        return await self.request("GET /plugins")

    async def start_plugin(self, name: str) -> Dict:
        return await self.request(f"POST /plugins/{name}/start")

    async def stop_plugin(self, name: str) -> Dict:
        return await self.request(f"POST /plugins/{name}/stop")

    async def restart_plugin(self, name: str) -> Dict:
        return await self.request(f"POST /plugins/{name}/restart")

    async def get_plugin_logs(self, name: str, lines: int = 100) -> Dict:
        return await self.request(f"GET /plugins/{name}/logs", params={"lines": lines})

    async def list_tunnels(self) -> Dict:
        return await self.request("GET /tunnels")

    async def get_ranked_tunnels(self) -> Dict:
        return await self.request("GET /tunnels/ranked")

    async def switch_tunnel(self, name: str) -> Dict:
        return await self.request(f"POST /tunnels/switch/{name}")

    async def clear_tunnel_override(self) -> Dict:
        return await self.request("DELETE /tunnels/override")

    async def run_benchmark(self, quick: bool = True) -> Dict:
        route = "POST /benchmark/quick" if quick else "POST /benchmark/full"
        return await self.request(route)

    async def list_users(self) -> Dict:
        return await self.request("GET /users")

    async def create_user(
        self,
        username: str,
        traffic_limit_gb: Optional[float] = None,
        expire_days: int = 30,
    ) -> Dict:
        return await self.request(
            "POST /users",
            params={
                "username": username,
                "traffic_limit_gb": traffic_limit_gb,
                "expire_days": expire_days,
            },
        )

    async def delete_user(self, username: str) -> Dict:
        return await self.request(f"DELETE /users/{username}")

    async def toggle_user(self, username: str) -> Dict:
        return await self.request(f"POST /users/{username}/toggle")

    async def get_user_config(self, username: str) -> Dict:
        return await self.request(f"GET /users/{username}/config")

    async def get_metrics(self) -> Dict:
        return await self.request("GET /metrics")

    async def get_config(self) -> Dict:
        return await self.request("GET /config")

    async def update_config(self, updates: Dict[str, Any]) -> Dict:
        return await self.request("PATCH /config", params={"updates": updates})

    async def get_routing_status(self) -> Dict:
        return await self.request("GET /routing")

    async def get_routing_history(self) -> Dict:
        return await self.request("GET /routing/history")

    # ── Sync wrapper ──────────────────────────

    def request_sync(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """
        Synchronous wrapper for use in non-async contexts (CLI).

        Args:
            method: Route string
            params: Optional parameters

        Returns:
            Result from the handler
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Inside async context — create a new loop
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, self.request(method, params))
                    return future.result(timeout=self._timeout)
            else:
                return loop.run_until_complete(self.request(method, params))
        except (ConnectionError, APIError):
            raise


class SyncAPIClient:
    """
    Fully synchronous API client for use in the CLI.

    Usage:
        client = SyncAPIClient()
        status = client.get_status()
        plugins = client.list_plugins()
    """

    def __init__(self, socket_path: Path = SOCKET_PATH, timeout: float = 30.0):
        self._async_client = APIClient(socket_path=socket_path, timeout=timeout)

    def _call(self, method: str, params: Optional[Dict] = None) -> Any:
        """Execute an async API call synchronously."""
        return asyncio.run(self._async_client.request(method, params))

    def get_health(self) -> Dict:
        return self._call("GET /health")

    def get_status(self) -> Dict:
        return self._call("GET /status")

    def list_plugins(self) -> Dict:
        return self._call("GET /plugins")

    def start_plugin(self, name: str) -> Dict:
        return self._call(f"POST /plugins/{name}/start")

    def stop_plugin(self, name: str) -> Dict:
        return self._call(f"POST /plugins/{name}/stop")

    def restart_plugin(self, name: str) -> Dict:
        return self._call(f"POST /plugins/{name}/restart")

    def get_plugin_logs(self, name: str, lines: int = 100) -> Dict:
        return self._call(f"GET /plugins/{name}/logs", {"lines": lines})

    def list_tunnels(self) -> Dict:
        return self._call("GET /tunnels")

    def get_ranked_tunnels(self) -> Dict:
        return self._call("GET /tunnels/ranked")

    def switch_tunnel(self, name: str) -> Dict:
        return self._call(f"POST /tunnels/switch/{name}")

    def clear_tunnel_override(self) -> Dict:
        return self._call("DELETE /tunnels/override")

    def run_benchmark(self, quick: bool = True) -> Dict:
        route = "POST /benchmark/quick" if quick else "POST /benchmark/full"
        return self._call(route)

    def list_users(self) -> Dict:
        return self._call("GET /users")

    def create_user(
        self,
        username: str,
        traffic_limit_gb: Optional[float] = None,
        expire_days: int = 30,
    ) -> Dict:
        return self._call(
            "POST /users",
            {
                "username": username,
                "traffic_limit_gb": traffic_limit_gb,
                "expire_days": expire_days,
            },
        )

    def delete_user(self, username: str) -> Dict:
        return self._call(f"DELETE /users/{username}")

    def toggle_user(self, username: str) -> Dict:
        return self._call(f"POST /users/{username}/toggle")

    def get_user_config(self, username: str) -> Dict:
        return self._call(f"GET /users/{username}/config")

    def get_metrics(self) -> Dict:
        return self._call("GET /metrics")

    def get_config(self) -> Dict:
        return self._call("GET /config")

    def update_config(self, updates: Dict[str, Any]) -> Dict:
        return self._call("PATCH /config", {"updates": updates})

    def get_routing_status(self) -> Dict:
        return self._call("GET /routing")
