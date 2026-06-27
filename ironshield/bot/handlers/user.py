"""
IronShield - User Bot Handlers
Path: ironshield/bot/handlers/user.py
Purpose: Handlers for regular VPN users.
         Shows subscription info, config file, QR code, and connection guide.
"""

from __future__ import annotations

import io
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from ironshield.api.client import APIClient, APIError
from ironshield.bot.keyboards.admin_kb import back_to_user_menu, user_main_menu
from ironshield.bot.middlewares.auth import AuthMiddleware, I18n, RateLimitMiddleware
from ironshield.db.database import Database
from ironshield.db.models import User
from ironshield.utils.logger import get_bot_logger

logger = get_bot_logger()


class UserHandlers:
    """
    Handlers for regular VPN users.

    Users can:
    - View their subscription status and traffic usage
    - Download their .ovpn config file
    - Get a QR code for mobile apps
    - View connection guide
    - Contact support (sends message to admin)
    """

    def __init__(
        self,
        api_client: APIClient,
        auth: AuthMiddleware,
        rate_limit: RateLimitMiddleware,
        i18n: I18n,
        db: Database,
        admin_ids: list,
    ):
        self.api = api_client
        self.auth = auth
        self.rate = rate_limit
        self.i18n = i18n
        self.db = db
        self.admin_ids = admin_ids

    # ── Guards ────────────────────────────────

    async def _guard(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
        """Check user auth and rate limit."""
        user_id = update.effective_user.id

        # Admins can also use user panel
        if self.auth.is_admin(user_id):
            return True

        if not self.auth.is_registered_user(user_id):
            await self._reply(update, self.i18n.t("unauthorized", user_id))
            return False

        if not self.rate.is_allowed(user_id):
            await self._reply(update, self.i18n.t("rate_limited", user_id))
            return False

        return True

    @staticmethod
    async def _reply(update: Update, text: str, keyboard=None) -> None:
        """Send or edit message."""
        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        elif update.message:
            await update.message.reply_text(
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )

    def _get_user_from_db(self, telegram_id: int) -> Optional[User]:
        """Fetch user record by Telegram ID."""
        try:
            with self.db.session() as s:
                return s.query(User).filter_by(telegram_id=telegram_id).first()
        except Exception as e:
            logger.warning(f"DB lookup failed for telegram_id={telegram_id}: {e}")
            return None

    def _get_username_for_telegram_id(self, telegram_id: int) -> Optional[str]:
        """Get VPN username linked to a Telegram user."""
        user = self._get_user_from_db(telegram_id)
        return user.username if user else None

    # ── Start / Menu ──────────────────────────

    async def start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/start for regular users."""
        user_id = update.effective_user.id
        if not await self._guard(update, ctx):
            return
        await self._reply(
            update,
            self.i18n.t("welcome_user", user_id),
            user_main_menu(),
        )

    async def main_menu(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show user main menu."""
        if not await self._guard(update, ctx):
            return
        await self._reply(
            update,
            self.i18n.t("main_menu", update.effective_user.id),
            user_main_menu(),
        )

    # ── Subscription ──────────────────────────

    async def subscription(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show subscription info and traffic usage."""
        if not await self._guard(update, ctx):
            return

        user_id = update.effective_user.id
        username = self._get_username_for_telegram_id(user_id)

        if username is None:
            await self._reply(update, self.i18n.t("not_found", user_id))
            return

        try:
            data = await self.api.request(f"GET /users/{username}")
            if data.get("error"):
                await self._reply(update, f"❌ {data['error']}")
                return

            # Determine status string
            if not data.get("is_active"):
                status = self.i18n.t("status_inactive", user_id)
            elif data.get("is_expired"):
                status = self.i18n.t("status_expired", user_id)
            elif data.get("is_over_quota"):
                status = self.i18n.t("status_over_quota", user_id)
            else:
                status = self.i18n.t("status_active", user_id)

            # Traffic display
            used = data.get("traffic_used_gb", 0) or 0
            limit = data.get("traffic_limit_gb")
            remaining = data.get("traffic_remaining_gb")

            total_str = f"{limit:.0f} GB" if limit else self.i18n.t("traffic_unlimited", user_id)
            remaining_str = (
                f"{remaining:.1f} GB"
                if remaining is not None
                else self.i18n.t("traffic_unlimited", user_id)
            )

            # Expire info
            days = data.get("days_until_expiry")
            expire_str = f"{days} روز دیگر" if days is not None else "نامحدود"

            # Traffic bar (visual indicator)
            percent = data.get("traffic_percent") or 0
            bar_filled = int(percent / 10)
            bar = "▓" * bar_filled + "░" * (10 - bar_filled)
            bar_line = f"\n{bar} {percent:.0f}%" if limit else ""

            text = (
                self.i18n.t(
                    "subscription_info",
                    user_id,
                    username=username,
                    status=status,
                    used=used,
                    remaining=remaining_str,
                    total=total_str,
                    expire=expire_str,
                )
                + bar_line
            )

            await self._reply(update, text, back_to_user_menu())

        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    # ── Config File ───────────────────────────

    async def get_config(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Send .ovpn config file to user."""
        if not await self._guard(update, ctx):
            return

        user_id = update.effective_user.id
        username = self._get_username_for_telegram_id(user_id)

        if username is None:
            await self._reply(update, self.i18n.t("not_found", user_id))
            return

        try:
            data = await self.api.get_user_config(username)
            if data.get("error"):
                await self._reply(
                    update,
                    self.i18n.t("config_not_found", user_id),
                    back_to_user_menu(),
                )
                return

            config_content = data.get("ovpn_content", "")
            if update.callback_query:
                await update.callback_query.answer()
                await update.callback_query.message.reply_document(
                    document=config_content.encode("utf-8"),
                    filename=f"{username}.ovpn",
                    caption=self.i18n.t("config_ready", user_id),
                )
            else:
                await update.message.reply_document(
                    document=config_content.encode("utf-8"),
                    filename=f"{username}.ovpn",
                    caption=self.i18n.t("config_ready", user_id),
                )

        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    # ── QR Code ───────────────────────────────

    async def get_qr(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Generate and send QR code for the config file."""
        if not await self._guard(update, ctx):
            return

        user_id = update.effective_user.id
        username = self._get_username_for_telegram_id(user_id)

        if username is None:
            await self._reply(update, self.i18n.t("not_found", user_id))
            return

        try:
            data = await self.api.get_user_config(username)
            if data.get("error"):
                await self._reply(update, self.i18n.t("config_not_found", user_id))
                return

            config_content = data.get("ovpn_content", "")

            # Generate QR code
            try:
                import qrcode

                qr = qrcode.QRCode(
                    version=None,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=6,
                    border=4,
                )
                qr.add_data(config_content[:2000])  # QR size limit
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")

                buf = io.BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)

                if update.callback_query:
                    await update.callback_query.answer()
                    await update.callback_query.message.reply_photo(
                        photo=buf,
                        caption=f"📱 QR Code — {username}",
                    )
                else:
                    await update.message.reply_photo(
                        photo=buf,
                        caption=f"📱 QR Code — {username}",
                    )

            except ImportError:
                await self._reply(
                    update,
                    "⚠️ QR Code generator not available. Please download the config file instead.",
                    back_to_user_menu(),
                )

        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    # ── Connection Guide ──────────────────────

    async def guide(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show connection guide."""
        if not await self._guard(update, ctx):
            return

        user_id = update.effective_user.id
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        text = (
            f"📖 <b>{self.i18n.t('guide_title', user_id)}</b>\n\n"
            f"{self.i18n.t('guide_text', user_id)}"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        self.i18n.t("guide_android", user_id),
                        url="https://play.google.com/store/apps/details?id=net.openvpn.openvpn",
                    ),
                    InlineKeyboardButton(
                        self.i18n.t("guide_ios", user_id),
                        url="https://apps.apple.com/app/openvpn-connect/id590379981",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        self.i18n.t("guide_windows", user_id),
                        url="https://openvpn.net/client-connect-vpn-for-windows/",
                    )
                ],
                [InlineKeyboardButton("🔙 بازگشت", callback_data="user:menu")],
            ]
        )
        await self._reply(update, text, keyboard)

    # ── Support ───────────────────────────────

    async def support(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show support message and relay to admin."""
        if not await self._guard(update, ctx):
            return

        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        text = "💬 برای ارتباط با پشتیبانی پیام خود را بنویسید:"
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 بازگشت", callback_data="user:menu")]]
        )
        ctx.user_data["waiting_support"] = True
        await self._reply(update, text, keyboard)

    async def support_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Relay a support message to all admins."""
        if not ctx.user_data.get("waiting_support"):
            return

        user = update.effective_user
        msg = update.message.text

        # Forward to all admins
        for admin_id in self.admin_ids:
            try:
                await ctx.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"💬 <b>پیام پشتیبانی</b>\n\n"
                        f"👤 کاربر: {user.full_name} (@{user.username or 'N/A'})\n"
                        f"🆔 ID: <code>{user.id}</code>\n\n"
                        f"📝 پیام:\n{msg}"
                    ),
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.warning(f"Failed to forward support message to admin {admin_id}: {e}")

        ctx.user_data.pop("waiting_support", None)
        await update.message.reply_text(
            "✅ پیام شما به پشتیبانی ارسال شد.",
            reply_markup=back_to_user_menu(),
        )
