"""
IronShield - Security utilities
Path: ironshield/utils/security.py
Purpose: Shared constant-time comparison for API keys/tokens, used by
         both the core API (api/server.py) and the Agent API
         (agent/api.py) to avoid two independently-maintained,
         potentially-diverging copies of the same security-sensitive logic.

         See ADR-001-api-auth-centralization.md for the design rationale.
         Findings addressed: F-002 (core API token comparison),
         F-003 (agent X-Agent-Key comparison).
"""

from __future__ import annotations

import secrets
from typing import Any


def constant_time_compare(provided: Any, expected: Any) -> bool:
    """
    Compare a provided value against an expected secret in constant time.

    Returns False (never raises) if either `provided` or `expected` is
    not a string, so callers can pass untrusted/malformed input directly
    without a separate type check. `secrets.compare_digest` requires both
    arguments to be of the same type (str/str or bytes/bytes); validating
    both sides here — not just `provided` — avoids a TypeError if
    `expected` is ever something other than a plain str (e.g. None, by a
    future caller that forgets its own None-check before calling this
    function).
    """
    if not isinstance(provided, str) or not isinstance(expected, str):
        return False
    return secrets.compare_digest(provided, expected)
