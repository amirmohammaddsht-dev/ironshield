"""
Tests for Phase 4 — Plugin implementations.
All tests fully mocked — no real system calls.
"""

from __future__ import annotations

import json
import sys
import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from ironshield.services.base import (
    ServiceStatus,
    Result,
    HealthResult,
    BenchmarkResult,
    ServerRole,
    PluginCategory,
)


# ── Helpers ───────────────────────────────────


def load_plugin(rel_path: str, module_key: str):
    """Dynamically load a plugin service.py file."""
    full_path = Path(__file__).parent.parent.parent / rel_path
    spec = importlib.util.spec_from_file_location(module_key, full_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    spec.loader.exec_module(module)
    return module


# ── OpenVPN Tests ─────────────────────────────


class TestOpenVPNService:
    @pytest.fixture
    def module(self):
        return load_plugin("plugins/vpn/openvpn/service.py", "openvpn_svc")

    @pytest.fixture
    def svc(self, module):
        return module.OpenVPNService(
            server_role=ServerRole.IRAN,
            config={
                "port": 443,
                "port_fallback": 80,
                "protocol": "tcp",
                "network": "10.8.0.0",
                "netmask": "255.255.255.0",
                "max_clients": 100,
                "cipher": "AES-256-GCM",
                "dns_primary": "1.1.1.1",
                "dns_secondary": "8.8.8.8",
                "server_ip": "1.2.3.4",
            },
        )

    def test_meta_name(self, svc):
        assert svc.meta.name == "openvpn"

    def test_meta_required(self, svc):
        assert svc.meta.required is True

    def test_meta_category(self, svc):
        assert svc.meta.category == PluginCategory.VPN

    def test_meta_priority(self, svc):
        assert svc.meta.priority == 1

    def test_meta_iran_only(self, svc):
        assert svc.meta.supports_role(ServerRole.IRAN) is True
        assert svc.meta.supports_role(ServerRole.FOREIGN) is False

    def test_ufw_ports(self, svc):
        ports = [p["port"] for p in svc.meta.ufw_ports]
        assert 443 in ports
        assert 80 in ports

    def test_status_not_installed(self, svc, module, tmp_path):
        with patch.object(module, "SERVER_CONF", tmp_path / "missing.conf"):
            assert svc.status() == ServiceStatus.NOT_INSTALLED

    def test_write_server_config(self, svc, module, tmp_path):
        """Config file should contain correct values."""
        conf = tmp_path / "server.conf"
        log = tmp_path / "openvpn-status.log"
        with (
            patch.object(module, "SERVER_CONF", conf),
            patch.object(module, "STATUS_LOG", log),
            patch.object(module, "OPENVPN_DIR", tmp_path),
        ):
            result = svc._write_server_config()

        assert result.success is True
        assert conf.exists()
        text = conf.read_text()
        assert "port 443" in text
        assert "proto tcp" in text
        assert "AES-256-GCM" in text
        assert "10.8.0.0" in text
        assert "max-clients 100" in text
        assert "1.1.1.1" in text

    def test_get_config(self, svc):
        c = svc.get_config()
        assert c["port"] == 443
        assert c["port_fallback"] == 80
        assert c["protocol"] == "tcp"
        assert c["max_clients"] == 100

    def test_extract_cert(self, svc):
        raw = "header\n-----BEGIN CERTIFICATE-----\nDATA\n-----END CERTIFICATE-----\nfooter"
        out = svc._extract_cert(raw)
        assert "-----BEGIN CERTIFICATE-----" in out
        assert "header" not in out
        assert "footer" not in out

    def test_parse_status_log_missing(self, svc, module, tmp_path):
        with patch.object(module, "STATUS_LOG", tmp_path / "nolog"):
            assert svc.get_active_connections() == []

    def test_parse_status_log(self, svc, module, tmp_path):
        """Should parse connected clients from status log."""
        content = (
            "OpenVPN CLIENT LIST\n"
            "Updated,Mon Jan  1 12:00:00 2024\n"
            "Common Name,Real Address,Bytes Received,Bytes Sent,Connected Since\n"
            "ali_user,5.6.7.8:12345,1024000,2048000,Mon Jan  1 10:00:00 2024\n"
            "ROUTING TABLE\n"
        )
        log = tmp_path / "status.log"
        log.write_text(content)
        with patch.object(module, "STATUS_LOG", log):
            conns = svc.get_active_connections()
        assert len(conns) == 1
        assert conns[0]["username"] == "ali_user"
        assert conns[0]["real_ip"] == "5.6.7.8:12345"

    def test_get_metrics(self, svc, module, tmp_path):
        with patch.object(module, "STATUS_LOG", tmp_path / "nolog"):
            m = svc.get_metrics()
        assert m["plugin"] == "openvpn"
        assert "active_connections" in m
        assert "total_bytes_sent" in m


# ── GOST Tests ────────────────────────────────


class TestGOSTService:
    @pytest.fixture
    def module(self):
        return load_plugin("plugins/tunnels/gost/service.py", "gost_svc")

    @pytest.fixture
    def svc_iran(self, module):
        return module.GOSTService(
            server_role=ServerRole.IRAN,
            config={"local_port": 8080, "remote_host": "1.2.3.4", "remote_port": 8080},
        )

    def test_meta_name(self, svc_iran):
        assert svc_iran.meta.name == "gost"

    def test_meta_both_roles(self, svc_iran):
        assert svc_iran.meta.supports_role(ServerRole.IRAN) is True
        assert svc_iran.meta.supports_role(ServerRole.FOREIGN) is True

    def test_meta_category(self, svc_iran):
        assert svc_iran.meta.category == PluginCategory.TUNNEL_RELIABLE

    def test_write_config_iran(self, svc_iran, module, tmp_path):
        """Iran config should have forwarder to remote."""
        cfg = tmp_path / "gost.json"
        with patch.object(module, "GOST_CONFIG", cfg):
            result = svc_iran._write_config()
        assert result.success is True
        data = json.loads(cfg.read_text())
        svc = data["services"][0]
        assert svc["addr"] == ":8080"
        assert "forwarder" in svc
        assert "1.2.3.4:8080" in svc["forwarder"]["nodes"][0]["addr"]

    def test_write_config_foreign(self, module, tmp_path):
        """Foreign config should only listen — no forwarder."""
        svc = module.GOSTService(
            server_role=ServerRole.FOREIGN,
            config={"local_port": 8080},
        )
        cfg = tmp_path / "gost_foreign.json"
        with patch.object(module, "GOST_CONFIG", cfg):
            result = svc._write_config()
        assert result.success is True
        data = json.loads(cfg.read_text())
        assert "forwarder" not in data["services"][0]

    def test_status_not_installed(self, svc_iran, module, tmp_path):
        with patch.object(module, "GOST_BIN", tmp_path / "no_gost"):
            assert svc_iran.status() == ServiceStatus.NOT_INSTALLED

    def test_get_config(self, svc_iran):
        c = svc_iran.get_config()
        assert c["local_port"] == 8080
        assert c["remote_host"] == "1.2.3.4"

    def test_supports_benchmark(self, svc_iran):
        assert svc_iran.supports_benchmark() is True

    def test_benchmark_no_remote(self, svc_iran):
        svc_iran.config["remote_host"] = ""
        r = svc_iran.benchmark()
        assert r.success is False
        assert "No remote host" in r.error

    def test_benchmark_unreachable(self, module, svc_iran):
        from ironshield.utils.network import PingResult

        with patch.object(module, "ping") as mock_ping:
            mock_ping.return_value = PingResult(host="1.2.3.4", success=False)
            r = svc_iran.benchmark()
        assert r.success is False

    def test_benchmark_success(self, module, svc_iran):
        from ironshield.utils.network import PingResult, ThroughputResult

        with (
            patch.object(module, "ping") as mock_ping,
            patch.object(module, "measure_packet_loss") as mock_loss,
            patch.object(module, "measure_throughput") as mock_tp,
        ):
            mock_ping.return_value = PingResult(
                host="1.2.3.4", success=True, avg_ms=40.0, packet_loss=0.0
            )
            mock_loss.return_value = 0.0
            mock_tp.return_value = ThroughputResult(
                host="1.2.3.4", success=True, download_mbps=95.0
            )
            r = svc_iran.benchmark()

        assert r.success is True
        assert r.latency_ms == 40.0
        assert r.packet_loss_percent == 0.0
        assert r.throughput_mbps == 95.0
        assert r.score is not None
        assert r.score > 80


# ── Phormal Tests ─────────────────────────────


class TestPhormalService:
    @pytest.fixture
    def module(self):
        return load_plugin("plugins/tunnels/phormal/service.py", "phormal_svc")

    @pytest.fixture
    def svc(self, module):
        return module.PhormalService(
            server_role=ServerRole.IRAN,
            config={"mode": "relay", "peer_ip": "5.6.7.8", "port": 8531},
        )

    def test_meta_name(self, svc):
        assert svc.meta.name == "phormal"

    def test_meta_category(self, svc):
        assert svc.meta.category == PluginCategory.TUNNEL_FAST

    def test_meta_priority(self, svc):
        assert svc.meta.priority == 1

    def test_get_config(self, svc):
        c = svc.get_config()
        assert c["mode"] == "relay"
        assert c["peer_ip"] == "5.6.7.8"
        assert c["port"] == 8531

    def test_supports_benchmark(self, svc):
        assert svc.supports_benchmark() is True

    def test_benchmark_no_peer(self, svc):
        svc.config["peer_ip"] = ""
        r = svc.benchmark()
        assert r.success is False

    def test_benchmark_unreachable(self, module, svc):
        from ironshield.utils.network import PingResult

        with patch.object(module, "ping") as mock_ping:
            mock_ping.return_value = PingResult(host="5.6.7.8", success=False)
            r = svc.benchmark()
        assert r.success is False

    def test_benchmark_success(self, module, svc):
        from ironshield.utils.network import PingResult, ThroughputResult

        with (
            patch.object(module, "ping") as mock_ping,
            patch.object(module, "measure_packet_loss") as mock_loss,
            patch.object(module, "measure_throughput") as mock_tp,
        ):
            mock_ping.return_value = PingResult(
                host="5.6.7.8", success=True, avg_ms=35.0, packet_loss=0.0
            )
            mock_loss.return_value = 0.0
            mock_tp.return_value = ThroughputResult(
                host="5.6.7.8", success=True, download_mbps=150.0
            )
            r = svc.benchmark()

        assert r.success is True
        assert r.latency_ms == 35.0
        assert r.score is not None
        assert r.score >= 90

    def test_status_not_installed(self, module, svc, tmp_path):
        with patch.object(module, "PHORMAL_BIN", tmp_path / "no_phormal"):
            assert svc.status() == ServiceStatus.NOT_INSTALLED
