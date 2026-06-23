"""
IronShield Plugin — OpenVPN
Path: plugins/vpn/openvpn/service.py
Purpose: Full OpenVPN server implementation.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional

from ironshield.services.base import (
    BaseService,
    PluginMeta,
    PluginCategory,
    ServerRole,
    ServiceStatus,
    Result,
    HealthResult,
)
from ironshield.utils.logger import get_service_logger
from ironshield.utils.system import run_command, service_is_active, systemctl, port_is_open

logger = get_service_logger("openvpn")

OPENVPN_DIR = Path("/etc/openvpn")
EASYRSA_DIR = Path("/opt/ironshield/pki")
SERVER_CONF = OPENVPN_DIR / "server.conf"
STATUS_LOG = Path("/opt/ironshield/logs/openvpn-status.log")
CLIENTS_DIR = Path("/opt/ironshield/configs/openvpn/clients")


class OpenVPNService(BaseService):
    """OpenVPN server plugin — user entry point on TCP 443/80."""

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name="openvpn",
            display_name="OpenVPN",
            version="2.6.8",
            author="OpenVPN Inc.",
            source_url="https://openvpn.net",
            license="GPL-2.0",
            roles=[ServerRole.IRAN],
            category=PluginCategory.VPN,
            priority=1,
            required=True,
            description="OpenVPN server — user entry point on TCP 443/80",
            ufw_ports=[
                {"port": 443, "protocol": "tcp", "description": "OpenVPN primary"},
                {"port": 80, "protocol": "tcp", "description": "OpenVPN fallback"},
            ],
        )

    def install(self) -> Result:
        """Install OpenVPN, easy-rsa, and set up PKI."""
        logger.info("Installing OpenVPN...")

        code, _, err = run_command("apt-get install -y openvpn easy-rsa iptables", timeout=120)
        if code != 0:
            return Result.fail(f"Package installation failed: {err}")

        if not (EASYRSA_DIR / "pki" / "ca.crt").exists():
            result = self._setup_pki()
            if not result.success:
                return result

        result = self._write_server_config()
        if not result.success:
            return result

        self._setup_iptables()
        run_command("echo 'net.ipv4.ip_forward=1' >> /etc/sysctl.conf")
        run_command("sysctl -p")
        systemctl("enable", "openvpn@server")

        return Result.ok("OpenVPN installed successfully")

    def _setup_pki(self) -> Result:
        """Initialize PKI and generate CA + server certificates."""
        EASYRSA_DIR.mkdir(parents=True, exist_ok=True)
        run_command(f"cp -r /usr/share/easy-rsa/* {EASYRSA_DIR}/")

        for cmd in [
            f"cd {EASYRSA_DIR} && ./easyrsa init-pki",
            f'cd {EASYRSA_DIR} && echo "IronShield CA" | ./easyrsa build-ca nopass',
            (
                f"cd {EASYRSA_DIR} && ./easyrsa gen-req server nopass && "
                f'echo "yes" | ./easyrsa sign-req server server'
            ),
            f"cd {EASYRSA_DIR} && ./easyrsa gen-dh",
        ]:
            code, _, err = run_command(cmd, timeout=120)
            if code != 0:
                return Result.fail(f"PKI setup failed: {err}")

        for src, dst in [
            (f"{EASYRSA_DIR}/pki/ca.crt", f"{OPENVPN_DIR}/ca.crt"),
            (f"{EASYRSA_DIR}/pki/issued/server.crt", f"{OPENVPN_DIR}/server.crt"),
            (f"{EASYRSA_DIR}/pki/private/server.key", f"{OPENVPN_DIR}/server.key"),
            (f"{EASYRSA_DIR}/pki/dh.pem", f"{OPENVPN_DIR}/dh.pem"),
        ]:
            run_command(f"cp {src} {dst}")

        run_command(f"chmod 600 {OPENVPN_DIR}/server.key")
        return Result.ok()

    def _write_server_config(self) -> Result:
        """Write OpenVPN server.conf from config values."""
        vpn_network = self.config.get("network", "10.8.0.0")
        vpn_netmask = self.config.get("netmask", "255.255.255.0")
        port = self.config.get("port", 443)
        protocol = self.config.get("protocol", "tcp")
        cipher = self.config.get("cipher", "AES-256-GCM")
        max_clients = self.config.get("max_clients", 100)
        dns1 = self.config.get("dns_primary", "1.1.1.1")
        dns2 = self.config.get("dns_secondary", "8.8.8.8")

        config = f"""# IronShield OpenVPN Server — managed automatically
port {port}
proto {protocol}
dev tun
ca   {OPENVPN_DIR}/ca.crt
cert {OPENVPN_DIR}/server.crt
key  {OPENVPN_DIR}/server.key
dh   {OPENVPN_DIR}/dh.pem
server {vpn_network} {vpn_netmask}
push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS {dns1}"
push "dhcp-option DNS {dns2}"
keepalive 10 120
cipher {cipher}
auth SHA256
tls-version-min 1.2
max-clients {max_clients}
user nobody
group nogroup
persist-key
persist-tun
status {STATUS_LOG} 10
log-append /opt/ironshield/logs/openvpn.log
verb 3
"""
        try:
            OPENVPN_DIR.mkdir(parents=True, exist_ok=True)
            SERVER_CONF.write_text(config)
            return Result.ok()
        except Exception as e:
            return Result.fail(f"Failed to write server config: {e}")

    def _setup_iptables(self) -> None:
        """Set up NAT masquerade for VPN traffic."""
        code, iface, _ = run_command("ip route | grep default | awk '{print $5}' | head -1")
        iface = iface.strip() if code == 0 and iface.strip() else "eth0"

        rules = [
            f"iptables -t nat -A POSTROUTING -s 10.8.0.0/24 -o {iface} -j MASQUERADE",
            "iptables -A FORWARD -i tun0 -j ACCEPT",
            "iptables -A FORWARD -o tun0 -j ACCEPT",
        ]
        for rule in rules:
            run_command(rule)

    def uninstall(self) -> Result:
        systemctl("stop", "openvpn@server")
        systemctl("disable", "openvpn@server")
        run_command("apt-get remove -y openvpn easy-rsa -q")
        return Result.ok("OpenVPN uninstalled")

    def start(self) -> Result:
        STATUS_LOG.parent.mkdir(parents=True, exist_ok=True)
        Path("/var/log/openvpn").mkdir(parents=True, exist_ok=True)
        ok = systemctl("start", "openvpn@server")
        return Result.ok("OpenVPN started") if ok else Result.fail("Failed to start OpenVPN")

    def stop(self) -> Result:
        systemctl("stop", "openvpn@server")
        return Result.ok("OpenVPN stopped")

    def status(self) -> ServiceStatus:
        if not SERVER_CONF.exists():
            return ServiceStatus.NOT_INSTALLED
        return (
            ServiceStatus.RUNNING if service_is_active("openvpn@server") else ServiceStatus.STOPPED
        )

    def health_check(self) -> HealthResult:
        checks = {
            "process": service_is_active("openvpn@server"),
            "port_443": port_is_open("127.0.0.1", 443),
        }
        code, out, _ = run_command("ip link show tun0")
        checks["tun_interface"] = code == 0 and "UP" in out

        if STATUS_LOG.exists():
            age = datetime.now().timestamp() - STATUS_LOG.stat().st_mtime
            checks["status_log"] = age < 60
        else:
            checks["status_log"] = False

        healthy = all(checks.values())
        status = (
            ServiceStatus.RUNNING
            if healthy
            else (ServiceStatus.FAILED if not checks.get("process") else ServiceStatus.DEGRADED)
        )
        return HealthResult(
            healthy=healthy,
            status=status,
            checks=checks,
            message="OpenVPN is healthy" if healthy else "OpenVPN has issues",
        )

    def get_config(self) -> Dict[str, Any]:
        return {
            "port": self.config.get("port", 443),
            "port_fallback": self.config.get("port_fallback", 80),
            "protocol": self.config.get("protocol", "tcp"),
            "network": self.config.get("network", "10.8.0.0"),
            "max_clients": self.config.get("max_clients", 100),
            "cipher": self.config.get("cipher", "AES-256-GCM"),
        }

    def apply_config(self, config: Dict[str, Any]) -> Result:
        self.config.update(config)
        result = self._write_server_config()
        if not result.success:
            return result
        return self.restart()

    def get_logs(self, lines: int = 100) -> List[str]:
        log_file = Path("/opt/ironshield/logs/openvpn.log")
        if not log_file.exists():
            return []
        code, out, _ = run_command(f"tail -n {lines} {log_file}")
        return out.splitlines() if code == 0 else []

    # ── User Management ───────────────────────

    def add_user(
        self,
        username: str,
        expire_days: int = 30,
    ) -> Result:
        """Create a new VPN user with certificate and .ovpn config."""
        logger.info(f"Creating user: {username}")

        code, _, err = run_command(
            f"cd {EASYRSA_DIR} && ./easyrsa gen-req {username} nopass && "
            f'echo "yes" | ./easyrsa sign-req client {username}',
            timeout=60,
        )
        if code != 0:
            return Result.fail(f"Certificate generation failed: {err}")

        try:
            ca_cert = (OPENVPN_DIR / "ca.crt").read_text()
            client_cert = (EASYRSA_DIR / f"pki/issued/{username}.crt").read_text()
            client_key = (EASYRSA_DIR / f"pki/private/{username}.key").read_text()
        except FileNotFoundError as e:
            return Result.fail(f"Certificate file not found: {e}")

        server_ip = self.config.get("server_ip", "YOUR_SERVER_IP")
        port = self.config.get("port", 443)
        fallback_port = self.config.get("port_fallback", 80)
        protocol = self.config.get("protocol", "tcp")
        cipher = self.config.get("cipher", "AES-256-GCM")

        ovpn = f"""# IronShield VPN — {username}
client
dev tun
proto {protocol}
remote {server_ip} {port}
remote {server_ip} {fallback_port}
remote-random
resolv-retry infinite
nobind
persist-key
persist-tun
cipher {cipher}
auth SHA256
tls-version-min 1.2
verb 3
<ca>
{ca_cert.strip()}
</ca>
<cert>
{self._extract_cert(client_cert)}
</cert>
<key>
{client_key.strip()}
</key>
"""
        CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
        ovpn_path = CLIENTS_DIR / f"{username}.ovpn"
        ovpn_path.write_text(ovpn)
        os.chmod(ovpn_path, 0o600)

        return Result.ok(
            f"User {username} created",
            username=username,
            ovpn_content=ovpn,
            expires_at=(datetime.now(timezone.utc) + timedelta(days=expire_days)).isoformat(),
        )

    def remove_user(self, username: str) -> Result:
        """Revoke user certificate and remove config."""
        run_command(
            f'cd {EASYRSA_DIR} && echo "yes" | ./easyrsa revoke {username} '
            f"&& ./easyrsa gen-crl",
            timeout=30,
        )
        ovpn_path = CLIENTS_DIR / f"{username}.ovpn"
        if ovpn_path.exists():
            ovpn_path.unlink()
        return Result.ok(f"User {username} removed")

    def get_user_config(self, username: str) -> Optional[str]:
        """Return .ovpn file content for a user."""
        ovpn_path = CLIENTS_DIR / f"{username}.ovpn"
        return ovpn_path.read_text() if ovpn_path.exists() else None

    def get_active_connections(self) -> List[Dict[str, Any]]:
        """Parse OpenVPN status log and return active connections."""
        if not STATUS_LOG.exists():
            return []

        connections = []
        in_client_list = False

        for line in STATUS_LOG.read_text().splitlines():
            if line.startswith("Common Name,Real Address"):
                in_client_list = True
                continue
            if line.startswith("ROUTING TABLE"):
                in_client_list = False
                continue
            if in_client_list and "," in line:
                parts = line.split(",")
                if len(parts) >= 4:
                    connections.append(
                        {
                            "username": parts[0].strip(),
                            "real_ip": parts[1].strip(),
                            "bytes_sent": int(parts[2]) if parts[2].isdigit() else 0,
                            "bytes_recv": int(parts[3]) if parts[3].isdigit() else 0,
                            "connected_since": parts[4].strip() if len(parts) > 4 else "",
                        }
                    )
        return connections

    def get_metrics(self) -> Dict[str, Any]:
        """Return OpenVPN-specific metrics for monitoring."""
        connections = self.get_active_connections()
        return {
            "plugin": self.meta.name,
            "status": self.status().value,
            "version": self.meta.version,
            "active_connections": len(connections),
            "total_bytes_sent": sum(c["bytes_sent"] for c in connections),
            "total_bytes_recv": sum(c["bytes_recv"] for c in connections),
        }

    @staticmethod
    def _extract_cert(cert_content: str) -> str:
        """Extract only the certificate block from a full cert file."""
        match = re.search(
            r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
            cert_content,
            re.DOTALL,
        )
        return match.group(0) if match else cert_content.strip()
