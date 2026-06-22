"""
IronShield - Database Models
Path: ironshield/db/models.py
Purpose: SQLAlchemy ORM models for all IronShield data
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, List

from sqlalchemy import (
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Text,
    BigInteger,
    ForeignKey,
    Enum as SAEnum,
    Index,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    """Return current UTC time."""
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


# ─────────────────────────────────────────────
# User Model
# ─────────────────────────────────────────────


class User(Base):
    """
    VPN user account.
    Each user gets an OpenVPN certificate and config file.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(String(36), unique=True, default=_new_uuid, index=True)

    # Identity
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    telegram_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, unique=True, nullable=True, index=True
    )

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Traffic limits (bytes — None = unlimited)
    traffic_limit_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    traffic_used_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # Expiry
    expire_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
    last_connected_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Notes
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    vpn_configs: Mapped[List["VPNConfig"]] = relationship(
        "VPNConfig", back_populates="user", cascade="all, delete-orphan"
    )
    traffic_logs: Mapped[List["TrafficLog"]] = relationship(
        "TrafficLog", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User {self.username} active={self.is_active}>"

    @property
    def traffic_limit_gb(self) -> Optional[float]:
        """Traffic limit in GB."""
        if self.traffic_limit_bytes is None:
            return None
        return self.traffic_limit_bytes / (1024**3)

    @property
    def traffic_used_gb(self) -> float:
        """Traffic used in GB."""
        return self.traffic_used_bytes / (1024**3)

    @property
    def traffic_remaining_gb(self) -> Optional[float]:
        """Remaining traffic in GB."""
        if self.traffic_limit_bytes is None:
            return None
        remaining = self.traffic_limit_bytes - self.traffic_used_bytes
        return max(0.0, remaining / (1024**3))

    @property
    def traffic_percent_used(self) -> Optional[float]:
        """Percentage of traffic used."""
        if self.traffic_limit_bytes is None or self.traffic_limit_bytes == 0:
            return None
        return (self.traffic_used_bytes / self.traffic_limit_bytes) * 100

    @property
    def is_expired(self) -> bool:
        """Check if user account is expired."""
        if self.expire_at is None:
            return False
        return datetime.now(timezone.utc) > self.expire_at

    @property
    def is_over_quota(self) -> bool:
        """Check if user exceeded traffic limit."""
        if self.traffic_limit_bytes is None:
            return False
        return self.traffic_used_bytes >= self.traffic_limit_bytes

    @property
    def days_until_expiry(self) -> Optional[int]:
        """Days remaining until account expires."""
        if self.expire_at is None:
            return None
        delta = self.expire_at - datetime.now(timezone.utc)
        return max(0, delta.days)


# ─────────────────────────────────────────────
# VPN Config Model
# ─────────────────────────────────────────────


class VPNConfig(Base):
    """
    OpenVPN configuration file for a user.
    Stores the .ovpn file content (encrypted).
    """

    __tablename__ = "vpn_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)

    # Config file (encrypted)
    config_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # Certificate info
    cert_serial: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    cert_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    # Relationship
    user: Mapped["User"] = relationship("User", back_populates="vpn_configs")

    def __repr__(self) -> str:
        return f"<VPNConfig user_id={self.user_id} revoked={self.is_revoked}>"


# ─────────────────────────────────────────────
# Tunnel Model
# ─────────────────────────────────────────────


class Tunnel(Base):
    """
    Tunnel plugin instance and its current state.
    """

    __tablename__ = "tunnels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Identity
    plugin_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    server_role: Mapped[str] = mapped_column(
        SAEnum("iran", "foreign", "both", name="server_role_enum"),
        nullable=False,
    )

    # Status
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(
        SAEnum(
            "ACTIVE",
            "DEGRADED",
            "FAILED",
            "STANDBY",
            "DISABLED",
            "ESTABLISHING",
            "UNKNOWN",
            name="tunnel_status_enum",
        ),
        default="UNKNOWN",
        nullable=False,
    )

    # Latest benchmark results
    latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    real_delay_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    packet_loss_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    throughput_mbps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Routing priority
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_backup: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_emergency: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Timestamps
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_switched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    # Relationships
    metrics: Mapped[List["TunnelMetric"]] = relationship(
        "TunnelMetric", back_populates="tunnel", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Tunnel {self.plugin_name} status={self.status} score={self.score}>"


# ─────────────────────────────────────────────
# Tunnel Metric Model (Time Series)
# ─────────────────────────────────────────────


class TunnelMetric(Base):
    """
    Historical benchmark data for a tunnel.
    Used for Pattern Analyzer and Smart Routing.
    """

    __tablename__ = "tunnel_metrics"
    __table_args__ = (Index("ix_tunnel_metrics_tunnel_ts", "tunnel_id", "recorded_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tunnel_id: Mapped[int] = mapped_column(Integer, ForeignKey("tunnels.id"), nullable=False)

    # Benchmark results
    latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    real_delay_small_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    real_delay_medium_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    real_delay_large_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    packet_loss_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    throughput_mbps: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Test metadata
    test_type: Mapped[str] = mapped_column(
        SAEnum("quick", "standard", "full", name="test_type_enum"),
        default="quick",
        nullable=False,
    )
    resolution: Mapped[str] = mapped_column(
        SAEnum("realtime", "hourly", "daily", name="resolution_enum"),
        default="realtime",
        nullable=False,
    )

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    # Relationship
    tunnel: Mapped["Tunnel"] = relationship("Tunnel", back_populates="metrics")

    def __repr__(self) -> str:
        return f"<TunnelMetric tunnel_id={self.tunnel_id} score={self.score} at={self.recorded_at}>"


# ─────────────────────────────────────────────
# System Metric Model (Time Series)
# ─────────────────────────────────────────────


class SystemMetric(Base):
    """
    Server resource metrics (CPU, RAM, Disk, Network).
    Collected from both Iran and Foreign servers.
    """

    __tablename__ = "system_metrics"
    __table_args__ = (Index("ix_system_metrics_server_ts", "server", "recorded_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    server: Mapped[str] = mapped_column(
        SAEnum("iran", "foreign", name="metric_server_enum"),
        nullable=False,
    )
    resolution: Mapped[str] = mapped_column(
        SAEnum("realtime", "hourly", "daily", name="metric_resolution_enum"),
        default="realtime",
        nullable=False,
    )

    # CPU
    cpu_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cpu_load_1m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cpu_load_5m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cpu_load_15m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Memory
    ram_total_gb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ram_used_gb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ram_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Disk
    disk_total_gb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    disk_used_gb: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    disk_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Network (bytes/sec)
    net_bytes_sent: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    net_bytes_recv: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<SystemMetric server={self.server} cpu={self.cpu_percent}% at={self.recorded_at}>"


# ─────────────────────────────────────────────
# Traffic Log Model
# ─────────────────────────────────────────────


class TrafficLog(Base):
    """
    Per-user traffic consumption log.
    Updated periodically from OpenVPN status log.
    """

    __tablename__ = "traffic_logs"
    __table_args__ = (Index("ix_traffic_logs_user_ts", "user_id", "recorded_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)

    bytes_sent: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bytes_received: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # Connection info
    client_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    virtual_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    connected_since: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    # Relationship
    user: Mapped["User"] = relationship("User", back_populates="traffic_logs")

    def __repr__(self) -> str:
        return (
            f"<TrafficLog user_id={self.user_id} sent={self.bytes_sent} recv={self.bytes_received}>"
        )


# ─────────────────────────────────────────────
# Failover Event Model
# ─────────────────────────────────────────────


class FailoverEvent(Base):
    """
    Record of every failover action taken by the system.
    """

    __tablename__ = "failover_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    event_type: Mapped[str] = mapped_column(
        SAEnum(
            "service_failed",
            "service_recovered",
            "tunnel_failed",
            "tunnel_recovered",
            "all_tunnels_failed",
            "emergency_activated",
            "system_critical",
            name="failover_event_type_enum",
        ),
        nullable=False,
    )
    severity: Mapped[str] = mapped_column(
        SAEnum("WARNING", "CRITICAL", "EMERGENCY", name="failover_severity_enum"),
        nullable=False,
    )

    # What failed
    plugin_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    server: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # What was done
    action_taken: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    restart_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Notification
    admin_notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Error details
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    log_excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timeline
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def downtime_seconds(self) -> Optional[int]:
        """Calculate downtime duration in seconds."""
        if self.resolved_at is None:
            return None
        return int((self.resolved_at - self.occurred_at).total_seconds())

    def __repr__(self) -> str:
        return f"<FailoverEvent {self.event_type} severity={self.severity}>"


# ─────────────────────────────────────────────
# Audit Log Model
# ─────────────────────────────────────────────


class AuditLog(Base):
    """
    Immutable audit trail for all important system actions.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Who
    performed_by: Mapped[str] = mapped_column(
        SAEnum("admin_bot", "admin_cli", "system", "api", name="audit_actor_enum"),
        nullable=False,
    )
    actor_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # What
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # Details
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Result
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} by={self.performed_by} success={self.success}>"


# ─────────────────────────────────────────────
# Routing Decision Model
# ─────────────────────────────────────────────


class RoutingDecision(Base):
    """
    History of Smart Routing decisions.
    Used by Pattern Analyzer for learning.
    """

    __tablename__ = "routing_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Decision
    from_tunnel: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    to_tunnel: Mapped[str] = mapped_column(String(64), nullable=False)

    reason: Mapped[str] = mapped_column(
        SAEnum(
            "initial",
            "score_improved",
            "score_degraded",
            "tunnel_failed",
            "manual_override",
            "emergency",
            "recovery",
            "scheduled",
            name="routing_reason_enum",
        ),
        nullable=False,
    )

    # Scores at time of decision
    from_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    to_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Mode
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_emergency: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<RoutingDecision {self.from_tunnel} → {self.to_tunnel} reason={self.reason}>"


# ─────────────────────────────────────────────
# Settings Model
# ─────────────────────────────────────────────


class Setting(Base):
    """
    Key-value store for runtime settings.
    Overrides config file values at runtime.
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(
        SAEnum("str", "int", "float", "bool", "json", name="setting_type_enum"),
        default="str",
        nullable=False,
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<Setting {self.key}={self.value}>"
