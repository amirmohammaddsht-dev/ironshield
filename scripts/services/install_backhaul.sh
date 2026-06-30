#!/usr/bin/env bash
# IronShield - Backhaul Plugin Installer
set -euo pipefail

ARCH=$(uname -m)
case "$ARCH" in
    x86_64) BH_ARCH="amd64" ;;
    aarch64) BH_ARCH="arm64" ;;
    *) echo "[Backhaul] Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

echo "[Backhaul] Fetching latest release info..."
LATEST_URL=$(curl -fsSL https://api.github.com/repos/Musixal/Backhaul/releases/latest \
    | grep "browser_download_url.*linux_${BH_ARCH}.tar.gz" \
    | cut -d '"' -f 4 | head -1)

if [[ -z "$LATEST_URL" ]]; then
    echo "[Backhaul] ERROR: Could not find release for linux_${BH_ARCH}" >&2
    exit 1
fi

echo "[Backhaul] Downloading: $LATEST_URL"
TMP_DIR=$(mktemp -d)
curl -fsSL "$LATEST_URL" -o "$TMP_DIR/backhaul.tar.gz"
tar -xzf "$TMP_DIR/backhaul.tar.gz" -C "$TMP_DIR"

BH_BIN=$(find "$TMP_DIR" -name "backhaul" -type f | head -1)
if [[ -z "$BH_BIN" ]]; then
    echo "[Backhaul] ERROR: binary not found in archive" >&2
    exit 1
fi

cp "$BH_BIN" /usr/local/bin/backhaul
chmod +x /usr/local/bin/backhaul
rm -rf "$TMP_DIR"

echo "[Backhaul] Installed successfully"
