#!/usr/bin/env bash
# IronShield - Uninstaller
# Usage: sudo bash /opt/ironshield/scripts/uninstall.sh [--purge]
#
# --purge: also removes user data (database, logs, configs, certificates)

set -euo pipefail

INSTALL_DIR="/opt/ironshield"
SYSTEM_USER="ironshield"
PURGE=false

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()     { echo -e "[INFO]  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }

for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=true ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "This script must be run as root." >&2
    exit 1
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║       🗑️  IronShield Uninstaller          ║"
echo "╚══════════════════════════════════════════╝"
echo ""

if [[ "$PURGE" == true ]]; then
    warn "PURGE mode: all data will be permanently deleted!"
    read -rp "Type 'yes' to confirm: " confirm
    if [[ "$confirm" != "yes" ]]; then
        echo "Cancelled."
        exit 0
    fi
fi

# ── Stop and disable services ──────────────────

log "Stopping IronShield services..."
for service in ironshield-core ironshield-bot ironshield-agent; do
    if systemctl is-active --quiet "$service" 2>/dev/null; then
        systemctl stop "$service"
    fi
    if systemctl is-enabled --quiet "$service" 2>/dev/null; then
        systemctl disable "$service"
    fi
done
success "Services stopped"

# ── Remove systemd unit files ──────────────────

log "Removing systemd units..."
rm -f /etc/systemd/system/ironshield-*.service
systemctl daemon-reload
success "Systemd units removed"

# ── Stop plugin services ──────────────────────

log "Stopping plugin services..."
for service in openvpn@server ironshield-phormal ironshield-gost \
               ironshield-frp ironshield-backhaul ironshield-vxlan \
               ironshield-storm-dns; do
    systemctl stop "$service" 2>/dev/null || true
    systemctl disable "$service" 2>/dev/null || true
done
success "Plugin services stopped"

# ── UFW cleanup ────────────────────────────────

log "Removing UFW rules..."
# Remove all IronShield-related rules but keep SSH
ufw status numbered 2>/dev/null | grep -v "22/tcp" | grep -oP '^\[\d+\]' | tac | while read -r num; do
    num=${num//[\[\]]/}
    ufw --force delete "$num" 2>/dev/null || true
done
success "UFW rules cleaned"

# ── Remove sudoers ─────────────────────────────

log "Removing sudoers configuration..."
rm -f /etc/sudoers.d/ironshield
success "Sudoers removed"

# ── Data removal (if --purge) ──────────────────

if [[ "$PURGE" == true ]]; then
    log "Removing all data (--purge)..."
    rm -rf "$INSTALL_DIR"
    rm -f /etc/openvpn/server.conf /etc/openvpn/ca.crt \
          /etc/openvpn/server.crt /etc/openvpn/server.key /etc/openvpn/dh.pem
    success "All data removed"
else
    log "Keeping data directory: $INSTALL_DIR"
    log "Removing only binaries..."
    rm -rf "$INSTALL_DIR/venv"
    rm -f /usr/local/bin/gost /usr/local/bin/phormal \
          /usr/local/bin/frps /usr/local/bin/frpc \
          /usr/local/bin/backhaul
    success "Binaries removed"
fi

# ── Remove system user ─────────────────────────

if [[ "$PURGE" == true ]]; then
    log "Removing system user..."
    userdel -r "$SYSTEM_USER" 2>/dev/null || true
    success "System user removed"
fi

echo ""
success "IronShield uninstalled successfully"
if [[ "$PURGE" != true ]]; then
    echo ""
    warn "Data preserved at: $INSTALL_DIR"
    warn "To remove all data, run: sudo bash $0 --purge"
fi
echo ""
