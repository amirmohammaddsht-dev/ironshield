#!/usr/bin/env bash
# IronShield - VXLAN Plugin Installer
# VXLAN is kernel built-in — only ensure iproute2 and kernel module are present.
set -euo pipefail

echo "[VXLAN] Checking kernel module support..."
if ! modprobe vxlan 2>/dev/null; then
    echo "[VXLAN] WARNING: vxlan kernel module could not be loaded." >&2
fi

echo "[VXLAN] Ensuring iproute2 is installed..."
apt-get -o DPkg::Lock::Timeout=60 update -q
apt-get -o DPkg::Lock::Timeout=60 install -y -q iproute2

echo "[VXLAN] Ready (interface setup handled by Python VXLANService)"
