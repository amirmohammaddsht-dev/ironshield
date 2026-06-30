#!/usr/bin/env bash
# IronShield - Phormal Plugin Installer
set -euo pipefail

echo "[Phormal] Downloading and installing Phormal..."
curl -fsSL https://raw.githubusercontent.com/Schmi7zz/Phormal/main/phormal.sh | bash

if [[ -f /usr/local/bin/phormal ]]; then
    echo "[Phormal] Installation successful."
else
    echo "[Phormal] ERROR: binary not found after install." >&2
    exit 1
fi
