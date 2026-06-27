"""
IronShield - Alert System
Path: ironshield/bot/handlers/alerts.py
Purpose: Sends Telegram notifications to admins for system events.
         Integrates with HealthCheckEngine and FailoverEngine callbacks.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List, Optional

from telegram import Bot
from telegram.error import TelegramError

from ironshield.bot.middlewares.auth import I18n
from ironshield.utils.logger import get_bot_logger

logger = get_bot_logger()


class AlertSystem:
    """
    Sends alert notifications to admin Telegram users.

    Used as callback by:
    - HealthCheckEngine (service failures/recovery)
    - FailoverEngine (tunnel failures, emergency mode)
    - MonitoringEngine (system resource alerts, user quota alerts)
    """

    def __init__(
        self,
        bot: Bot,
        admin_ids: List[int],
        i18n: I18n,
        proxy_url: Optional[str] = None,
    ):
        self.bot = bot
        self.admin_ids = admin_ids
        self.i18n = i18n
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── Core Send ────────────────────────────

    async def send_to_all_admins(self, text: str) -> None:
        """Send a message to all admin users."""
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    parse_mode="HTML",
                    disable_notification=False,
                )
            except TelegramError as e:
                logger.warning(f"Failed to send alert to admin {admin_id}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error sending to admin {admin_id}: {e}")

    def send_sync(self, text: str) -> None:
        """
        Synchronous wrapper for sending alerts.
        Used as callback from non-async contexts.
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.send_to_all_admins(text))
            else:
                loop.run_until_complete(self.send_to_all_admins(text))
        except Exception as e:
            logger.error(f"Alert send error: {e}")

    # ── Service Alerts ────────────────────────

    def on_service_failure(
        self,
        service_name: str,
        health,
        consecutive_failures: int = 1,
        **kwargs,
    ) -> None:
        """Callback: service has failed."""
        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        text = self.i18n.t(
            "alert_service_down",
            service=service_name,
            server="Iran",
            time=now,
        )
        text += f"\n\n<i>Consecutive failures: {consecutive_failures}</i>"
        self.send_sync(text)

    def on_service_recovery(self, service_name: str, health=None, **kwargs) -> None:
        """Callback: service has recovered."""
        text = self.i18n.t(
            "alert_service_recovered",
            service=service_name,
            downtime="N/A",
        )
        self.send_sync(text)

    # ── Failover Alerts ───────────────────────

    def on_alert(self, title: str, body: str, severity: str, **kwargs) -> None:
        """
        Generic alert callback from FailoverEngine.

        Args:
            title: Alert title
            body: Alert body
            severity: INFO / WARNING / CRITICAL / EMERGENCY
        """
        severity_icons = {
            "INFO": "ℹ️",
            "WARNING": "⚠️",
            "CRITICAL": "🔴",
            "EMERGENCY": "🆘",
        }
        icon = severity_icons.get(severity, "🔔")
        text = f"{icon} <b>{title}</b>\n\n{body}"
        self.send_sync(text)

    def on_recovery(self, plugin_name: str, downtime_seconds: int = 0, **kwargs) -> None:
        """Callback: failover event resolved."""
        minutes = downtime_seconds // 60
        seconds = downtime_seconds % 60
        downtime_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
        text = self.i18n.t(
            "alert_service_recovered",
            service=plugin_name,
            downtime=downtime_str,
        )
        self.send_sync(text)

    # ── System Alerts ─────────────────────────

    def on_system_alert(self, resource: str, value: float, level: str, **kwargs) -> None:
        """Callback: system resource threshold exceeded."""
        key_map = {
            "CPU": "alert_cpu_high",
            "RAM": "alert_ram_high",
            "Disk": "alert_disk_high",
        }
        key = key_map.get(resource, "alert_cpu_high")
        text = self.i18n.t(key, server="Iran", value=value)
        self.send_sync(text)

    # ── Routing Alerts ────────────────────────

    def on_tunnel_switch(
        self,
        from_tunnel: Optional[str],
        to_tunnel: str,
        reason: str,
        from_score: Optional[float] = None,
        to_score: Optional[float] = None,
        **kwargs,
    ) -> None:
        """Notification when Smart Routing switches tunnels."""
        if reason == "initial":
            return  # Don't alert on initial selection

        reason_labels = {
            "score_improved": "📈 بهبود کیفیت",
            "score_degraded": "📉 افت کیفیت",
            "tunnel_failed": "🔴 خرابی تانل",
            "manual_override": "👤 تغییر دستی",
            "emergency": "🆘 حالت اضطراری",
            "recovery": "✅ بازیابی",
        }
        label = reason_labels.get(reason, reason)

        score_str = ""
        if from_score is not None and to_score is not None:
            score_str = f"\nامتیاز: {from_score:.0f} → {to_score:.0f}"

        text = (
            f"🔀 <b>تغییر مسیر</b>\n\n"
            f"از: {from_tunnel or 'هیچ'}\n"
            f"به: {to_tunnel}\n"
            f"دلیل: {label}"
            f"{score_str}"
        )
        self.send_sync(text)

    # ── User Quota Alerts ─────────────────────

    async def check_and_alert_users(self, db) -> None:
        """
        Check all users for expiry/quota warnings and send alerts.
        Called daily by the monitoring scheduler.
        """
        try:
            from ironshield.db.models import User

            now = datetime.now(timezone.utc)
            warning_days = 3

            with db.session() as s:
                users = s.query(User).filter_by(is_active=True).all()

                for user in users:
                    # Expiry warning
                    if user.expire_at:
                        days_left = (user.expire_at - now).days
                        if 0 < days_left <= warning_days:
                            text = self.i18n.t(
                                "alert_user_expiring",
                                username=user.username,
                                days=days_left,
                            )
                            await self.send_to_all_admins(text)

                            # Also notify the user if they have Telegram
                            if user.telegram_id:
                                try:
                                    await self.bot.send_message(
                                        chat_id=user.telegram_id,
                                        text=text,
                                        parse_mode="HTML",
                                    )
                                except Exception:
                                    pass

                    # Quota warning (80%)
                    percent = user.traffic_percent_used
                    if percent is not None and percent >= 80:
                        text = self.i18n.t(
                            "alert_user_quota",
                            username=user.username,
                            percent=percent,
                        )
                        await self.send_to_all_admins(text)

        except Exception as e:
            logger.error(f"User quota check failed: {e}")
