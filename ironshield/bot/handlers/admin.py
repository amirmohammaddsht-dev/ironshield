"""
IronShield - Admin Bot Handlers
Path: ironshield/bot/handlers/admin.py
Purpose: All admin panel Telegram bot handlers.
         Handles services, users, monitoring, tunnels, plugins, settings.
"""

from __future__ import annotations


from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from ironshield.api.client import APIClient, APIError
from ironshield.bot.keyboards.admin_kb import (
    admin_main_menu,
    back_button,
    benchmark_menu,
    confirm_delete_user,
    language_menu,
    monitoring_menu,
    plugins_menu,
    service_detail_menu,
    services_menu,
    settings_menu,
    tunnels_menu,
    user_detail_menu,
    users_list_keyboard,
    users_menu,
)
from ironshield.bot.middlewares.auth import AuthMiddleware, I18n, RateLimitMiddleware
from ironshield.utils.logger import get_bot_logger

logger = get_bot_logger()

# Conversation states
WAITING_USERNAME = 1
WAITING_TRAFFIC = 2
WAITING_DAYS = 3
WAITING_SEARCH = 4


class AdminHandlers:
    """
    All admin Telegram bot handlers.

    Each method is an async handler for a specific callback or command.
    Handlers receive Update and Context objects from python-telegram-bot.
    """

    def __init__(
        self,
        api_client: APIClient,
        auth: AuthMiddleware,
        rate_limit: RateLimitMiddleware,
        i18n: I18n,
    ):
        self.api = api_client
        self.auth = auth
        self.rate = rate_limit
        self.i18n = i18n

    # ── Guards ────────────────────────────────

    async def _guard(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
        """
        Common guard: check admin auth and rate limit.
        Returns True if request should proceed.
        """
        user_id = update.effective_user.id
        if not self.auth.is_admin(user_id):
            await self._reply(update, self.i18n.t("unauthorized", user_id))
            return False

        if not self.rate.is_allowed(user_id):
            await self._reply(update, self.i18n.t("rate_limited", user_id))
            return False

        return True

    @staticmethod
    async def _reply(update: Update, text: str, keyboard=None) -> None:
        """Send or edit a message depending on context."""
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

    # ── Main Menu ─────────────────────────────

    async def start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/start command handler."""
        user_id = update.effective_user.id

        if self.auth.is_admin(user_id):
            text = self.i18n.t("welcome_admin", user_id)
            keyboard = admin_main_menu()
        elif self.auth.is_registered_user(user_id):
            # Redirect to user handlers
            text = self.i18n.t("welcome_user", user_id)
            keyboard = None
        else:
            text = self.i18n.t("unauthorized", user_id)
            keyboard = None

        await self._reply(update, text, keyboard)

    async def main_menu(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show admin main menu."""
        if not await self._guard(update, ctx):
            return
        user_id = update.effective_user.id
        await self._reply(
            update,
            self.i18n.t("admin_menu", user_id),
            admin_main_menu(),
        )

    # ── Services ──────────────────────────────

    async def services(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show services list."""
        if not await self._guard(update, ctx):
            return
        try:
            data = await self.api.list_plugins()
            plugins = list(data.get("plugins", {}).values())
            await self._reply(
                update,
                self.i18n.t("services_status", update.effective_user.id),
                services_menu(plugins),
            )
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    async def service_detail(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show detail for a specific service."""
        if not await self._guard(update, ctx):
            return
        data = update.callback_query.data  # "service:detail:openvpn"
        plugin_name = data.split(":")[-1]
        try:
            info = await self.api.request(f"GET /plugins/{plugin_name}")
            is_running = info.get("status") == "RUNNING"
            text = (
                f"🔌 <b>{info.get('display_name', plugin_name)}</b>\n"
                f"📦 نسخه: {info.get('version', 'N/A')}\n"
                f"⚡ وضعیت: {'🟢 در حال اجرا' if is_running else '🔴 متوقف'}\n"
                f"🏷️ دسته: {info.get('category', 'N/A')}"
            )
            await self._reply(update, text, service_detail_menu(plugin_name, is_running))
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    async def service_action(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle start/stop/restart for a service."""
        if not await self._guard(update, ctx):
            return
        parts = update.callback_query.data.split(":")
        action, name = parts[1], parts[2]
        user_id = update.effective_user.id

        try:
            if action == "start":
                result = await self.api.start_plugin(name)
                msg = self.i18n.t("service_started", user_id).format(name)
            elif action == "stop":
                result = await self.api.stop_plugin(name)
                msg = self.i18n.t("service_stopped", user_id).format(name)
            else:
                result = await self.api.restart_plugin(name)
                msg = self.i18n.t("service_restarted", user_id).format(name)

            if not result.get("success"):
                msg = f"❌ {result.get('error', 'Unknown error')}"

            await self._reply(update, msg, back_button("admin:services"))
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    async def service_logs(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show last 30 log lines for a service."""
        if not await self._guard(update, ctx):
            return
        plugin_name = update.callback_query.data.split(":")[-1]
        try:
            data = await self.api.get_plugin_logs(plugin_name, lines=30)
            lines = data.get("lines", [])
            if lines:
                log_text = "\n".join(f"<code>{line}</code>" for line in lines[-20:])
                text = f"📝 <b>Logs: {plugin_name}</b>\n\n{log_text}"
            else:
                text = f"📝 No logs available for {plugin_name}"
            await self._reply(update, text[:4000], back_button(f"service:detail:{plugin_name}"))
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    # ── Users ─────────────────────────────────

    async def users_menu_handler(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show users management menu."""
        if not await self._guard(update, ctx):
            return
        await self._reply(
            update,
            self.i18n.t("users_list", update.effective_user.id),
            users_menu(),
        )

    async def users_list(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show paginated user list."""
        if not await self._guard(update, ctx):
            return
        parts = update.callback_query.data.split(":")
        page = int(parts[2]) if len(parts) > 2 else 0

        try:
            data = await self.api.list_users()
            users = data.get("users", [])
            text = f"👥 کاربران ({len(users)} نفر)"
            await self._reply(update, text, users_list_keyboard(users, page=page))
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    async def user_detail(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show user detail."""
        if not await self._guard(update, ctx):
            return
        username = update.callback_query.data.split(":")[-1]
        try:
            user = await self.api.request(f"GET /users/{username}")
            if "error" in user:
                await self._reply(update, f"❌ {user['error']}")
                return

            traffic_used = user.get("traffic_used_gb", 0)
            traffic_limit = user.get("traffic_limit_gb")
            traffic_str = (
                f"{traffic_used:.1f} / {traffic_limit:.0f} GB"
                if traffic_limit
                else f"{traffic_used:.1f} GB / ∞"
            )

            text = (
                f"👤 <b>{username}</b>\n\n"
                f"⚡ وضعیت: {'✅ فعال' if user.get('is_active') else '❌ غیرفعال'}\n"
                f"📦 ترافیک: {traffic_str}\n"
                f"📅 انقضا: {user.get('days_until_expiry', '?')} روز دیگر\n"
                f"🕐 آخرین اتصال: {user.get('last_connected_at', 'هرگز') or 'هرگز'}"
            )
            await self._reply(
                update, text, user_detail_menu(username, user.get("is_active", False))
            )
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    async def user_add_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        """Start add user conversation."""
        if not await self._guard(update, ctx):
            return ConversationHandler.END
        await self._reply(
            update,
            self.i18n.t("enter_username", update.effective_user.id),
            back_button("admin:users"),
        )
        return WAITING_USERNAME

    async def user_add_username(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        """Receive username from conversation."""
        username = update.message.text.strip()
        ctx.user_data["new_username"] = username
        user_id = update.effective_user.id

        await update.message.reply_text(self.i18n.t("enter_expire_days", user_id))
        return WAITING_DAYS

    async def user_add_days(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
        """Receive expire days and create user."""
        user_id = update.effective_user.id
        try:
            days = int(update.message.text.strip())
        except ValueError:
            await update.message.reply_text("❌ عدد معتبر وارد کنید.")
            return WAITING_DAYS

        username = ctx.user_data.get("new_username", "")
        try:
            result = await self.api.create_user(username=username, expire_days=days)
            if result.get("error"):
                await update.message.reply_text(f"❌ {result['error']}")
            else:
                msg = self.i18n.t("user_created", user_id).format(username)
                await update.message.reply_text(msg, reply_markup=back_button("admin:users"))
        except (APIError, ConnectionError) as e:
            await update.message.reply_text(f"❌ {e}")

        ctx.user_data.clear()
        return ConversationHandler.END

    async def user_toggle(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Toggle user active/inactive."""
        if not await self._guard(update, ctx):
            return
        username = update.callback_query.data.split(":")[-1]
        try:
            result = await self.api.toggle_user(username)
            is_active = result.get("is_active", False)
            user_id = update.effective_user.id
            msg = (
                self.i18n.t("user_activated", user_id).format(username)
                if is_active
                else self.i18n.t("user_deactivated", user_id).format(username)
            )
            await self._reply(update, msg, back_button(f"user:detail:{username}"))
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    async def user_delete_confirm(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Ask for delete confirmation."""
        if not await self._guard(update, ctx):
            return
        username = update.callback_query.data.split(":")[-1]
        await self._reply(
            update,
            f"⚠️ آیا مطمئن هستید که می‌خواهید کاربر <b>{username}</b> را حذف کنید؟",
            confirm_delete_user(username),
        )

    async def user_delete_execute(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Execute user deletion after confirmation."""
        if not await self._guard(update, ctx):
            return
        username = update.callback_query.data.split(":")[-1]
        user_id = update.effective_user.id
        try:
            await self.api.delete_user(username)
            msg = self.i18n.t("user_deleted", user_id).format(username)
            await self._reply(update, msg, back_button("users:list"))
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    async def user_get_config(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Send .ovpn config file for a user."""
        if not await self._guard(update, ctx):
            return
        username = update.callback_query.data.split(":")[-1]
        try:
            data = await self.api.get_user_config(username)
            if data.get("error"):
                await self._reply(update, f"❌ {data['error']}")
                return
            config_content = data.get("ovpn_content", "")
            await update.callback_query.answer()
            await update.callback_query.message.reply_document(
                document=config_content.encode(),
                filename=f"{username}.ovpn",
                caption=f"🔗 فایل اتصال کاربر {username}",
            )
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    # ── Monitoring ────────────────────────────

    async def monitoring(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show monitoring menu."""
        if not await self._guard(update, ctx):
            return
        await self._reply(
            update,
            self.i18n.t("monitoring_dashboard", update.effective_user.id),
            monitoring_menu(),
        )

    async def monitor_server(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show server resource metrics."""
        if not await self._guard(update, ctx):
            return
        server = update.callback_query.data.split(":")[-1]
        try:
            data = await self.api.get_metrics()
            system = data.get("system", {}) or {}
            label = "🇮🇷 سرور ایران" if server == "iran" else "🌍 سرور خارج"
            text = (
                f"<b>{label}</b>\n\n"
                f"🖥️ CPU: {system.get('cpu_percent', '?')}%\n"
                f"💾 RAM: {system.get('ram_percent', '?')}%"
                f" ({system.get('ram_used_gb', '?'):.1f}/"
                f"{system.get('ram_total_gb', '?'):.0f} GB)\n"
                f"💿 Disk: {system.get('disk_percent', '?')}%\n"
                f"🕐 آخرین بروزرسانی: {system.get('recorded_at', 'N/A')}"
            )
            await self._reply(update, text, back_button("admin:monitoring"))
        except Exception as e:
            await self._reply(update, f"❌ {e}")

    async def monitor_tunnels(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show tunnel status and scores."""
        if not await self._guard(update, ctx):
            return
        try:
            data = await self.api.get_ranked_tunnels()
            tunnels = data.get("tunnels", [])
            lines = ["🔗 <b>وضعیت تانل‌ها</b>\n"]
            for t in tunnels:
                icon = "🟢" if t["status"] == "ACTIVE" else ("🔴" if t["status"] == "FAILED" else "🟡")
                score = f"{t['score']:.0f}pts" if t.get("score") else "N/A"
                latency = f"{t['latency_ms']:.0f}ms" if t.get("latency_ms") else "N/A"
                lines.append(f"{icon} <b>{t['name']}</b> — {score} | {latency}")
            await self._reply(update, "\n".join(lines), back_button("admin:monitoring"))
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    async def benchmark_menu_handler(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show benchmark options."""
        if not await self._guard(update, ctx):
            return
        await self._reply(
            update,
            "🔬 انتخاب نوع Benchmark:",
            benchmark_menu(),
        )

    async def benchmark_run(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Run a benchmark and show results."""
        if not await self._guard(update, ctx):
            return
        btype = update.callback_query.data.split(":")[-1]
        user_id = update.effective_user.id

        await self._reply(update, self.i18n.t("benchmark_running", user_id))

        try:
            is_quick = btype == "quick"
            data = await self.api.run_benchmark(quick=is_quick)
            results = data.get("results", {})

            lines = [f"🔬 <b>نتایج Benchmark ({btype})</b>\n"]
            for name, r in results.items():
                if r.get("success"):
                    score = r.get("score", "N/A")
                    latency = r.get("latency_ms", "N/A")
                    lines.append(f"✅ <b>{name}</b>: {score}pts | {latency}ms")
                else:
                    lines.append(f"❌ <b>{name}</b>: {r.get('error', 'failed')}")

            await self._reply(update, "\n".join(lines), back_button("monitor:benchmark"))
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    # ── Tunnels ───────────────────────────────

    async def tunnels_handler(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show tunnel management menu."""
        if not await self._guard(update, ctx):
            return
        try:
            data = await self.api.get_ranked_tunnels()
            tunnels = data.get("tunnels", [])
            await self._reply(
                update,
                "🔗 انتخاب تانل فعال:",
                tunnels_menu(tunnels),
            )
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    async def tunnel_switch_handler(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Switch active tunnel."""
        if not await self._guard(update, ctx):
            return
        tunnel_name = update.callback_query.data.split(":")[-1]
        user_id = update.effective_user.id
        try:
            result = await self.api.switch_tunnel(tunnel_name)
            if result.get("success"):
                msg = self.i18n.t("tunnel_switched", user_id).format(tunnel_name)
            else:
                msg = f"❌ {result.get('message', 'Failed')}"
            await self._reply(update, msg, back_button("admin:tunnels"))
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    async def tunnel_auto_handler(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Restore auto tunnel selection."""
        if not await self._guard(update, ctx):
            return
        user_id = update.effective_user.id
        try:
            await self.api.clear_tunnel_override()
            await self._reply(
                update,
                self.i18n.t("tunnel_auto_restored", user_id),
                back_button("admin:tunnels"),
            )
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    # ── Plugins ───────────────────────────────

    async def plugins_handler(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show plugin manager."""
        if not await self._guard(update, ctx):
            return
        try:
            data = await self.api.list_plugins()
            plugins = list(data.get("plugins", {}).values())
            await self._reply(update, "🔌 مدیریت پلاگین‌ها:", plugins_menu(plugins))
        except (APIError, ConnectionError) as e:
            await self._reply(update, f"❌ {e}")

    # ── Settings ──────────────────────────────

    async def settings_handler(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show settings menu."""
        if not await self._guard(update, ctx):
            return
        await self._reply(
            update,
            self.i18n.t("settings_menu", update.effective_user.id),
            settings_menu(),
        )

    async def language_menu_handler(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Show language selection."""
        await self._reply(
            update,
            self.i18n.t("select_language", update.effective_user.id),
            language_menu(),
        )

    async def set_language(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Set user language preference."""
        user_id = update.effective_user.id
        lang = update.callback_query.data.split(":")[-1]
        self.i18n.set_language(user_id, lang)
        await self._reply(
            update,
            self.i18n.t("language_set", user_id),
            back_button("settings:back"),
        )
