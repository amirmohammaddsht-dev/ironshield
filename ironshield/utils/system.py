"""
IronShield - System Utilities
Path: ironshield/utils/system.py
Purpose: Linux system operations, process management, and resource monitoring
"""

import os
import subprocess
import shutil
import psutil
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass

from ironshield.utils.logger import get_logger

logger = get_logger("system")


@dataclass
class SystemInfo:
    """System resource information snapshot."""

    cpu_percent: float
    cpu_load_1m: float
    cpu_load_5m: float
    cpu_load_15m: float
    ram_total_gb: float
    ram_used_gb: float
    ram_percent: float
    disk_total_gb: float
    disk_used_gb: float
    disk_percent: float
    net_bytes_sent: int
    net_bytes_recv: int
    uptime_hours: float


def run_command(
    cmd: str,
    timeout: int = 30,
    check: bool = False,
    shell: bool = True,
) -> Tuple[int, str, str]:
    """
    Execute a shell command and return its output.

    Args:
        cmd: Command string
        timeout: Timeout in seconds
        check: Raise exception on non-zero exit code
        shell: Use shell execution

    Returns:
        tuple: (returncode, stdout, stderr)
    """
    try:
        result = subprocess.run(
            cmd,
            shell=shell,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out: {cmd}")
        return -1, "", "timeout"
    except Exception as e:
        logger.error(f"Command execution error [{cmd}]: {e}")
        return -1, "", str(e)


def systemctl(action: str, service: str) -> bool:
    """
    Control a systemd service.

    Args:
        action: start/stop/restart/enable/disable/is-active
        service: Service name

    Returns:
        bool: True on success
    """
    code, _, err = run_command(f"systemctl {action} {service}")
    if code != 0:
        logger.warning(f"systemctl {action} {service} failed: {err}")
    return code == 0


def service_is_active(service: str) -> bool:
    """Check if a systemd service is active."""
    code, out, _ = run_command(f"systemctl is-active {service}")
    return out.strip() == "active"


def port_is_open(host: str, port: int, timeout: int = 3) -> bool:
    """
    Check if a TCP port is open on a host.

    Args:
        host: Target host
        port: Port number
        timeout: Connection timeout in seconds

    Returns:
        bool: True if port is open
    """
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def get_system_info() -> SystemInfo:
    """
    Collect current system resource metrics.

    Returns:
        SystemInfo: Complete system snapshot
    """
    cpu = psutil.cpu_percent(interval=1)
    load = psutil.getloadavg()
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    boot_time = psutil.boot_time()
    uptime = (psutil.time.time() - boot_time) / 3600

    return SystemInfo(
        cpu_percent=cpu,
        cpu_load_1m=load[0],
        cpu_load_5m=load[1],
        cpu_load_15m=load[2],
        ram_total_gb=ram.total / (1024**3),
        ram_used_gb=ram.used / (1024**3),
        ram_percent=ram.percent,
        disk_total_gb=disk.total / (1024**3),
        disk_used_gb=disk.used / (1024**3),
        disk_percent=disk.percent,
        net_bytes_sent=net.bytes_sent,
        net_bytes_recv=net.bytes_recv,
        uptime_hours=uptime,
    )


def get_process_by_port(port: int) -> Optional[psutil.Process]:
    """Find the process listening on a given port."""
    for conn in psutil.net_connections():
        if conn.laddr.port == port and conn.status == "LISTEN":
            try:
                return psutil.Process(conn.pid)
            except psutil.NoSuchProcess:
                pass
    return None


def is_root() -> bool:
    """Check if running as root."""
    return os.geteuid() == 0


def get_ubuntu_version() -> Optional[str]:
    """Get Ubuntu release version string."""
    code, out, _ = run_command("lsb_release -rs")
    return out if code == 0 else None


def get_available_ram_gb() -> float:
    """Get available RAM in GB."""
    return psutil.virtual_memory().available / (1024**3)


def get_available_disk_gb(path: str = "/") -> float:
    """Get available disk space in GB."""
    return psutil.disk_usage(path).free / (1024**3)


def binary_exists(binary: str) -> bool:
    """Check if a binary exists in PATH."""
    return shutil.which(binary) is not None


def create_system_user(username: str) -> bool:
    """
    Create the IronShield system user.

    Args:
        username: System username

    Returns:
        bool: True on success
    """
    code, _, _ = run_command(f"id {username}")
    if code == 0:
        logger.info(f"User {username} already exists")
        return True

    code, _, err = run_command(f"useradd -r -s /usr/sbin/nologin -d /opt/ironshield -m {username}")
    if code != 0:
        logger.error(f"Failed to create user {username}: {err}")
        return False

    logger.info(f"System user {username} created")
    return True


def setup_sudoers(username: str) -> bool:
    """
    Configure limited sudo rules for the IronShield user.

    Args:
        username: System username

    Returns:
        bool: True on success
    """
    sudoers_content = f"""# IronShield sudo rules
# Managed by IronShield — do not edit manually

{username} ALL=(ALL) NOPASSWD: \\
    /bin/systemctl start ironshield-*, \\
    /bin/systemctl stop ironshield-*, \\
    /bin/systemctl restart ironshield-*, \\
    /bin/systemctl status ironshield-*, \\
    /bin/systemctl enable ironshield-*, \\
    /bin/systemctl disable ironshield-*, \\
    /sbin/ip route *, \\
    /sbin/ip link *, \\
    /sbin/ip tunnel *, \\
    /sbin/ip addr *, \\
    /sbin/iptables -t nat *, \\
    /sbin/iptables -t filter *, \\
    /usr/sbin/ufw allow *, \\
    /usr/sbin/ufw deny *, \\
    /usr/sbin/ufw delete *, \\
    /usr/sbin/ufw status *
"""
    sudoers_file = Path("/etc/sudoers.d/ironshield")
    try:
        with open(sudoers_file, "w") as f:
            f.write(sudoers_content)
        os.chmod(sudoers_file, 0o440)
        logger.info("Sudoers rules configured")
        return True
    except Exception as e:
        logger.error(f"Failed to configure sudoers: {e}")
        return False


def setup_directories() -> bool:
    """
    Create required directories with correct permissions.

    Returns:
        bool: True on success
    """
    dirs = {
        "/opt/ironshield": 0o750,
        "/opt/ironshield/logs": 0o750,
        "/opt/ironshield/db": 0o700,
        "/opt/ironshield/configs": 0o700,
        "/opt/ironshield/configs/openvpn": 0o700,
        "/opt/ironshield/configs/tunnels": 0o700,
        "/opt/ironshield/keys": 0o700,
    }

    try:
        for path, mode in dirs.items():
            p = Path(path)
            p.mkdir(parents=True, exist_ok=True)
            os.chmod(p, mode)
            run_command(f"chown -R ironshield:ironshield {path}")

        logger.info("Directories created successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to create directories: {e}")
        return False
