#!/usr/bin/env bash
# IronShield - GOST Plugin Installer
set -euo pipefail

ARCH=$(uname -m)
case "$ARCH" in
    x86_64) GOST_ARCH="amd64" ;;
    aarch64) GOST_ARCH="arm64" ;;
    *) echo "[GOST] Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

echo "[GOST] Fetching latest release info..."
LATEST_URL=$(curl -fsSL https://api.github.com/repos/ginuerzh/gost/releases/latest \
    | grep "browser_download_url.*linux_${GOST_ARCH}.tar.gz" \
    | cut -d '"' -f 4 | head -1)

if [[ -z "$LATEST_URL" ]]; then
    echo "[GOST] ERROR: Could not find release for linux_${GOST_ARCH}" >&2
    exit 1
fi

echo "[GOST] Downloading: $LATEST_URL"
TMP_DIR=$(mktemp -d)
curl -fsSL "$LATEST_URL" -o "$TMP_DIR/gost.tar.gz"
tar -xzf "$TMP_DIR/gost.tar.gz" -C "$TMP_DIR"

GOST_BIN=$(find "$TMP_DIR" -name "gost" -type f | head -1)
if [[ -z "$GOST_BIN" ]]; then
    echo "[GOST] ERROR: binary not found in archive" >&2
    exit 1
fi

cp "$GOST_BIN" /usr/local/bin/gost
chmod +x /usr/local/bin/gost
rm -rf "$TMP_DIR"

echo "[GOST] Installed: $(/usr/local/bin/gost -V 2>&1 | head -1)"
