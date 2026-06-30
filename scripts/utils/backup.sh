#!/usr/bin/env bash
# IronShield - Backup Script
# Usage: sudo bash /opt/ironshield/scripts/utils/backup.sh [output_dir]

set -euo pipefail

INSTALL_DIR="/opt/ironshield"
OUTPUT_DIR="${1:-/opt/ironshield/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="ironshield_backup_${TIMESTAMP}"
BACKUP_PATH="${OUTPUT_DIR}/${BACKUP_NAME}.tar.gz"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

log()     { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root." >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

log "Creating backup: $BACKUP_NAME"

# Items to back up
ITEMS=(
    "configs"
    "db"
    "keys"
)

TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

for item in "${ITEMS[@]}"; do
    if [[ -e "$INSTALL_DIR/$item" ]]; then
        cp -r "$INSTALL_DIR/$item" "$TEMP_DIR/"
        log "Added: $item"
    fi
done

# Create archive
tar -czf "$BACKUP_PATH" -C "$TEMP_DIR" .
chmod 600 "$BACKUP_PATH"

SIZE=$(du -h "$BACKUP_PATH" | cut -f1)
success "Backup created: $BACKUP_PATH ($SIZE)"

# Keep only last 10 backups
log "Cleaning old backups (keeping last 10)..."
cd "$OUTPUT_DIR"
ls -t ironshield_backup_*.tar.gz 2>/dev/null | tail -n +11 | xargs -r rm -f
success "Backup complete"
