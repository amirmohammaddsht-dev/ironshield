#!/usr/bin/env bash
# IronShield - FRP Plugin Installer
set -euo pipefail

ARCH=$(uname -m)
case "$ARCH" in
    x86_64) FRP_ARCH="amd64" ;;
    aarch64) FRP_ARCH="arm64" ;;
    *) echo "[FRP] Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

echo "[FRP] Fetching latest release info..."
LATEST_URL=$(curl -fsSL https://api.github.com/repos/fatedier/frp/releases/latest \
    | grep "browser_download_url.*linux_${FRP_ARCH}.tar.gz" \
    | cut -d '"' -f 4 | head -1)

if [[ -z "$LATEST_URL" ]]; then
    echo "[FRP] ERROR: Could not find release for linux_${FRP_ARCH}" >&2
    exit 1
fi

echo "[FRP] Downloading: $LATEST_URL"
TMP_DIR=$(mktemp -d)
curl -fsSL "$LATEST_URL" -o "$TMP_DIR/frp.tar.gz"
tar -xzf "$TMP_DIR/frp.tar.gz" -C "$TMP_DIR" --strip-components=1

cp "$TMP_DIR/frps" /usr/local/bin/frps
cp "$TMP_DIR/frpc" /usr/local/bin/frpc
chmod +x /usr/local/bin/frps /usr/local/bin/frpc
rm -rf "$TMP_DIR"

echo "[FRP] Installed frps and frpc"
