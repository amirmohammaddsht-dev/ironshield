#!/usr/bin/env bash
# IronShield - Update Script
# Usage: sudo bash /opt/ironshield/scripts/update.sh

set -euo pipefail

INSTALL_DIR="/opt/ironshield"
SYSTEM_USER="ironshield"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

log()     { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }

if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root."
    exit 1
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║        🔄 IronShield Updater              ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Backup current config ─────────────────────

log "Backing up configuration..."
BACKUP_DIR="$INSTALL_DIR/configs/backups"
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
cp "$INSTALL_DIR/configs/main.yaml" "$BACKUP_DIR/main_${TIMESTAMP}.yaml" 2>/dev/null || true
success "Config backed up"

# ── Get current version ───────────────────────

CURRENT_VERSION=$(sudo -u "$SYSTEM_USER" "$INSTALL_DIR/venv/bin/python" -c \
    "from ironshield.version import __version__; print(__version__)" 2>/dev/null || echo "unknown")
log "Current version: $CURRENT_VERSION"

# ── Stop services ─────────────────────────────

log "Stopping services..."
for service in ironshield-bot ironshield-core; do
    systemctl stop "$service" 2>/dev/null || true
done
success "Services stopped"

# ── Pull latest code ──────────────────────────

log "Pulling latest changes..."
cd "$INSTALL_DIR"
sudo -u "$SYSTEM_USER" git fetch origin main
sudo -u "$SYSTEM_USER" git reset --hard origin/main
success "Code updated"

# ── Update dependencies ───────────────────────

log "Updating Python dependencies..."
sudo -u "$SYSTEM_USER" "$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade \
    -r "$INSTALL_DIR/requirements.txt"
success "Dependencies updated"

# ── Run database migrations (if any) ──────────

log "Checking for database migrations..."
if [[ -d "$INSTALL_DIR/ironshield/db/migrations/versions" ]] && \
   [[ -n "$(ls -A "$INSTALL_DIR/ironshield/db/migrations/versions" 2>/dev/null)" ]]; then
    sudo -u "$SYSTEM_USER" "$INSTALL_DIR/venv/bin/alembic" upgrade head 2>/dev/null || true
    success "Migrations applied"
else
    log "No migrations to apply"
fi

# ── Update systemd units ──────────────────────

log "Updating systemd units..."
for f in "$INSTALL_DIR"/configs/templates/systemd/*.service; do
    cp "$f" "/etc/systemd/system/$(basename "$f")"
done
systemctl daemon-reload
success "Systemd units updated"

# ── Restart services ───────────────────────────

log "Restarting services..."
systemctl start ironshield-core
sleep 2
systemctl start ironshield-bot
success "Services restarted"

# ── Get new version ───────────────────────────

NEW_VERSION=$(sudo -u "$SYSTEM_USER" "$INSTALL_DIR/venv/bin/python" -c \
    "from ironshield.version import __version__; print(__version__)" 2>/dev/null || echo "unknown")

echo ""
success "Update complete: $CURRENT_VERSION → $NEW_VERSION"
echo ""
log "Verify status with: ironshield status"
echo ""
