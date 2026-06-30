#!/usr/bin/env bash
# IronShield - Dependency Checker
# Usage: bash check_deps.sh
# Checks for required system binaries and reports missing ones.

set -uo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

REQUIRED_BINS=(
    curl wget git
    python3 pip3
    systemctl
    ufw iptables
    ip
)

OPTIONAL_BINS=(
    fping mtr iperf3
    dig
    jq
)

missing_required=()
missing_optional=()

echo "Checking required dependencies..."
for bin in "${REQUIRED_BINS[@]}"; do
    if command -v "$bin" &>/dev/null; then
        echo -e "  ${GREEN}✓${NC} $bin"
    else
        echo -e "  ${RED}✗${NC} $bin"
        missing_required+=("$bin")
    fi
done

echo ""
echo "Checking optional dependencies (used for benchmarking)..."
for bin in "${OPTIONAL_BINS[@]}"; do
    if command -v "$bin" &>/dev/null; then
        echo -e "  ${GREEN}✓${NC} $bin"
    else
        echo -e "  ${YELLOW}○${NC} $bin (optional)"
        missing_optional+=("$bin")
    fi
done

echo ""

if [[ ${#missing_required[@]} -gt 0 ]]; then
    echo -e "${RED}Missing required dependencies:${NC} ${missing_required[*]}"
    echo "Install with: apt-get install -y ${missing_required[*]}"
    exit 1
fi

if [[ ${#missing_optional[@]} -gt 0 ]]; then
    echo -e "${YELLOW}Missing optional dependencies:${NC} ${missing_optional[*]}"
    echo "Install for full benchmark support: apt-get install -y ${missing_optional[*]}"
fi

echo -e "${GREEN}All required dependencies are present.${NC}"
exit 0
