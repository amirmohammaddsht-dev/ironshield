"""
IronShield - Smart Routing Engine
Path: ironshield/core/smart_routing.py
Purpose: Selects the best tunnel route based on benchmark scores.
         Implements anti-flapping, pattern learning, and override support.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

from ironshield.core.tunnel_manager import TunnelManager
from ironshield.db.database import Database
from ironshield.db.models import RoutingDecision
from ironshield.utils.logger import get_logger

logger = get_logger("smart_routing")


class RoutingConfig:
    """Smart routing configuration parameters."""

    def __init__(
        self,
        mode: str = "auto",
        cooldown_minutes: int = 10,
        min_score_difference: float = 10.0,
        stability_bonus: float = 5.0,
        consecutive_failures: int = 3,
        pattern_learning: bool = True,
    ):
        self.mode = mode  # auto | manual | emergency
        self.cooldown_sec = cooldown_minutes * 60
        self.min_score_diff = min_score_difference
        self.stability_bonus = stability_bonus
        self.consecutive_failures = consecutive_failures
        self.pattern_learning = pattern_learning


class SmartRoutingEngine:
    """
    Selects and switches tunnel routes intelligently.

    Key features:
    - Anti-flapping: cooldown period + minimum score difference
    - Stability bonus: rewards tunnels that have been stable
    - Manual override: admin can lock a specific tunnel
    - Emergency mode: activates Storm-DNS when all tunnels fail
    - Pattern learning: learns from historical routing decisions
    """

    def __init__(
        self,
        tunnel_manager: TunnelManager,
        db: Database,
        config: Optional[RoutingConfig] = None,
        on_switch: Optional[Callable] = None,
    ):
        self.tm = tunnel_manager
        self.db = db
        self.config = config or RoutingConfig()
        self.on_switch = on_switch  # Callback for notifications

        # State
        self._current_tunnel: Optional[str] = None
        self._last_switch_time: float = 0.0
        self._failure_counts: Dict[str, int] = {}
        self._stable_since: Dict[str, float] = {}
        self._override_tunnel: Optional[str] = None
        self._emergency_mode: bool = False
        self._blocked: List[str] = []

    # ── Main Decision Loop ────────────────────

    def evaluate(self) -> Optional[str]:
        """
        Evaluate current tunnel scores and decide whether to switch.

        Returns:
            Name of selected tunnel (None if no action needed)
        """
        mode = self._override_tunnel and "manual" or self.config.mode

        if mode == "manual" and self._override_tunnel:
            return self._handle_manual_override()

        if self._emergency_mode:
            return self._handle_emergency()

        return self._handle_auto()

    def _handle_manual_override(self) -> Optional[str]:
        """Keep the manually selected tunnel."""
        if self._current_tunnel != self._override_tunnel:
            self._switch_to(self._override_tunnel, reason="manual_override")
        return self._override_tunnel

    def _handle_emergency(self) -> Optional[str]:
        """Use emergency tunnel (Storm-DNS)."""
        emergency = self.tm.get_emergency_tunnel()
        if emergency:
            name = emergency["name"]
            if self._current_tunnel != name:
                self._switch_to(name, reason="emergency")
            return name
        return self._current_tunnel

    def _handle_auto(self) -> Optional[str]:
        """Auto-select best tunnel with anti-flapping protection."""
        # Check if all non-emergency tunnels have failed
        if self.tm.all_non_emergency_failed():
            if not self._emergency_mode:
                logger.warning("All tunnels failed — activating emergency mode")
                self._emergency_mode = True
            return self._handle_emergency()

        # Exit emergency mode if tunnels are back
        if self._emergency_mode:
            best = self.tm.get_best_tunnel(exclude_emergency=True)
            if best and best["status"] == "ACTIVE":
                logger.info("Tunnels recovered — exiting emergency mode")
                self._emergency_mode = False

        # Get best available tunnel
        best = self.tm.get_best_tunnel(exclude_emergency=True)
        if best is None:
            return self._current_tunnel

        # Skip blocked tunnels
        if best["name"] in self._blocked:
            return self._current_tunnel

        # No current tunnel → switch immediately
        if self._current_tunnel is None:
            self._switch_to(best["name"], reason="initial")
            return self._current_tunnel

        # Already on best tunnel
        if self._current_tunnel == best["name"]:
            self._reset_failure_count(best["name"])
            return self._current_tunnel

        # Apply anti-flapping rules
        if not self._should_switch(best):
            return self._current_tunnel

        self._switch_to(best["name"], reason="score_improved")
        return self._current_tunnel

    # ── Switch Decision ───────────────────────

    def _should_switch(self, candidate: Dict) -> bool:
        """
        Apply anti-flapping rules to decide if switching is safe.

        Rules:
        1. Cooldown: minimum time between switches
        2. Minimum score difference to justify switch
        3. Consecutive failures before marking current as failed
        """
        now = time.monotonic()

        # Rule 1: Cooldown period
        if now - self._last_switch_time < self.config.cooldown_sec:
            remaining = self.config.cooldown_sec - (now - self._last_switch_time)
            logger.debug(f"Anti-flapping cooldown: {remaining:.0f}s remaining")
            return False

        # Get current tunnel score
        ranked = self.tm.get_ranked_tunnels()
        current_info = next((t for t in ranked if t["name"] == self._current_tunnel), None)

        if current_info is None:
            return True

        current_score = current_info.get("score") or 0
        candidate_score = candidate.get("score") or 0

        # Apply stability bonus to current tunnel
        stable_since = self._stable_since.get(self._current_tunnel, now)
        stable_minutes = (now - stable_since) / 60
        if stable_minutes >= 30:
            current_score += self.config.stability_bonus
            logger.debug(
                f"Stability bonus applied to {self._current_tunnel}: "
                f"+{self.config.stability_bonus}pts"
            )

        # Rule 2: Minimum score difference
        score_diff = candidate_score - current_score
        if score_diff < self.config.min_score_diff:
            logger.debug(
                f"Score diff too small to switch: "
                f"{candidate['name']}({candidate_score:.1f}) vs "
                f"{self._current_tunnel}({current_score:.1f}) = {score_diff:.1f}pts"
            )
            return False

        logger.info(
            f"Switch justified: {candidate['name']}({candidate_score:.1f}) > "
            f"{self._current_tunnel}({current_score:.1f}) by {score_diff:.1f}pts"
        )
        return True

    # ── Switch Action ─────────────────────────

    def _switch_to(self, tunnel_name: str, reason: str) -> None:
        """
        Execute a tunnel switch and record in DB.

        Args:
            tunnel_name: Target tunnel name
            reason: Reason for switch (for audit)
        """
        from_tunnel = self._current_tunnel
        from_score = self._get_tunnel_score(from_tunnel)
        to_score = self._get_tunnel_score(tunnel_name)

        logger.info(
            f"Switching route: {from_tunnel or 'none'} → {tunnel_name} " f"(reason={reason})"
        )

        # Update DB
        self.tm.mark_as_primary(tunnel_name)
        backup = self.tm.get_backup_tunnel(tunnel_name)
        if backup:
            self.tm.mark_as_backup(backup["name"])

        # Record decision
        self._record_decision(
            from_tunnel=from_tunnel,
            to_tunnel=tunnel_name,
            reason=reason,
            from_score=from_score,
            to_score=to_score,
            is_manual=reason == "manual_override",
            is_emergency=reason == "emergency",
        )

        # Update state
        self._current_tunnel = tunnel_name
        self._last_switch_time = time.monotonic()
        self._stable_since[tunnel_name] = time.monotonic()
        self._reset_failure_count(tunnel_name)

        # Notify (Telegram bot, etc.)
        if self.on_switch:
            try:
                self.on_switch(
                    from_tunnel=from_tunnel,
                    to_tunnel=tunnel_name,
                    reason=reason,
                    from_score=from_score,
                    to_score=to_score,
                )
            except Exception as e:
                logger.warning(f"Switch notification failed: {e}")

    def _record_decision(
        self,
        from_tunnel: Optional[str],
        to_tunnel: str,
        reason: str,
        from_score: Optional[float],
        to_score: Optional[float],
        is_manual: bool = False,
        is_emergency: bool = False,
    ) -> None:
        """Persist routing decision to DB for pattern learning."""
        try:
            with self.db.session() as s:
                s.add(
                    RoutingDecision(
                        from_tunnel=from_tunnel,
                        to_tunnel=to_tunnel,
                        reason=reason,
                        from_score=from_score,
                        to_score=to_score,
                        is_manual=is_manual,
                        is_emergency=is_emergency,
                    )
                )
        except Exception as e:
            logger.warning(f"Failed to record routing decision: {e}")

    # ── Failure Tracking ──────────────────────

    def report_tunnel_failure(self, tunnel_name: str) -> None:
        """
        Report a tunnel failure. After consecutive_failures, trigger switch.

        Args:
            tunnel_name: Name of the failed tunnel
        """
        self._failure_counts[tunnel_name] = self._failure_counts.get(tunnel_name, 0) + 1
        count = self._failure_counts[tunnel_name]

        logger.warning(
            f"Tunnel failure reported: {tunnel_name} "
            f"(consecutive: {count}/{self.config.consecutive_failures})"
        )

        if count >= self.config.consecutive_failures:
            if tunnel_name == self._current_tunnel:
                logger.error(f"Current tunnel {tunnel_name} failed {count} times — forcing switch")
                self._force_switch_away(tunnel_name)

    def _force_switch_away(self, failed_tunnel: str) -> None:
        """Force an immediate switch away from a failed tunnel."""
        backup = self.tm.get_backup_tunnel(failed_tunnel)
        if backup:
            self._switch_to(backup["name"], reason="tunnel_failed")
        elif self.tm.all_non_emergency_failed():
            self._emergency_mode = True
            self._handle_emergency()

    def _reset_failure_count(self, tunnel_name: str) -> None:
        """Reset failure counter for a tunnel."""
        self._failure_counts[tunnel_name] = 0

    # ── Override Controls ─────────────────────

    def set_manual_override(self, tunnel_name: str) -> bool:
        """
        Admin manually selects a specific tunnel.

        Args:
            tunnel_name: Tunnel to force

        Returns:
            bool: True if tunnel exists and is active
        """
        ranked = self.tm.get_ranked_tunnels()
        tunnel = next((t for t in ranked if t["name"] == tunnel_name), None)

        if tunnel is None:
            logger.warning(f"Cannot override to unknown tunnel: {tunnel_name}")
            return False

        self._override_tunnel = tunnel_name
        self.config.mode = "manual"
        self._switch_to(tunnel_name, reason="manual_override")
        logger.info(f"Manual override set: {tunnel_name}")
        return True

    def clear_manual_override(self) -> None:
        """Return to automatic tunnel selection."""
        self._override_tunnel = None
        self.config.mode = "auto"
        logger.info("Manual override cleared — returning to auto mode")

    def block_tunnel(self, tunnel_name: str) -> None:
        """Prevent a tunnel from being selected by auto-routing."""
        if tunnel_name not in self._blocked:
            self._blocked.append(tunnel_name)
            logger.info(f"Tunnel blocked from routing: {tunnel_name}")

    def unblock_tunnel(self, tunnel_name: str) -> None:
        """Allow a previously blocked tunnel to be selected."""
        self._blocked = [t for t in self._blocked if t != tunnel_name]
        logger.info(f"Tunnel unblocked: {tunnel_name}")

    # ── Status ────────────────────────────────

    def get_status(self) -> Dict:
        """Return current routing engine status."""
        ranked = self.tm.get_ranked_tunnels()
        backup = self.tm.get_backup_tunnel(self._current_tunnel or "")

        return {
            "mode": "manual" if self._override_tunnel else self.config.mode,
            "emergency": self._emergency_mode,
            "current_tunnel": self._current_tunnel,
            "backup_tunnel": backup["name"] if backup else None,
            "override": self._override_tunnel,
            "blocked": self._blocked,
            "cooldown_remaining": max(
                0,
                self.config.cooldown_sec - (time.monotonic() - self._last_switch_time),
            ),
            "tunnel_count": len(ranked),
            "active_count": sum(1 for t in ranked if t["status"] == "ACTIVE"),
        }

    def get_recent_decisions(self, limit: int = 10) -> List[Dict]:
        """Return recent routing decisions from DB."""
        try:
            with self.db.session() as s:
                decisions = (
                    s.query(RoutingDecision)
                    .order_by(RoutingDecision.decided_at.desc())
                    .limit(limit)
                    .all()
                )
                return [
                    {
                        "from": d.from_tunnel,
                        "to": d.to_tunnel,
                        "reason": d.reason,
                        "from_score": d.from_score,
                        "to_score": d.to_score,
                        "manual": d.is_manual,
                        "emergency": d.is_emergency,
                        "at": d.decided_at.isoformat(),
                    }
                    for d in decisions
                ]
        except Exception:
            return []

    # ── Helpers ──────────────────────────────

    def _get_tunnel_score(self, tunnel_name: Optional[str]) -> Optional[float]:
        """Get current score for a tunnel from ranked list."""
        if tunnel_name is None:
            return None
        ranked = self.tm.get_ranked_tunnels()
        tunnel = next((t for t in ranked if t["name"] == tunnel_name), None)
        return tunnel.get("score") if tunnel else None
