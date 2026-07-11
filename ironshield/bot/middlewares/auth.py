"""
IronShield - Bot Middlewares
Path: ironshield/bot/middlewares/auth.py
Purpose: Authentication and rate-limiting middleware for the Telegram bot.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set


from ironshield.utils.logger import get_bot_logger

logger = get_bot_logger()

LOCALES_DIR = Path(__file__).parent.parent / "locales"


class I18n:
    """
    Simple internationalization helper.
    Loads locale strings from JSON files and formats them.
    """

    def __init__(self):
        self._strings: Dict[str, Dict[str, str]] = {}
        self._user_languages: Dict[int, str] = {}
        self._default_lang = "fa"
        self._load_locales()

    def _load_locales(self) -> None:
        """Load all available locale files."""
        for lang_file in LOCALES_DIR.glob("*.json"):
            lang = lang_file.stem
            try:
                with open(lang_file, encoding="utf-8") as f:
                    self._strings[lang] = json.load(f)
                logger.info(f"Locale loaded: {lang} ({len(self._strings[lang])} strings)")
            except Exception as e:
                logger.error(f"Failed to load locale {lang}: {e}")

    def set_language(self, user_id: int, lang: str) -> None:
        """Set preferred language for a user."""
        if lang in self._strings:
            self._user_languages[user_id] = lang

    def get_language(self, user_id: int) -> str:
        """Get preferred language for a user."""
        return self._user_languages.get(user_id, self._default_lang)

    def t(self, key: str, user_id: Optional[int] = None, **kwargs) -> str:
        """
        Translate a key to the user's preferred language.

        Args:
            key: Locale string key
            user_id: Telegram user ID (for language preference)
            **kwargs: Format arguments

        Returns:
            Translated and formatted string
        """
        lang = self.get_language(user_id) if user_id else self._default_lang
        strings = self._strings.get(lang, self._strings.get(self._default_lang, {}))
        text = strings.get(key, f"[{key}]")
        if kwargs:
            try:
                text = text.format(**kwargs)
            except (KeyError, IndexError):
                pass
        return text

    def available_languages(self) -> List[str]:
        """Return list of available language codes."""
        return list(self._strings.keys())


class AuthMiddleware:
    """
    Authentication middleware for the Telegram bot.

    Checks:
    - Admin whitelist by Telegram user ID
    - User access based on active subscription in DB
    - Blocks unauthorized access
    """

    def __init__(
        self,
        admin_ids: List[int],
        db=None,
        i18n: Optional[I18n] = None,
    ):
        self._admin_ids: Set[int] = set(admin_ids)
        self._db = db
        self._i18n = i18n or I18n()

    def is_admin(self, user_id: int) -> bool:
        """Check if user is in admin whitelist."""
        return user_id in self._admin_ids

    def is_registered_user(self, user_id: int) -> bool:
        """Check if user has a registered account in DB."""
        if self._db is None:
            return False
        try:
            from ironshield.db.models import User

            with self._db.session() as s:
                user = s.query(User).filter_by(telegram_id=user_id, is_active=True).first()
                if not user or user.is_expired or user.is_blocked:
                    return False
                return True
        except Exception:
            return False

    def add_admin(self, user_id: int) -> None:
        """Add a user to the admin whitelist."""
        self._admin_ids.add(user_id)
        logger.info(f"Admin added: {user_id}")

    def remove_admin(self, user_id: int) -> None:
        """Remove a user from the admin whitelist."""
        self._admin_ids.discard(user_id)


class RateLimitMiddleware:
    """
    Rate limiting middleware.
    Limits requests per user to prevent abuse.
    """

    def __init__(
        self,
        max_requests: int = 10,
        window_seconds: int = 60,
    ):
        self._max_requests = max_requests
        self._window = window_seconds
        self._user_requests: Dict[int, List[float]] = defaultdict(list)

    def is_allowed(self, user_id: int) -> bool:
        """
        Check if a user is within the rate limit.

        Args:
            user_id: Telegram user ID

        Returns:
            True if allowed, False if rate limited
        """
        now = time.monotonic()
        window_start = now - self._window

        # Clean old entries
        self._user_requests[user_id] = [t for t in self._user_requests[user_id] if t > window_start]

        if len(self._user_requests[user_id]) >= self._max_requests:
            return False

        self._user_requests[user_id].append(now)
        return True

    def get_reset_time(self, user_id: int) -> int:
        """Return seconds until rate limit resets for a user."""
        if not self._user_requests[user_id]:
            return 0
        oldest = min(self._user_requests[user_id])
        reset_at = oldest + self._window
        return max(0, int(reset_at - time.monotonic()))
