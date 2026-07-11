"""
Level 1 (Mocked/Real-DB Unit) — ironshield.bot.middlewares.auth

Target: `AuthMiddleware` — security-critical gatekeeping used by
`bot/handlers/admin.py` and `bot/handlers/user.py` (both call
`self.auth.is_admin(...)` / `self.auth.is_registered_user(...)` directly;
see grep evidence in the session report).

Architectural note (documented, not fixed): `AuthMiddleware` is NOT an
aiogram pipeline middleware — it has no `__call__(handler, event, data)`
and is never registered via `dp.message.middleware(...)`. It is a plain
helper object injected into handler classes, which each perform their own
`if not self.auth.is_admin(...) and not self.auth.is_registered_user(...)`
branching. This means the actual "reject unauthorized user" *decision* is
implemented per-handler, not inside this module. This file therefore tests
the security PRIMITIVES this module actually exposes
(`is_admin`, `is_registered_user`, `add_admin`, `remove_admin`), not a
unified authorize-or-reject entrypoint, because no such entrypoint exists
in `ironshield/bot/middlewares/auth.py`. Testing handler-level branching
is a different module and out of scope for this file.

Existing coverage (tests/unit/test_bot.py::TestAuthMiddleware — NOT
duplicated here):
- admin recognized / non-admin rejected
- add_admin / remove_admin (happy path, member exists)
- is_registered_user with db=None
- is_registered_user with a real DB: active+not-expired user found,
  unknown telegram_id not found
- is_registered_user: expired user (is_active=True, expire_at in the past)
  correctly rejected

Gaps identified by direct inspection of `AuthMiddleware.is_registered_user`
and `ironshield/db/models.py::User` (confirmed this session, not carried
from prior documents) and covered here:

1. **Fail-closed on DB/session error** — `is_registered_user` wraps the
   whole DB lookup in a bare `except Exception: return False`. No existing
   test ever causes that except-branch to actually execute. Covered by
   two independent reproductions: a real, uninitialized `Database` (raises
   `RuntimeError` from `session()` itself) and a mocked DB raising a
   different, unrelated exception type from `.session()` — to show the
   catch is generic (`except Exception`), not tied to one exception class.

2. **`is_blocked` is never checked — confirmed, unfixed security finding.**
   `is_registered_user`'s query is
   `filter_by(telegram_id=user_id, is_active=True)`, and the only
   additional check is `not user.is_expired`. `User.is_blocked` (a real,
   separate column on the model) is never read anywhere in this method.
   A user with `is_active=True, is_blocked=True, expire_at=None` is
   therefore currently treated as a registered/authorized user. This test
   file DOCUMENTS this exact current behavior (asserts what the code
   *does*, not what it *should* do) per phase rules — no production code
   is touched or fixed here.

3. **`is_active=False` (deactivated, not expired) user is rejected** —
   distinct from the already-covered "expired" case; not previously
   exercised.

4. **Defensive/edge-case inputs** — `is_admin`/`remove_admin` with
   non-member ids, `None`, `0`, negative ids, and duplicate `admin_ids` at
   construction time — confirming no exception is raised and behavior is
   the plain, unsurprising set-membership semantics implied by the source
   (`Set[int]` + `in`/`discard`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from ironshield.bot.middlewares.auth import AuthMiddleware


# ── Fail-closed on DB / session error ──────────────────────────────


class TestFailClosedOnDatabaseError:
    def test_uninitialized_real_database_fails_closed(self, tmp_path):
        """A real Database() that was never .init()'d raises RuntimeError
        from session() (ironshield/db/database.py:130). is_registered_user
        must not propagate this — it must be swallowed and treated as
        'not registered', matching the fail-closed intent of the bare
        except clause."""
        from ironshield.db.database import Database

        db = Database(tmp_path / "never_initialized.db")
        # deliberately NOT calling db.init()

        auth = AuthMiddleware(admin_ids=[], db=db)

        # Must not raise — must fail closed.
        assert auth.is_registered_user(12345) is False

    def test_arbitrary_db_exception_type_fails_closed(self):
        """The except clause is `except Exception`, not tied to one
        exception class. Reproduce with an unrelated exception type
        (ConnectionError) raised directly from db.session() to confirm the
        catch is generic, not coincidentally matching only RuntimeError as
        in the real-DB test above."""
        db = MagicMock()
        db.session.side_effect = ConnectionError("simulated db connection loss")

        auth = AuthMiddleware(admin_ids=[], db=db)

        assert auth.is_registered_user(999) is False

    def test_exception_raised_inside_session_context_also_fails_closed(self):
        """Distinguish 'db.session() call itself raises' (test above) from
        'the session is entered successfully but the query inside raises'
        — both must be swallowed by the same except clause."""
        session_cm = MagicMock()
        session_cm.__enter__.return_value.query.side_effect = RuntimeError(
            "simulated query failure"
        )
        session_cm.__exit__.return_value = False

        db = MagicMock()
        db.session.return_value = session_cm

        auth = AuthMiddleware(admin_ids=[], db=db)

        assert auth.is_registered_user(42) is False


# ── Confirmed, unfixed finding: is_blocked is never checked ────────


class TestIsBlockedEnforced:
    """PR-2 / F-001 FIX: `is_blocked` is now enforced in
    is_registered_user() (see ironshield/bot/middlewares/auth.py). This
    class previously documented the CONFIRMED, unfixed bypass
    (LEVEL1_FINDINGS_BACKLOG.md P0-1) — renamed from
    TestIsBlockedNotEnforced now that the fix is in place, and the
    assertion below is inverted to match."""

    def test_blocked_active_unexpired_user_is_rejected(
        self, tmp_path
    ):
        from ironshield.db.database import Database
        from ironshield.db.models import User

        db = Database(tmp_path / "blocked_user.db")
        db.init()

        with db.session() as s:
            s.add(
                User(
                    username="blocked_user",
                    telegram_id=555,
                    is_active=True,
                    is_blocked=True,  # explicitly blocked
                    expire_at=None,  # not expired
                )
            )

        auth = AuthMiddleware(admin_ids=[], db=db)

        # FIXED: is_blocked is now consulted in is_registered_user, so a
        # blocked user is correctly rejected.
        assert auth.is_registered_user(555) is False

        db.close()

    def test_blocked_and_expired_user_rejected_regression_F_001(self, tmp_path):
        """Both conditions simultaneously true — locks in the combined
        case explicitly, not just each condition in isolation."""
        from datetime import datetime, timedelta, timezone

        from ironshield.db.database import Database
        from ironshield.db.models import User

        db = Database(tmp_path / "blocked_and_expired_user.db")
        db.init()

        with db.session() as s:
            s.add(
                User(
                    username="blocked_and_expired_user",
                    telegram_id=556,
                    is_active=True,
                    is_blocked=True,
                    expire_at=datetime.now(timezone.utc) - timedelta(days=1),
                )
            )

        auth = AuthMiddleware(admin_ids=[], db=db)

        assert auth.is_registered_user(556) is False

        db.close()

    def test_non_blocked_active_unexpired_user_still_registered_regression_F_001(
        self, tmp_path
    ):
        """Explicit non-regression check: the ordinary, correct
        'registered' path (is_active=True, is_blocked=False, not
        expired) must be completely untouched by this fix."""
        from ironshield.db.database import Database
        from ironshield.db.models import User

        db = Database(tmp_path / "normal_user.db")
        db.init()

        with db.session() as s:
            s.add(
                User(
                    username="normal_user",
                    telegram_id=557,
                    is_active=True,
                    is_blocked=False,
                    expire_at=None,
                )
            )

        auth = AuthMiddleware(admin_ids=[], db=db)

        assert auth.is_registered_user(557) is True

        db.close()


# ── is_active=False (deactivated, not expired) ──────────────────────


class TestInactiveUserRejected:
    def test_inactive_unexpired_user_not_registered(self, tmp_path):
        """Distinct from the already-covered 'expired' scenario: here the
        user is explicitly deactivated (is_active=False) while still
        within any notional validity window (expire_at=None). The SQL
        filter (`filter_by(..., is_active=True)`) should exclude this row
        entirely, independent of the is_expired property."""
        from ironshield.db.database import Database
        from ironshield.db.models import User

        db = Database(tmp_path / "inactive_user.db")
        db.init()

        with db.session() as s:
            s.add(
                User(
                    username="deactivated_user",
                    telegram_id=321,
                    is_active=False,
                    expire_at=None,
                )
            )

        auth = AuthMiddleware(admin_ids=[], db=db)

        assert auth.is_registered_user(321) is False

        db.close()


# ── Defensive / edge-case inputs ────────────────────────────────────


class TestAuthMiddlewareEdgeCases:
    def test_constructor_deduplicates_admin_ids(self):
        auth = AuthMiddleware(admin_ids=[111, 111, 222, 111], db=None)
        assert auth.is_admin(111) is True
        assert auth.is_admin(222) is True

    def test_constructor_with_empty_admin_list(self):
        auth = AuthMiddleware(admin_ids=[], db=None)
        assert auth.is_admin(1) is False
        assert auth.is_admin(0) is False

    def test_is_admin_does_not_raise_on_none(self):
        auth = AuthMiddleware(admin_ids=[111], db=None)
        assert auth.is_admin(None) is False

    def test_is_admin_does_not_raise_on_negative_id(self):
        auth = AuthMiddleware(admin_ids=[111], db=None)
        assert auth.is_admin(-1) is False

    def test_is_admin_zero_is_not_confused_with_falsy_admin(self):
        """0 is falsy in Python; confirm membership check (`in`), not a
        truthiness check, is what's actually used — 0 must only be admin
        if explicitly whitelisted."""
        auth_without_zero = AuthMiddleware(admin_ids=[111], db=None)
        assert auth_without_zero.is_admin(0) is False

        auth_with_zero = AuthMiddleware(admin_ids=[0], db=None)
        assert auth_with_zero.is_admin(0) is True

    def test_remove_admin_on_non_member_does_not_raise(self):
        """set.discard() (not .remove()) is used in source — confirm no
        KeyError for removing an id that was never added."""
        auth = AuthMiddleware(admin_ids=[111], db=None)
        auth.remove_admin(999)  # must not raise
        assert auth.is_admin(111) is True
        assert auth.is_admin(999) is False

    def test_add_admin_twice_is_idempotent(self):
        auth = AuthMiddleware(admin_ids=[], db=None)
        auth.add_admin(42)
        auth.add_admin(42)
        assert auth.is_admin(42) is True

    def test_is_registered_user_with_none_user_id_and_no_db_fails_closed(self):
        auth = AuthMiddleware(admin_ids=[], db=None)
        assert auth.is_registered_user(None) is False

    def test_is_registered_user_unknown_telegram_id_with_real_db(self, tmp_path):
        """Sanity companion to the fail-closed tests above: confirm the
        *normal* not-found path (no exception at all) also returns False,
        so the fail-closed tests are proven to be exercising the except
        branch specifically, not just the ordinary 'no such user' path."""
        from ironshield.db.database import Database

        db = Database(tmp_path / "empty_but_initialized.db")
        db.init()

        auth = AuthMiddleware(admin_ids=[], db=db)
        assert auth.is_registered_user(4242) is False

        db.close()
