#!/usr/bin/env bash
# IronShield - Storm-DNS Plugin Installer
set -euo pipefail

echo "[Storm-DNS] Downloading from GitHub releases..."

# NOTE: the original "storm-dns/storm-dns" repo does not exist (404).
# The real upstream project is nullroute1970/StormDNS.
REPO="nullroute1970/StormDNS"

case "$(uname -m)" in
    x86_64)          ASSET_ARCH="AMD64" ;;
    aarch64|arm64)   ASSET_ARCH="ARM64" ;;
    *)
        echo "[Storm-DNS] WARNING: Unsupported architecture '$(uname -m)'. Plugin may need manual setup." >&2
        exit 0
        ;;
esac

# Release asset names use "Linux" (capital L) and separate Client/Server
# builds, e.g. StormDNS_Server_Linux_AMD64.tar.gz — match case-sensitively
# and restrict to the Server build for this architecture.
LATEST_URL=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
    | grep "browser_download_url" \
    | grep "StormDNS_Server_Linux_${ASSET_ARCH}\.tar\.gz" \
    | cut -d '"' -f 4 | head -1)

if [[ -z "$LATEST_URL" ]]; then
    echo "[Storm-DNS] WARNING: Could not find a matching release asset. Plugin may need manual setup." >&2
    exit 0
fi

echo "[Storm-DNS] Downloading: $LATEST_URL"
TMP_DIR=$(mktemp -d)
curl -fsSL "$LATEST_URL" -o "$TMP_DIR/storm-dns.tar.gz"
tar xzf "$TMP_DIR/storm-dns.tar.gz" -C "$TMP_DIR"

# Don't assume the exact binary filename inside the release archive —
# find the extracted executable instead.
BINARY=$(find "$TMP_DIR" -maxdepth 2 -type f -executable | head -1)
if [[ -z "$BINARY" ]]; then
    echo "[Storm-DNS] ERROR: No executable found in downloaded archive." >&2
    rm -rf "$TMP_DIR"
    exit 1
fi

chmod +x "$BINARY"
cp "$BINARY" /usr/local/bin/storm-dns
rm -rf "$TMP_DIR"

echo "[Storm-DNS] Installed successfully"
echo "[Storm-DNS] NOTE: Requires domain with NS record — configure in CLI installer"
