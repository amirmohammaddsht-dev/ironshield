"""
Tests for Phase 7 — Telegram Bot.
Tests: I18n, AuthMiddleware, RateLimitMiddleware, keyboards, alert system.
All Telegram API calls are mocked.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ironshield.bot.middlewares.auth import AuthMiddleware, I18n, RateLimitMiddleware
from ironshield.bot.keyboards.admin_kb import (
    admin_main_menu,
    back_button,
    language_menu,
    service_detail_menu,
    user_main_menu,
    users_list_keyboard,
    confirm_delete_user,
    benchmark_menu,
)
from ironshield.bot.handlers.alerts import AlertSystem


# ── I18n Tests ────────────────────────────────


class TestI18n:
    @pytest.fixture
    def i18n(self, tmp_path):
        """Create I18n with test locale files."""
        locales_dir = tmp_path / "locales"
        locales_dir.mkdir()

        # Write test locale files
        fa = {
            "welcome_admin": "خوش آمدید ادمین",
            "unauthorized": "دسترسی ندارید",
            "alert_cpu_high": "CPU سرور {server}: {value:.0f}%",
            "user_created": "کاربر {} ایجاد شد",
        }
        en = {
            "welcome_admin": "Welcome Admin",
            "unauthorized": "Unauthorized",
            "alert_cpu_high": "CPU on {server}: {value:.0f}%",
            "user_created": "User {} created",
        }

        (locales_dir / "fa.json").write_text(json.dumps(fa, ensure_ascii=False))
        (locales_dir / "en.json").write_text(json.dumps(en, ensure_ascii=False))

        with patch("ironshield.bot.middlewares.auth.LOCALES_DIR", locales_dir):
            return I18n()

    def test_loads_locales(self, i18n):
        """Should load both FA and EN locales."""
        assert "fa" in i18n._strings
        assert "en" in i18n._strings

    def test_default_language_is_fa(self, i18n):
        """Default language should be Farsi."""
        assert i18n.get_language(99999) == "fa"

    def test_translate_fa(self, i18n):
        """Should return Farsi translation by default."""
        result = i18n.t("welcome_admin")
        assert result == "خوش آمدید ادمین"

    def test_translate_en(self, i18n):
        """Should return English translation when set."""
        i18n.set_language(123, "en")
        result = i18n.t("welcome_admin", user_id=123)
        assert result == "Welcome Admin"

    def test_translate_with_kwargs(self, i18n):
        """Should format string with provided kwargs."""
        result = i18n.t("alert_cpu_high", server="Iran", value=85.5)
        assert "Iran" in result
        assert "85" in result or "86" in result

    def test_missing_key_returns_placeholder(self, i18n):
        """Missing key should return bracketed key name."""
        result = i18n.t("nonexistent_key")
        assert result == "[nonexistent_key]"

    def test_set_and_get_language(self, i18n):
        """Language preference should persist per user."""
        i18n.set_language(42, "en")
        assert i18n.get_language(42) == "en"

        i18n.set_language(42, "fa")
        assert i18n.get_language(42) == "fa"

    def test_available_languages(self, i18n):
        """Should return list of available language codes."""
        langs = i18n.available_languages()
        assert "fa" in langs
        assert "en" in langs

    def test_ignore_invalid_language(self, i18n):
        """Setting invalid language should not change preference."""
        i18n.set_language(99, "invalid")
        assert i18n.get_language(99) == "fa"  # stays default


# ── AuthMiddleware Tests ───────────────────────


class TestAuthMiddleware:
    @pytest.fixture
    def auth(self):
        return AuthMiddleware(admin_ids=[111, 222], db=None)

    def test_admin_is_recognized(self, auth):
        assert auth.is_admin(111) is True
        assert auth.is_admin(222) is True

    def test_non_admin_rejected(self, auth):
        assert auth.is_admin(999) is False

    def test_add_admin(self, auth):
        auth.add_admin(333)
        assert auth.is_admin(333) is True

    def test_remove_admin(self, auth):
        auth.remove_admin(111)
        assert auth.is_admin(111) is False

    def test_registered_user_without_db(self, auth):
        """Should return False when DB is None."""
        assert auth.is_registered_user(999) is False

    def test_registered_user_with_db(self, tmp_path):
        """Should check DB for user registration."""
        from ironshield.db.database import Database
        from ironshield.db.models import User

        db = Database(tmp_path / "test.db")
        db.init()

        # Create an active user with telegram_id
        with db.session() as s:
            s.add(User(username="testuser", telegram_id=777, is_active=True))

        auth = AuthMiddleware(admin_ids=[111], db=db)
        assert auth.is_registered_user(777) is True
        assert auth.is_registered_user(888) is False

        db.close()

    def test_expired_user_not_registered(self, tmp_path):
        """Expired users should not be considered registered."""
        from datetime import datetime, timezone, timedelta
        from ironshield.db.database import Database
        from ironshield.db.models import User

        db = Database(tmp_path / "test2.db")
        db.init()

        with db.session() as s:
            s.add(
                User(
                    username="expired",
                    telegram_id=666,
                    is_active=True,
                    expire_at=datetime.now(timezone.utc) - timedelta(days=1),
                )
            )

        auth = AuthMiddleware(admin_ids=[], db=db)
        assert auth.is_registered_user(666) is False
        db.close()


# ── RateLimitMiddleware Tests ──────────────────


class TestRateLimitMiddleware:
    def test_allows_within_limit(self):
        rl = RateLimitMiddleware(max_requests=5, window_seconds=60)
        for _ in range(5):
            assert rl.is_allowed(1) is True

    def test_blocks_when_limit_exceeded(self):
        rl = RateLimitMiddleware(max_requests=3, window_seconds=60)
        for _ in range(3):
            rl.is_allowed(2)
        assert rl.is_allowed(2) is False

    def test_different_users_independent(self):
        rl = RateLimitMiddleware(max_requests=2, window_seconds=60)
        rl.is_allowed(1)
        rl.is_allowed(1)
        assert rl.is_allowed(1) is False
        assert rl.is_allowed(2) is True  # User 2 is fresh

    def test_window_resets_over_time(self):
        rl = RateLimitMiddleware(max_requests=2, window_seconds=1)
        rl.is_allowed(3)
        rl.is_allowed(3)
        assert rl.is_allowed(3) is False

        time.sleep(1.1)  # Wait for window to expire
        assert rl.is_allowed(3) is True

    def test_get_reset_time(self):
        rl = RateLimitMiddleware(max_requests=1, window_seconds=60)
        rl.is_allowed(4)
        rl.is_allowed(4)  # blocked
        reset = rl.get_reset_time(4)
        assert 50 <= reset <= 61

    def test_get_reset_time_no_requests(self):
        rl = RateLimitMiddleware(max_requests=5, window_seconds=60)
        assert rl.get_reset_time(999) == 0


# ── Keyboard Tests ─────────────────────────────


class TestKeyboards:
    def test_admin_main_menu_has_buttons(self):
        kb = admin_main_menu()
        rows = kb.inline_keyboard
        assert len(rows) > 0
        all_btns = [btn for row in rows for btn in row]
        callbacks = [btn.callback_data for btn in all_btns]
        assert "admin:services" in callbacks
        assert "admin:users" in callbacks
        assert "admin:monitoring" in callbacks
        assert "admin:tunnels" in callbacks

    def test_user_main_menu_has_buttons(self):
        kb = user_main_menu()
        rows = kb.inline_keyboard
        assert len(rows) > 0
        all_btns = [btn for row in rows for btn in row]
        callbacks = [btn.callback_data for btn in all_btns]
        assert "user:subscription" in callbacks
        assert "user:get_config" in callbacks

    def test_service_detail_running(self):
        kb = service_detail_menu("openvpn", is_running=True)
        all_btns = [btn for row in kb.inline_keyboard for btn in row]
        callbacks = [btn.callback_data for btn in all_btns]
        assert "service:stop:openvpn" in callbacks
        assert "service:restart:openvpn" in callbacks

    def test_service_detail_stopped(self):
        kb = service_detail_menu("gost", is_running=False)
        all_btns = [btn for row in kb.inline_keyboard for btn in row]
        callbacks = [btn.callback_data for btn in all_btns]
        assert "service:start:gost" in callbacks

    def test_language_menu(self):
        kb = language_menu()
        all_btns = [btn for row in kb.inline_keyboard for btn in row]
        callbacks = [btn.callback_data for btn in all_btns]
        assert "lang:fa" in callbacks
        assert "lang:en" in callbacks

    def test_back_button_default(self):
        kb = back_button()
        btn = kb.inline_keyboard[0][0]
        assert btn.callback_data == "admin:back"

    def test_back_button_custom(self):
        kb = back_button("admin:users")
        btn = kb.inline_keyboard[0][0]
        assert btn.callback_data == "admin:users"

    def test_confirm_delete_user(self):
        kb = confirm_delete_user("ali")
        all_btns = [btn for row in kb.inline_keyboard for btn in row]
        callbacks = [btn.callback_data for btn in all_btns]
        assert "user:confirm_delete:ali" in callbacks
        assert "user:detail:ali" in callbacks  # cancel goes back to detail

    def test_users_list_keyboard_pagination(self):
        users = [{"username": f"user{i}", "is_active": True} for i in range(20)]
        kb = users_list_keyboard(users, page=0, per_page=8)
        all_btns = [btn for row in kb.inline_keyboard for btn in row]
        callbacks = [btn.callback_data for btn in all_btns]
        # Should have "next" button
        assert any("users:list:1" in c for c in callbacks)

    def test_users_list_keyboard_no_prev_on_first_page(self):
        users = [{"username": f"user{i}", "is_active": True} for i in range(5)]
        kb = users_list_keyboard(users, page=0)
        all_btns = [btn for row in kb.inline_keyboard for btn in row]
        callbacks = [btn.callback_data for btn in all_btns]
        # Should not have "prev" button on first page
        assert not any("users:list:-1" in c for c in callbacks)

    def test_benchmark_menu(self):
        kb = benchmark_menu()
        all_btns = [btn for row in kb.inline_keyboard for btn in row]
        callbacks = [btn.callback_data for btn in all_btns]
        assert "benchmark:quick" in callbacks
        assert "benchmark:full" in callbacks


# ── AlertSystem Tests ──────────────────────────


class TestAlertSystem:
    @pytest.fixture
    def alerts(self, tmp_path):
        """Create AlertSystem with mocked bot."""
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock()

        i18n_dir = tmp_path / "locales"
        i18n_dir.mkdir()
        fa = {
            "alert_service_down": "سرویس {service} قطع شد",
            "alert_service_recovered": "سرویس {service} بازیابی شد - {downtime}",
            "alert_cpu_high": "CPU {server}: {value:.0f}%",
            "alert_ram_high": "RAM {server}: {value:.0f}%",
            "alert_disk_high": "Disk {server}: {value:.0f}%",
            "alert_user_expiring": "کاربر {username} {days} روز",
            "alert_user_quota": "کاربر {username} {percent:.0f}%",
        }
        (i18n_dir / "fa.json").write_text(json.dumps(fa, ensure_ascii=False))

        with patch("ironshield.bot.middlewares.auth.LOCALES_DIR", i18n_dir):
            i18n = I18n()

        return AlertSystem(
            bot=mock_bot,
            admin_ids=[111, 222],
            i18n=i18n,
        )

    @pytest.mark.asyncio
    async def test_send_to_all_admins(self, alerts):
        """Should send message to all admin IDs."""
        await alerts.send_to_all_admins("Test message")
        assert alerts.bot.send_message.call_count == 2
        call_args = [c.kwargs["chat_id"] for c in alerts.bot.send_message.call_args_list]
        assert 111 in call_args
        assert 222 in call_args

    @pytest.mark.asyncio
    async def test_send_continues_on_error(self, alerts):
        """Should continue sending to other admins if one fails."""
        from telegram.error import TelegramError

        alerts.bot.send_message.side_effect = [
            TelegramError("blocked"),
            None,  # second admin succeeds
        ]
        await alerts.send_to_all_admins("Test")
        assert alerts.bot.send_message.call_count == 2

    def test_on_alert_callback(self, alerts):
        """on_alert should call send_sync."""
        with patch.object(alerts, "send_sync") as mock_send:
            alerts.on_alert("Test Alert", "Body text", "WARNING")
            mock_send.assert_called_once()
            call_text = mock_send.call_args[0][0]
            assert "Test Alert" in call_text
            assert "Body text" in call_text

    def test_on_service_failure_callback(self, alerts):
        """on_service_failure should notify admins."""
        with patch.object(alerts, "send_sync") as mock_send:
            alerts.on_service_failure(
                service_name="openvpn",
                health=MagicMock(error="Process died"),
                consecutive_failures=3,
            )
            mock_send.assert_called_once()

    def test_on_system_alert_cpu(self, alerts):
        """CPU alert should use correct locale key."""
        with patch.object(alerts, "send_sync") as mock_send:
            alerts.on_system_alert("CPU", 92.5, "CRITICAL")
            mock_send.assert_called_once()
            text = mock_send.call_args[0][0]
            assert "92" in text or "93" in text

    def test_on_tunnel_switch_no_alert_on_initial(self, alerts):
        """Should not alert on initial tunnel selection."""
        with patch.object(alerts, "send_sync") as mock_send:
            alerts.on_tunnel_switch(
                from_tunnel=None,
                to_tunnel="phormal",
                reason="initial",
            )
            mock_send.assert_not_called()

    def test_on_tunnel_switch_alerts_on_failure(self, alerts):
        """Should alert when switching due to tunnel failure."""
        with patch.object(alerts, "send_sync") as mock_send:
            alerts.on_tunnel_switch(
                from_tunnel="phormal",
                to_tunnel="backhaul",
                reason="tunnel_failed",
                from_score=45.0,
                to_score=91.0,
            )
            mock_send.assert_called_once()
            text = mock_send.call_args[0][0]
            assert "phormal" in text
            assert "backhaul" in text
