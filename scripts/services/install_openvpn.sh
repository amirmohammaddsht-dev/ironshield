#!/usr/bin/env bash
# IronShield - OpenVPN Plugin Installer
set -euo pipefail

echo "[OpenVPN] Installing packages..."
apt-get update -q
apt-get install -y -q openvpn easy-rsa iptables

echo "[OpenVPN] Package installation complete."
echo "[OpenVPN] Configuration handled by Python OpenVPNService.install()"
