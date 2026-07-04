#!/usr/bin/env bash
# IronShield - Phormal Plugin Installer
set -euo pipefail

echo "[Phormal] Downloading and installing Phormal..."
# The upstream script (Schmi7zz/Phormal) is stored with CRLF line endings,
# which breaks bash parsing (`$'\r': command not found`, etc). Strip them.
curl -fsSL https://raw.githubusercontent.com/Schmi7zz/Phormal/main/phormal.sh | tr -d '\r' | bash

if [[ -f /usr/local/bin/phormal ]]; then
    echo "[Phormal] Installation successful."
else
    echo "[Phormal] ERROR: binary not found after install." >&2
    exit 1
fi
