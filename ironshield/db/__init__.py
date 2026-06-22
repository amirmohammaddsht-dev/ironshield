"""IronShield database layer."""

from ironshield.db.database import get_db, init_db
from ironshield.db.models import (
    Base,
    User,
    VPNConfig,
    Tunnel,
    TunnelMetric,
    SystemMetric,
    TrafficLog,
    FailoverEvent,
    AuditLog,
    RoutingDecision,
    Setting,
)

__all__ = [
    "get_db",
    "init_db",
    "Base",
    "User",
    "VPNConfig",
    "Tunnel",
    "TunnelMetric",
    "SystemMetric",
    "TrafficLog",
    "FailoverEvent",
    "AuditLog",
    "RoutingDecision",
    "Setting",
]
