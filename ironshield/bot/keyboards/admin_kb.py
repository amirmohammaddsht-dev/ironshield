"""
IronShield - Bot Keyboards
Path: ironshield/bot/keyboards/admin_kb.py
Purpose: All inline keyboards for Admin and User bot menus.
"""

from __future__ import annotations

from typing import List

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


# ── Admin Keyboards ───────────────────────────


def admin_main_menu() -> InlineKeyboardMarkup:
    """Main admin menu keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🖥️ سرویس‌ها", callback_data="admin:services"),
                InlineKeyboardButton("👥 کاربران", callback_data="admin:users"),
            ],
            [
                InlineKeyboardButton("📊 مانیتورینگ", callback_data="admin:monitoring"),
                InlineKeyboardButton("🔗 تانل‌ها", callback_data="admin:tunnels"),
            ],
            [
                InlineKeyboardButton("🔌 پلاگین‌ها", callback_data="admin:plugins"),
                InlineKeyboardButton("⚙️ تنظیمات", callback_data="admin:settings"),
            ],
            [
                InlineKeyboardButton("🔄 بروزرسانی", callback_data="admin:refresh"),
            ],
        ]
    )


def services_menu(plugins: List[dict]) -> InlineKeyboardMarkup:
    """Services list keyboard with status indicators."""
    buttons = []

    for plugin in plugins:
        name = plugin.get("name", "")
        display = plugin.get("display_name", name)
        status = plugin.get("status", "UNKNOWN")

        icon = "🟢" if status == "RUNNING" else ("🔴" if status == "FAILED" else "🟡")
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{icon} {display}",
                    callback_data=f"service:detail:{name}",
                )
            ]
        )

    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin:back")])
    return InlineKeyboardMarkup(buttons)


def service_detail_menu(plugin_name: str, is_running: bool) -> InlineKeyboardMarkup:
    """Detail menu for a specific service."""
    action_btn = (
        InlineKeyboardButton("⏹️ توقف", callback_data=f"service:stop:{plugin_name}")
        if is_running
        else InlineKeyboardButton("▶️ شروع", callback_data=f"service:start:{plugin_name}")
    )
    return InlineKeyboardMarkup(
        [
            [
                action_btn,
                InlineKeyboardButton("🔄 ریستارت", callback_data=f"service:restart:{plugin_name}"),
            ],
            [InlineKeyboardButton("📝 لاگ‌ها", callback_data=f"service:logs:{plugin_name}")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin:services")],
        ]
    )


def users_menu() -> InlineKeyboardMarkup:
    """Users management menu."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ افزودن کاربر", callback_data="users:add"),
                InlineKeyboardButton("📋 لیست کاربران", callback_data="users:list"),
            ],
            [
                InlineKeyboardButton("🔍 جستجو", callback_data="users:search"),
                InlineKeyboardButton("⌛ منقضی‌شده", callback_data="users:expired"),
            ],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin:back")],
        ]
    )


def user_detail_menu(username: str, is_active: bool) -> InlineKeyboardMarkup:
    """Detail menu for a specific user."""
    toggle_label = "⏸️ غیرفعال کردن" if is_active else "▶️ فعال کردن"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(toggle_label, callback_data=f"user:toggle:{username}"),
                InlineKeyboardButton("🗑️ حذف", callback_data=f"user:delete:{username}"),
            ],
            [
                InlineKeyboardButton("📦 افزایش حجم", callback_data=f"user:traffic:{username}"),
                InlineKeyboardButton("⏰ تمدید", callback_data=f"user:extend:{username}"),
            ],
            [
                InlineKeyboardButton("🔗 دریافت Config", callback_data=f"user:config:{username}"),
            ],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="users:list")],
        ]
    )


def users_list_keyboard(
    users: List[dict], page: int = 0, per_page: int = 8
) -> InlineKeyboardMarkup:
    """Paginated user list keyboard."""
    start = page * per_page
    end = start + per_page
    page_users = users[start:end]

    buttons = []
    for user in page_users:
        username = user.get("username", "")
        active = user.get("is_active", False)
        icon = "✅" if active else "❌"
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{icon} {username}",
                    callback_data=f"user:detail:{username}",
                )
            ]
        )

    # Pagination
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"users:list:{page-1}"))
    if end < len(users):
        nav.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"users:list:{page+1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data="admin:users")])
    return InlineKeyboardMarkup(buttons)


def monitoring_menu() -> InlineKeyboardMarkup:
    """Monitoring menu keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🇮🇷 سرور ایران", callback_data="monitor:iran"),
                InlineKeyboardButton("🌍 سرور خارج", callback_data="monitor:foreign"),
            ],
            [
                InlineKeyboardButton("🔗 وضعیت تانل‌ها", callback_data="monitor:tunnels"),
                InlineKeyboardButton("🔬 Benchmark", callback_data="monitor:benchmark"),
            ],
            [
                InlineKeyboardButton("🔄 بروزرسانی", callback_data="monitor:refresh"),
                InlineKeyboardButton("🔙 بازگشت", callback_data="admin:back"),
            ],
        ]
    )


def benchmark_menu() -> InlineKeyboardMarkup:
    """Benchmark run menu."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚡ تست سریع", callback_data="benchmark:quick"),
                InlineKeyboardButton("🔬 تست کامل", callback_data="benchmark:full"),
            ],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin:monitoring")],
        ]
    )


def tunnels_menu(tunnels: List[dict]) -> InlineKeyboardMarkup:
    """Tunnels management keyboard with scores."""
    buttons = []

    for t in tunnels[:6]:  # Show top 6
        name = t.get("name", "")
        score = t.get("score")
        status = t.get("status", "UNKNOWN")
        icon = "🟢" if status == "ACTIVE" else ("🔴" if status == "FAILED" else "🟡")
        score_str = f" ({score:.0f}pts)" if score else ""
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{icon} {name}{score_str}",
                    callback_data=f"tunnel:switch:{name}",
                )
            ]
        )

    buttons.extend(
        [
            [InlineKeyboardButton("🤖 حالت خودکار", callback_data="tunnel:auto")],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin:back")],
        ]
    )
    return InlineKeyboardMarkup(buttons)


def plugins_menu(plugins: List[dict]) -> InlineKeyboardMarkup:
    """Plugin manager keyboard."""
    buttons = []
    for plugin in plugins:
        name = plugin.get("name", "")
        version = plugin.get("version", "?")
        buttons.append(
            [
                InlineKeyboardButton(
                    f"🔌 {name} v{version}",
                    callback_data=f"plugin:detail:{name}",
                )
            ]
        )
    buttons.append(
        [
            InlineKeyboardButton("🔄 آپدیت همه", callback_data="plugin:update_all"),
            InlineKeyboardButton("🔙 بازگشت", callback_data="admin:back"),
        ]
    )
    return InlineKeyboardMarkup(buttons)


def settings_menu() -> InlineKeyboardMarkup:
    """Settings keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🌐 زبان", callback_data="settings:language"),
                InlineKeyboardButton("🔔 هشدارها", callback_data="settings:alerts"),
            ],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="admin:back")],
        ]
    )


def language_menu() -> InlineKeyboardMarkup:
    """Language selection keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🇮🇷 فارسی", callback_data="lang:fa"),
                InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
            ],
            [InlineKeyboardButton("🔙 بازگشت", callback_data="settings:back")],
        ]
    )


def confirm_delete_user(username: str) -> InlineKeyboardMarkup:
    """Confirm user deletion keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ بله، حذف شود", callback_data=f"user:confirm_delete:{username}"
                ),
                InlineKeyboardButton("❌ لغو", callback_data=f"user:detail:{username}"),
            ]
        ]
    )


# ── User Keyboards ────────────────────────────


def user_main_menu() -> InlineKeyboardMarkup:
    """Main menu for regular users."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📦 اشتراک من", callback_data="user:subscription")],
            [
                InlineKeyboardButton("🔗 فایل اتصال", callback_data="user:get_config"),
                InlineKeyboardButton("📱 QR Code", callback_data="user:qr"),
            ],
            [InlineKeyboardButton("📖 راهنمای اتصال", callback_data="user:guide")],
            [InlineKeyboardButton("💬 پشتیبانی", callback_data="user:support")],
            [InlineKeyboardButton("🌐 تغییر زبان", callback_data="settings:language")],
        ]
    )


def back_to_user_menu() -> InlineKeyboardMarkup:
    """Simple back button to user main menu."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data="user:menu")]])


def back_button(callback: str = "admin:back") -> InlineKeyboardMarkup:
    """Generic back button."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 بازگشت", callback_data=callback)]])
