#!/usr/bin/env bash
# IronShield - Restore Script
# Usage: sudo bash /opt/ironshield/scripts/utils/restore.sh <backup_file.tar.gz>

set -euo pipefail

INSTALL_DIR="/opt/ironshield"
SYSTEM_USER="ironshield"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log()     { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }

if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root."
    exit 1
fi

BACKUP_FILE="${1:-}"

if [[ -z "$BACKUP_FILE" ]]; then
    error "Usage: $0 <backup_file.tar.gz>"
    exit 1
fi

if [[ ! -f "$BACKUP_FILE" ]]; then
    error "Backup file not found: $BACKUP_FILE"
    exit 1
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       ♻️  IronShield Restore              ║"
echo "╚══════════════════════════════════════════╝"
echo ""

warn "This will overwrite current configuration, database, and keys!"
read -rp "Type 'yes' to confirm: " confirm
if [[ "$confirm" != "yes" ]]; then
    echo "Cancelled."
    exit 0
fi

# ── Stop services ─────────────────────────────

log "Stopping services..."
for service in ironshield-bot ironshield-core ironshield-agent; do
    systemctl stop "$service" 2>/dev/null || true
done
success "Services stopped"

# ── Backup current state before restore ───────

log "Backing up current state (safety net)..."
SAFETY_BACKUP="/tmp/ironshield_pre_restore_$(date +%Y%m%d_%H%M%S).tar.gz"
tar -czf "$SAFETY_BACKUP" -C "$INSTALL_DIR" configs db keys 2>/dev/null || true
log "Safety backup: $SAFETY_BACKUP"

# ── Extract backup ────────────────────────────

log "Extracting backup..."
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

tar -xzf "$BACKUP_FILE" -C "$TEMP_DIR"

for item in configs db keys; do
    if [[ -e "$TEMP_DIR/$item" ]]; then
        rm -rf "${INSTALL_DIR:?}/$item"
        cp -r "$TEMP_DIR/$item" "$INSTALL_DIR/"
        log "Restored: $item"
    fi
done

chown -R "$SYSTEM_USER:$SYSTEM_USER" "$INSTALL_DIR"
chmod 700 "$INSTALL_DIR/db" "$INSTALL_DIR/configs" "$INSTALL_DIR/keys"

success "Data restored"

# ── Restart services ───────────────────────────

log "Restarting services..."
systemctl start ironshield-core
sleep 2
systemctl start ironshield-bot
success "Services restarted"

echo ""
success "Restore complete from: $BACKUP_FILE"
log "If something went wrong, your previous state is saved at:"
log "  $SAFETY_BACKUP"
echo ""
