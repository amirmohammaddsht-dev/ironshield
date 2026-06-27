"""
IronShield - Telegram Bot Main
Path: ironshield/bot/main.py
Purpose: Assembles all bot handlers and starts the polling loop.
         Connects through the active tunnel via SOCKS5 proxy.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from ironshield.api.client import APIClient
from ironshield.bot.handlers.admin import AdminHandlers, WAITING_USERNAME, WAITING_DAYS
from ironshield.bot.handlers.alerts import AlertSystem
from ironshield.bot.handlers.user import UserHandlers
from ironshield.bot.middlewares.auth import AuthMiddleware, I18n, RateLimitMiddleware
from ironshield.db.database import Database
from ironshield.utils.logger import get_bot_logger

logger = get_bot_logger()


class IronShieldBot:
    """
    Main Telegram bot class for IronShield.

    Responsibilities:
    - Connect to Telegram API via SOCKS5 proxy (through tunnel)
    - Register all admin and user handlers
    - Start polling loop
    - Provide alert system for other engines
    """

    def __init__(
        self,
        token: str,
        admin_ids: List[int],
        db: Database,
        socket_path: Optional[Path] = None,
        proxy_url: Optional[str] = None,
    ):
        self.token = token
        self.admin_ids = admin_ids
        self.db = db
        self.socket_path = socket_path
        self.proxy_url = proxy_url

        # Components
        self.i18n = I18n()
        self.auth = AuthMiddleware(admin_ids=admin_ids, db=db, i18n=self.i18n)
        self.rate = RateLimitMiddleware(max_requests=10, window_seconds=60)
        self.api = APIClient(socket_path=socket_path) if socket_path else None

        self._app: Optional[Application] = None
        self._alert_system: Optional[AlertSystem] = None

    # ── Build Application ─────────────────────

    def _build_app(self) -> Application:
        """Build and configure the Telegram Application."""
        builder = ApplicationBuilder().token(self.token)

        # Connect via SOCKS5 proxy if configured (through tunnel)
        if self.proxy_url:
            request = HTTPXRequest(proxy=self.proxy_url)
            builder = builder.request(request)
            logger.info(f"Bot using proxy: {self.proxy_url}")

        app = builder.build()
        return app

    # ── Register Handlers ─────────────────────

    def _register_handlers(self, app: Application) -> None:
        """Register all command and callback handlers."""
        # Create handler instances
        admin = AdminHandlers(self.api, self.auth, self.rate, self.i18n)
        user = UserHandlers(self.api, self.auth, self.rate, self.i18n, self.db, self.admin_ids)

        # ── /start command ──
        app.add_handler(CommandHandler("start", self._start_router(admin, user)))

        # ── Add user conversation ──
        add_user_conv = ConversationHandler(
            entry_points=[CallbackQueryHandler(admin.user_add_start, pattern="^users:add$")],
            states={
                WAITING_USERNAME: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, admin.user_add_username)
                ],
                WAITING_DAYS: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, admin.user_add_days)
                ],
            },
            fallbacks=[CommandHandler("cancel", self._cancel)],
        )
        app.add_handler(add_user_conv)

        # ── Admin callbacks ──
        app.add_handler(CallbackQueryHandler(admin.main_menu, pattern="^admin:back$"))
        app.add_handler(CallbackQueryHandler(admin.main_menu, pattern="^admin:refresh$"))
        app.add_handler(CallbackQueryHandler(admin.services, pattern="^admin:services$"))
        app.add_handler(CallbackQueryHandler(admin.users_menu_handler, pattern="^admin:users$"))
        app.add_handler(CallbackQueryHandler(admin.monitoring, pattern="^admin:monitoring$"))
        app.add_handler(CallbackQueryHandler(admin.tunnels_handler, pattern="^admin:tunnels$"))
        app.add_handler(CallbackQueryHandler(admin.plugins_handler, pattern="^admin:plugins$"))
        app.add_handler(CallbackQueryHandler(admin.settings_handler, pattern="^admin:settings$"))

        # ── Service callbacks ──
        app.add_handler(CallbackQueryHandler(admin.service_detail, pattern="^service:detail:"))
        app.add_handler(
            CallbackQueryHandler(admin.service_action, pattern="^service:(start|stop|restart):")
        )
        app.add_handler(CallbackQueryHandler(admin.service_logs, pattern="^service:logs:"))

        # ── User management callbacks ──
        app.add_handler(CallbackQueryHandler(admin.users_list, pattern="^users:list"))
        app.add_handler(CallbackQueryHandler(admin.user_detail, pattern="^user:detail:"))
        app.add_handler(CallbackQueryHandler(admin.user_toggle, pattern="^user:toggle:"))
        app.add_handler(CallbackQueryHandler(admin.user_delete_confirm, pattern="^user:delete:"))
        app.add_handler(
            CallbackQueryHandler(admin.user_delete_execute, pattern="^user:confirm_delete:")
        )
        app.add_handler(CallbackQueryHandler(admin.user_get_config, pattern="^user:config:"))

        # ── Monitoring callbacks ──
        app.add_handler(
            CallbackQueryHandler(admin.monitor_server, pattern="^monitor:(iran|foreign)$")
        )
        app.add_handler(CallbackQueryHandler(admin.monitor_tunnels, pattern="^monitor:tunnels$"))
        app.add_handler(
            CallbackQueryHandler(admin.benchmark_menu_handler, pattern="^monitor:benchmark$")
        )
        app.add_handler(CallbackQueryHandler(admin.monitor_server, pattern="^monitor:refresh$"))
        app.add_handler(
            CallbackQueryHandler(admin.benchmark_run, pattern="^benchmark:(quick|full)$")
        )

        # ── Tunnel callbacks ──
        app.add_handler(
            CallbackQueryHandler(admin.tunnel_switch_handler, pattern="^tunnel:switch:")
        )
        app.add_handler(CallbackQueryHandler(admin.tunnel_auto_handler, pattern="^tunnel:auto$"))

        # ── Language callbacks ──
        app.add_handler(
            CallbackQueryHandler(admin.language_menu_handler, pattern="^settings:language$")
        )
        app.add_handler(CallbackQueryHandler(admin.set_language, pattern="^lang:(fa|en)$"))

        # ── User panel callbacks ──
        app.add_handler(CallbackQueryHandler(user.main_menu, pattern="^user:menu$"))
        app.add_handler(CallbackQueryHandler(user.subscription, pattern="^user:subscription$"))
        app.add_handler(CallbackQueryHandler(user.get_config, pattern="^user:get_config$"))
        app.add_handler(CallbackQueryHandler(user.get_qr, pattern="^user:qr$"))
        app.add_handler(CallbackQueryHandler(user.guide, pattern="^user:guide$"))
        app.add_handler(CallbackQueryHandler(user.support, pattern="^user:support$"))

        # ── Message fallback (support messages) ──
        app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                user.support_message,
            )
        )

        logger.info("All bot handlers registered")

    def _start_router(self, admin: AdminHandlers, user: UserHandlers):
        """Route /start to admin or user handler based on identity."""

        async def router(update: Update, ctx) -> None:
            uid = update.effective_user.id
            if self.auth.is_admin(uid):
                await admin.start(update, ctx)
            else:
                await user.start(update, ctx)

        return router

    @staticmethod
    async def _cancel(update: Update, ctx) -> int:
        """Cancel an active conversation."""
        from telegram.ext import ConversationHandler

        await update.message.reply_text("❌ عملیات لغو شد.")
        return ConversationHandler.END

    # ── Alert System ──────────────────────────

    def get_alert_system(self) -> Optional[AlertSystem]:
        """Return the alert system for use by other engines."""
        return self._alert_system

    # ── Lifecycle ─────────────────────────────

    async def start(self) -> None:
        """Build, register handlers, and start polling."""
        self._app = self._build_app()
        self._register_handlers(self._app)

        # Set up alert system
        self._alert_system = AlertSystem(
            bot=self._app.bot,
            admin_ids=self.admin_ids,
            i18n=self.i18n,
        )

        logger.info("Starting IronShield Telegram bot (polling)...")

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )

        logger.info("Bot is running")

        # Keep running until stopped
        await asyncio.Event().wait()

    async def stop(self) -> None:
        """Stop the bot gracefully."""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        logger.info("Bot stopped")


def create_bot_from_config(config: dict, db: Database) -> IronShieldBot:
    """
    Factory function to create a bot from IronShield config dict.

    Args:
        config: Full IronShield configuration
        db: Database instance

    Returns:
        Configured IronShieldBot
    """
    tg_cfg = config.get("telegram", {})
    token = tg_cfg.get("token", "")
    admin_ids = [int(uid) for uid in tg_cfg.get("admin_ids", [])]

    # Proxy config (SOCKS5 through tunnel)
    proxy_cfg = tg_cfg.get("proxy", {})
    proxy_url = None
    if proxy_cfg.get("enabled") and proxy_cfg.get("host"):
        host = proxy_cfg["host"]
        port = proxy_cfg.get("port", 18000)
        proxy_url = f"socks5://{host}:{port}"

    from pathlib import Path

    socket_path = Path("/opt/ironshield/ironshield.sock")

    return IronShieldBot(
        token=token,
        admin_ids=admin_ids,
        db=db,
        socket_path=socket_path,
        proxy_url=proxy_url,
    )
