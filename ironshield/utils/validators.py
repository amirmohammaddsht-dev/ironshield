"""
IronShield - Input Validators
Path: ironshield/utils/validators.py
Purpose: Validate user inputs, configs, and network parameters
"""

import re
import ipaddress
from typing import Optional


def is_valid_ip(ip: str) -> bool:
    """Check if a string is a valid IPv4 or IPv6 address."""
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False


def is_valid_port(port: int) -> bool:
    """Check if a port number is valid (1-65535)."""
    return 1 <= port <= 65535


def is_valid_cidr(cidr: str) -> bool:
    """Check if a string is a valid CIDR network notation."""
    try:
        ipaddress.ip_network(cidr, strict=False)
        return True
    except ValueError:
        return False


def is_valid_telegram_token(token: str) -> bool:
    """Check if a string looks like a valid Telegram bot token."""
    pattern = r"^\d+:[A-Za-z0-9_-]{30,}$"
    return bool(re.match(pattern, token.strip()))


def is_valid_telegram_id(user_id: str) -> bool:
    """Check if a string is a valid Telegram user ID."""
    try:
        uid = int(user_id)
        return uid > 0
    except ValueError:
        return False


def is_valid_domain(domain: str) -> bool:
    """Check if a string is a valid domain name."""
    pattern = r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
    return bool(re.match(pattern, domain.strip()))


def is_valid_username(username: str) -> bool:
    """
    Check if a username is valid for VPN user creation.
    Rules: 3-32 chars, alphanumeric + underscore, starts with letter.
    """
    pattern = r"^[a-zA-Z][a-zA-Z0-9_]{2,31}$"
    return bool(re.match(pattern, username))


def sanitize_filename(filename: str) -> str:
    """Remove unsafe characters from a filename."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", filename)


def validate_traffic_gb(value: str) -> Optional[float]:
    """
    Validate and parse a traffic limit value in GB.

    Args:
        value: String like "50", "100.5", "unlimited"

    Returns:
        float: GB value, or None for unlimited/invalid
    """
    if value.lower() in ("unlimited", "0", "inf"):
        return None
    try:
        gb = float(value)
        if gb > 0:
            return gb
    except ValueError:
        pass
    return None


def validate_days(value: str) -> Optional[int]:
    """
    Validate and parse a number of days.

    Args:
        value: String like "30", "365"

    Returns:
        int: Number of days, or None if invalid
    """
    try:
        days = int(value)
        if 1 <= days <= 3650:
            return days
    except ValueError:
        pass
    return None
