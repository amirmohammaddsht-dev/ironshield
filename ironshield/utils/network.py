"""
IronShield - Network Utilities
Path: ironshield/utils/network.py
Purpose: Network testing, latency measurement, and connectivity checks
"""

import asyncio
import time
from typing import Optional, Dict
from dataclasses import dataclass

from ironshield.utils.logger import get_logger
from ironshield.utils.system import run_command

logger = get_logger("network")


@dataclass
class PingResult:
    """Result of a ping/latency test."""

    host: str
    success: bool
    min_ms: float = 0.0
    avg_ms: float = 0.0
    max_ms: float = 0.0
    packet_loss: float = 0.0
    error: Optional[str] = None


@dataclass
class RealDelayResult:
    """Result of an HTTP round-trip delay test."""

    host: str
    port: int
    success: bool
    small_ms: float = 0.0  # 64-byte payload
    medium_ms: float = 0.0  # 1KB payload
    large_ms: float = 0.0  # 8KB payload
    avg_ms: float = 0.0
    error: Optional[str] = None


@dataclass
class ThroughputResult:
    """Result of an iperf3 throughput test."""

    host: str
    success: bool
    download_mbps: float = 0.0
    upload_mbps: float = 0.0
    error: Optional[str] = None


def ping(host: str, count: int = 10, timeout: int = 5) -> PingResult:
    """
    Ping a host using fping and return latency statistics.

    Args:
        host: Target host/IP
        count: Number of ping packets
        timeout: Timeout per packet in seconds

    Returns:
        PingResult: Ping statistics
    """
    if not _binary_available("fping"):
        # Fallback to system ping
        return _ping_fallback(host, count)

    code, out, err = run_command(
        f"fping -c {count} -q -t {timeout * 1000} {host}",
        timeout=count * timeout + 5,
    )

    if code != 0 and "min/avg/max" not in out and "min/avg/max" not in err:
        return PingResult(host=host, success=False, error=err or "Host unreachable")

    # Parse fping output: "host : xmt/rcv/%loss = min/avg/max"
    output = out or err
    try:
        loss_part = output.split("=")[0]
        stats_part = output.split("=")[1] if "=" in output else ""

        loss_str = loss_part.split("/")[2].strip().replace("%", "")
        packet_loss = float(loss_str)

        if stats_part:
            parts = stats_part.strip().split("/")
            min_ms = float(parts[0])
            avg_ms = float(parts[1])
            max_ms = float(parts[2])
        else:
            return PingResult(host=host, success=False, packet_loss=100.0)

        return PingResult(
            host=host,
            success=packet_loss < 100,
            min_ms=min_ms,
            avg_ms=avg_ms,
            max_ms=max_ms,
            packet_loss=packet_loss,
        )
    except (IndexError, ValueError) as e:
        logger.debug(f"Failed to parse fping output: {output} — {e}")
        return PingResult(host=host, success=False, error=str(e))


def _ping_fallback(host: str, count: int = 5) -> PingResult:
    """Fallback ping using system ping command."""
    code, out, err = run_command(f"ping -c {count} -W 3 {host}", timeout=count * 5)

    if code != 0:
        return PingResult(host=host, success=False, error="Host unreachable")

    try:
        for line in out.split("\n"):
            if "min/avg/max" in line:
                stats = line.split("=")[1].strip().split("/")
                return PingResult(
                    host=host,
                    success=True,
                    min_ms=float(stats[0]),
                    avg_ms=float(stats[1]),
                    max_ms=float(stats[2].split()[0]),
                    packet_loss=0.0,
                )
    except (IndexError, ValueError):
        pass

    return PingResult(host=host, success=False, error="Could not parse ping output")


def measure_packet_loss(host: str, cycles: int = 20) -> float:
    """
    Measure packet loss using mtr.

    Args:
        host: Target host/IP
        cycles: Number of test cycles

    Returns:
        float: Packet loss percentage
    """
    if not _binary_available("mtr"):
        result = ping(host, count=cycles)
        return result.packet_loss

    code, out, err = run_command(
        f"mtr --report --report-cycles {cycles} --no-dns {host}",
        timeout=cycles * 2 + 10,
    )

    if code != 0:
        return 100.0

    # Parse mtr output — last hop is the destination
    lines = [line for line in out.split("\n") if line.strip()]
    if not lines:
        return 100.0

    try:
        last_line = lines[-1]
        parts = last_line.split()
        # mtr output: HOST  Loss%  Snt  Last  Avg  Best  Wrst  StDev
        loss_str = parts[2].replace("%", "")
        return float(loss_str)
    except (IndexError, ValueError):
        return 100.0


async def measure_real_delay(host: str, port: int = 8080) -> RealDelayResult:
    """
    Measure real round-trip delay using HTTP requests of different sizes.

    Args:
        host: Target host (IronShield agent)
        port: Agent API port

    Returns:
        RealDelayResult: Delay measurements for different payload sizes
    """
    import httpx

    payloads = {
        "small": b"x" * 64,
        "medium": b"x" * 1024,
        "large": b"x" * 8192,
    }
    results: Dict[str, float] = {}

    async with httpx.AsyncClient(timeout=5.0) as client:
        for size_name, payload in payloads.items():
            delays = []
            for _ in range(5):
                try:
                    start = time.monotonic()
                    await client.post(
                        f"http://{host}:{port}/api/ping",
                        content=payload,
                    )
                    elapsed = (time.monotonic() - start) * 1000
                    delays.append(elapsed)
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.debug(f"Real delay test error ({size_name}): {e}")

            if delays:
                results[size_name] = sum(delays) / len(delays)
            else:
                return RealDelayResult(
                    host=host, port=port, success=False, error=f"Failed to reach {host}:{port}"
                )

    avg = sum(results.values()) / len(results) if results else 0.0

    return RealDelayResult(
        host=host,
        port=port,
        success=True,
        small_ms=results.get("small", 0.0),
        medium_ms=results.get("medium", 0.0),
        large_ms=results.get("large", 0.0),
        avg_ms=avg,
    )


def measure_throughput(host: str, port: int = 5201, duration: int = 10) -> ThroughputResult:
    """
    Measure network throughput using iperf3.

    Args:
        host: iperf3 server host
        port: iperf3 server port
        duration: Test duration in seconds

    Returns:
        ThroughputResult: Download and upload speeds in Mbps
    """
    if not _binary_available("iperf3"):
        return ThroughputResult(host=host, success=False, error="iperf3 not installed")

    # Download test
    code, out, err = run_command(
        f"iperf3 -c {host} -p {port} -t {duration} -J",
        timeout=duration + 15,
    )

    if code != 0:
        return ThroughputResult(host=host, success=False, error=err)

    try:
        import json

        data = json.loads(out)
        download_bps = data["end"]["sum_received"]["bits_per_second"]
        download_mbps = download_bps / 1_000_000
    except (json.JSONDecodeError, KeyError):
        return ThroughputResult(host=host, success=False, error="Failed to parse iperf3 output")

    # Upload test (reverse)
    code, out, err = run_command(
        f"iperf3 -c {host} -p {port} -t {duration} -R -J",
        timeout=duration + 15,
    )

    upload_mbps = 0.0
    if code == 0:
        try:
            data = json.loads(out)
            upload_bps = data["end"]["sum_received"]["bits_per_second"]
            upload_mbps = upload_bps / 1_000_000
        except (json.JSONDecodeError, KeyError):
            pass

    return ThroughputResult(
        host=host,
        success=True,
        download_mbps=round(download_mbps, 2),
        upload_mbps=round(upload_mbps, 2),
    )


def get_public_ip() -> Optional[str]:
    """Get the server's public IP address."""
    import httpx

    try:
        response = httpx.get("https://api.ipify.org", timeout=10)
        return response.text.strip()
    except Exception:
        # Fallback
        code, out, _ = run_command("curl -s --max-time 5 https://api.ipify.org")
        return out.strip() if code == 0 and out else None


def _binary_available(binary: str) -> bool:
    """Check if a binary is available in PATH."""
    import shutil

    return shutil.which(binary) is not None
