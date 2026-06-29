"""
IronShield - Agent Main Entry Point
Path: ironshield/agent/main.py
Purpose: Entry point for the Foreign server Agent.
         Starts the REST API server and metric collection loop.
         Installed as a systemd service on the Foreign server.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path
from typing import Optional

from ironshield.agent.api import AgentAPIServer
from ironshield.agent.collector import AgentCollector
from ironshield.utils.logger import get_logger

logger = get_logger("agent.main")

# Default config path
CONFIG_PATH = Path("/opt/ironshield/configs/main.yaml")
DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"


class Agent:
    """
    IronShield Foreign Server Agent.

    Runs as a lightweight background service on the Foreign server.
    Provides the Iran server with:
    - System metrics (CPU/RAM/Disk/Network)
    - Service status (running/stopped)
    - Log access
    - Service control (start/stop/restart)
    - Real-delay ping endpoint for benchmark engine

    Architecture:
        [Iran Server Core Engine]
               |
        [Tunnel (Phormal/GOST/etc)]
               |
        [Foreign Server Agent:8765]
               |
        [AgentCollector → systemd/psutil]
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        api_key: Optional[str] = None,
    ):
        self.collector = AgentCollector()
        self.api_server = AgentAPIServer(
            collector=self.collector,
            host=host,
            port=port,
            api_key=api_key,
        )
        self._running = False

    async def start(self) -> None:
        """Start the agent — runs until stopped."""
        self._running = True
        logger.info(f"IronShield Agent starting on {self.api_server.host}:{self.api_server.port}")

        # Register shutdown handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown)

        # Pre-warm the metric cache
        self.collector.get_system_metrics(force=True)
        self.collector.get_service_status(force=True)

        try:
            await self.api_server.start()
        except Exception as e:
            logger.error(f"Agent error: {e}")
        finally:
            self._running = False
            logger.info("Agent stopped")

    def _shutdown(self) -> None:
        """Handle shutdown signal."""
        logger.info("Shutdown signal received")
        asyncio.get_event_loop().create_task(self.api_server.stop())


def load_config_from_file() -> dict:
    """Load agent configuration from the main IronShield config file."""
    if not CONFIG_PATH.exists():
        logger.warning(f"Config file not found: {CONFIG_PATH} — using defaults")
        return {}

    try:
        import yaml

        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
        return {}


def main() -> None:
    """
    Main entry point for the IronShield Agent.
    Called by systemd unit: ironshield-agent.service
    """
    config = load_config_from_file()

    # Extract agent config
    agent_cfg = config.get("agent", {})
    host = agent_cfg.get("host", DEFAULT_HOST)
    port = int(agent_cfg.get("port", DEFAULT_PORT))
    api_key = agent_cfg.get("api_key", None)

    logger.info("Starting IronShield Agent v1.0.0")
    logger.info(f"Listen: {host}:{port}")
    logger.info("API key: " + ("configured" if api_key else "not set"))

    agent = Agent(host=host, port=port, api_key=api_key)

    try:
        asyncio.run(agent.start())
    except KeyboardInterrupt:
        logger.info("Agent interrupted by user")
    except Exception as e:
        logger.critical(f"Agent crashed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
