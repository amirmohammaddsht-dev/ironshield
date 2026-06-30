#!/usr/bin/env bash
# IronShield - Storm-DNS Plugin Installer
set -euo pipefail

echo "[Storm-DNS] Downloading from GitHub releases..."
LATEST_URL=$(curl -fsSL https://api.github.com/repos/storm-dns/storm-dns/releases/latest \
    | grep "browser_download_url.*linux" \
    | cut -d '"' -f 4 | head -1)

if [[ -z "$LATEST_URL" ]]; then
    echo "[Storm-DNS] WARNING: Could not find release. Plugin may need manual setup." >&2
    exit 0
fi

echo "[Storm-DNS] Downloading: $LATEST_URL"
TMP_DIR=$(mktemp -d)
curl -fsSL "$LATEST_URL" -o "$TMP_DIR/storm-dns"
chmod +x "$TMP_DIR/storm-dns"
cp "$TMP_DIR/storm-dns" /usr/local/bin/storm-dns
rm -rf "$TMP_DIR"

echo "[Storm-DNS] Installed successfully"
echo "[Storm-DNS] NOTE: Requires domain with NS record — configure in CLI installer"
