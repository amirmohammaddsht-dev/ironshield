#!/usr/bin/env bash
# IronShield - Main Installer
# Usage: curl -sSL https://raw.githubusercontent.com/amirmohammaddsht-dev/ironshield/main/scripts/install.sh | bash
#
# This script:
#   1. Checks system requirements
#   2. Installs Python 3.11+
#   3. Creates ironshield system user
#   4. Clones/downloads the repository
#   5. Installs Python dependencies
#   6. Launches the interactive CLI installer

set -euo pipefail

# Prevent any debconf prompt (e.g. iperf3's "start as daemon?" question)
# from blocking unattended/piped installs.
export DEBIAN_FRONTEND=noninteractive

# ── Constants ─────────────────────────────────

REPO_URL="https://github.com/amirmohammaddsht-dev/ironshield"
INSTALL_DIR="/opt/ironshield"
SYSTEM_USER="ironshield"
LOG_FILE="/tmp/ironshield_install.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ── Helpers ───────────────────────────────────

log()     { echo -e "${BLUE}[INFO]${NC}  $*" | tee -a "$LOG_FILE"; }
success() { echo -e "${GREEN}[OK]${NC}    $*" | tee -a "$LOG_FILE"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*" | tee -a "$LOG_FILE"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" | tee -a "$LOG_FILE"; }
die()     { error "$*"; exit 1; }

# ── Pre-flight Checks ─────────────────────────

check_root() {
    if [[ $EUID -ne 0 ]]; then
        die "This script must be run as root or with sudo."
    fi
    success "Running as root"
}

check_os() {
    if [[ ! -f /etc/os-release ]]; then
        die "Cannot detect OS. Ubuntu required."
    fi
    # shellcheck disable=SC1091
    source /etc/os-release
    if [[ "$ID" != "ubuntu" ]]; then
        die "Ubuntu required. Detected: $ID"
    fi
    local ver="${VERSION_ID:-0}"
    log "Ubuntu $ver detected"
    # Warn on older versions but don't block
    if [[ $(echo "$ver < 20.04" | bc -l) -eq 1 ]]; then
        warn "Ubuntu 20.04+ recommended. Proceeding anyway."
    fi
    success "OS check passed"
}

check_ram() {
    local ram_mb
    ram_mb=$(awk '/MemAvailable/ {printf "%.0f", $2/1024}' /proc/meminfo)
    if [[ $ram_mb -lt 256 ]]; then
        die "Not enough RAM. Need at least 256MB free (have ${ram_mb}MB)."
    fi
    success "RAM: ${ram_mb}MB available"
}

check_disk() {
    local disk_gb
    disk_gb=$(df -BG / | awk 'NR==2 {gsub("G","",$4); print $4}')
    if [[ $disk_gb -lt 2 ]]; then
        die "Not enough disk space. Need at least 2GB free (have ${disk_gb}GB)."
    fi
    success "Disk: ${disk_gb}GB available"
}

check_internet() {
    if ! curl -s --max-time 5 https://api.github.com > /dev/null 2>&1; then
        die "No internet connection. Required for installation."
    fi
    success "Internet connection OK"
}

# ── Python Installation ───────────────────────

install_python() {
    log "Checking Python 3.11+..."

    # Check if suitable Python already exists
    for py in python3.12 python3.11 python3; do
        if command -v "$py" &>/dev/null; then
            local ver
            ver=$("$py" --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
            local major minor
            major=$(echo "$ver" | cut -d. -f1)
            minor=$(echo "$ver" | cut -d. -f2)
            if [[ $major -ge 3 && $minor -ge 11 ]]; then
                PYTHON_BIN="$py"
                success "Python $ver found at $(command -v $py)"
                return 0
            fi
        fi
    done

    # Install Python 3.11
    log "Installing Python 3.11..."
    apt-get -o DPkg::Lock::Timeout=60 update -q >> "$LOG_FILE" 2>&1
    apt-get -o DPkg::Lock::Timeout=60 install -y -q python3.11 python3.11-venv python3.11-dev python3-pip >> "$LOG_FILE" 2>&1
    PYTHON_BIN="python3.11"
    success "Python 3.11 installed"
}

# ── System User ───────────────────────────────

create_user() {
    if id "$SYSTEM_USER" &>/dev/null; then
        log "System user '$SYSTEM_USER' already exists"
        return 0
    fi

    useradd -r -s /usr/sbin/nologin -d "$INSTALL_DIR" -m "$SYSTEM_USER" >> "$LOG_FILE" 2>&1
    success "System user '$SYSTEM_USER' created"
}

setup_sudoers() {
    local sudoers_file="/etc/sudoers.d/ironshield"
    cat > "$sudoers_file" << SUDOERS
# IronShield sudo rules — managed automatically
ironshield ALL=(ALL) NOPASSWD: \\
    /bin/systemctl start ironshield-*, \\
    /bin/systemctl stop ironshield-*, \\
    /bin/systemctl restart ironshield-*, \\
    /bin/systemctl status ironshield-*, \\
    /bin/systemctl enable ironshield-*, \\
    /bin/systemctl disable ironshield-*, \\
    /sbin/ip route *, \\
    /sbin/ip link *, \\
    /sbin/ip tunnel *, \\
    /sbin/ip addr *, \\
    /sbin/iptables -t nat *, \\
    /sbin/iptables -t filter *, \\
    /usr/sbin/ufw allow *, \\
    /usr/sbin/ufw deny *, \\
    /usr/sbin/ufw delete *, \\
    /usr/sbin/ufw status *, \\
    /usr/sbin/ufw --force *, \\
    /usr/sbin/ufw default *
SUDOERS
    chmod 440 "$sudoers_file"
    success "Sudoers rules configured"
}

# ── Directory Setup ───────────────────────────

setup_directories() {
    local dirs=(
        "$INSTALL_DIR"
        "$INSTALL_DIR/logs"
        "$INSTALL_DIR/db"
        "$INSTALL_DIR/configs"
        "$INSTALL_DIR/configs/openvpn"
        "$INSTALL_DIR/configs/openvpn/clients"
        "$INSTALL_DIR/configs/tunnels"
        "$INSTALL_DIR/keys"
    )

    for dir in "${dirs[@]}"; do
        mkdir -p "$dir"
    done

    # Set permissions
    chmod 750 "$INSTALL_DIR" "$INSTALL_DIR/logs"
    chmod 700 "$INSTALL_DIR/db" "$INSTALL_DIR/configs" "$INSTALL_DIR/keys"
    chown -R "$SYSTEM_USER:$SYSTEM_USER" "$INSTALL_DIR"
    success "Directories created"
}

# ── Repository Download ───────────────────────

download_repo() {
    log "Downloading IronShield..."

    # Install git if needed
    if ! command -v git &>/dev/null; then
        apt-get -o DPkg::Lock::Timeout=60 install -y -q git >> "$LOG_FILE" 2>&1
    fi

    if [[ -d "$INSTALL_DIR/.git" ]]; then
        log "Repository exists — pulling latest..."
        git -C "$INSTALL_DIR" pull origin main >> "$LOG_FILE" 2>&1
    else
        # $INSTALL_DIR is never empty here: useradd -m already populated
        # it with dotfiles from /etc/skel (and setup_directories may have
        # added logs/db/keys). `git clone` refuses a non-empty target, so
        # clone to a temp dir and merge its contents in instead.
        local tmp_clone
        tmp_clone=$(mktemp -d)
        git clone --depth=1 "$REPO_URL" "$tmp_clone" >> "$LOG_FILE" 2>&1
        shopt -s dotglob
        cp -a "$tmp_clone"/. "$INSTALL_DIR"/
        shopt -u dotglob
        rm -rf "$tmp_clone"
    fi

    chown -R "$SYSTEM_USER:$SYSTEM_USER" "$INSTALL_DIR"
    success "Repository downloaded"
}

# ── Python Virtual Environment ────────────────

setup_venv() {
    local venv_dir="$INSTALL_DIR/venv"

    if [[ ! -d "$venv_dir" ]]; then
        log "Creating Python virtual environment..."
        sudo -u "$SYSTEM_USER" bash -c "'$PYTHON_BIN' -m venv '$venv_dir'" >> "$LOG_FILE" 2>&1
    fi

    log "Installing Python dependencies..."
    sudo -u "$SYSTEM_USER" bash -c "'$venv_dir/bin/pip' install --quiet --upgrade pip" >> "$LOG_FILE" 2>&1
    sudo -u "$SYSTEM_USER" bash -c "'$venv_dir/bin/pip' install --quiet -r '$INSTALL_DIR/requirements.txt'" >> "$LOG_FILE" 2>&1
    sudo -u "$SYSTEM_USER" bash -c "'$venv_dir/bin/pip' install --quiet -e '$INSTALL_DIR'" >> "$LOG_FILE" 2>&1

    success "Python environment ready"
}

# ── Systemd Services ──────────────────────────

install_systemd_services() {
    local templates_dir="$INSTALL_DIR/configs/templates/systemd"
    local systemd_dir="/etc/systemd/system"

    for service_file in "$templates_dir"/*.service; do
        local service_name
        service_name=$(basename "$service_file")
        cp "$service_file" "$systemd_dir/$service_name"
        log "Installed systemd service: $service_name"
    done

    systemctl daemon-reload >> "$LOG_FILE" 2>&1
    success "Systemd services installed"
}

# ── Required Packages ─────────────────────────

install_base_packages() {
    log "Installing required system packages..."
    apt-get -o DPkg::Lock::Timeout=60 update -q >> "$LOG_FILE" 2>&1
    apt-get -o DPkg::Lock::Timeout=60 install -y -q \
        curl wget git ufw iptables \
        net-tools iproute2 fping mtr iperf3 \
        build-essential libssl-dev libffi-dev \
        python3-pip python3-venv \
        >> "$LOG_FILE" 2>&1
    success "Base packages installed"
}

# ── UFW Initialization ────────────────────────

init_ufw() {
    log "Initializing UFW firewall..."
    ufw --force reset >> "$LOG_FILE" 2>&1
    ufw default deny incoming >> "$LOG_FILE" 2>&1
    ufw default allow outgoing >> "$LOG_FILE" 2>&1
    ufw allow 22/tcp comment 'SSH' >> "$LOG_FILE" 2>&1
    ufw --force enable >> "$LOG_FILE" 2>&1
    success "UFW initialized (SSH allowed, all else denied)"
}

# ── Launch Installer ──────────────────────────

launch_installer() {
    log "Launching IronShield interactive installer..."
    echo ""
    exec sudo -u "$SYSTEM_USER" \
        -E "$INSTALL_DIR/venv/bin/python" \
        -m ironshield.cli.main install
}

# ── Main ──────────────────────────────────────

main() {
    echo ""
    echo "╔══════════════════════════════════════════╗"
    echo "║        🛡️  IronShield Installer          ║"
    echo "╚══════════════════════════════════════════╝"
    echo ""

    log "Installation log: $LOG_FILE"
    echo ""

    # Pre-flight
    check_root
    check_os
    check_ram
    check_disk
    check_internet
    echo ""

    # System setup
    log "Step 1/7: Installing base packages..."
    install_base_packages

    log "Step 2/7: Installing Python 3.11+..."
    install_python

    log "Step 3/7: Creating system user..."
    create_user
    setup_sudoers

    log "Step 4/7: Setting up directories..."
    setup_directories

    log "Step 5/7: Downloading IronShield..."
    download_repo

    log "Step 6/7: Setting up Python environment..."
    setup_venv

    log "Step 7/7: Installing systemd services..."
    install_systemd_services
    init_ufw

    echo ""
    success "Bootstrap complete! Launching installer..."
    echo ""

    launch_installer
}

main "$@"
