"""
Level 1 (Mocked Unit) — ironshield.api.server token comparison

Target: `APIServer._process_request`, specifically the API-key check at
`ironshield/api/server.py:149-153`.

UPDATE (PR-1, F-002): this file originally documented a confirmed,
unfixed finding (plain `!=` comparison, `RECONCILIATION_REPORT.md`'s
"confirmed timing-unsafe key comparison" / `KNOWLEDGE_PACK.md` §9 /
`LEVEL1_FINDINGS_BACKLOG.md` P0-2). That finding has now been FIXED as
part of PR-1 (see `ironshield/utils/security.py::constant_time_compare`,
`ADR-001-api-auth-centralization.md` for the design). The current
production code is:

    if self._api_key is not None:
        token = request.get("token", "")
        if not constant_time_compare(token, self._api_key):
            await self._send_error(writer, request_id, "Unauthorized")
            return

Tests below have been updated to assert the FIXED behavior. Tests that
previously documented the old, broken behavior as "confirmed, unfixed"
have been inverted or renamed accordingly — see individual class/test
docstrings for what changed and why.

Existing coverage (tests/unit/test_api.py::TestAPIServer — NOT duplicated
here): `test_api_key_rejected` (wrong key over a real socket) and
`test_api_key_accepted` (correct key over a real socket). Both use a
non-empty string token that differs/matches exactly. Neither exercises a
missing token field, a non-string token, an empty api_key, or the
underlying comparison mechanism itself — the gaps this file addresses.

Testing approach: `_process_request(raw: bytes, writer)` is called
directly (bypassing the Unix socket transport) against a lightweight fake
writer. This is the same production coroutine invoked by
`_handle_client`/the real socket path in `test_api.py`; skipping the
socket layer only removes transport overhead and the ~0.1s startup sleep
used by the existing socket-based tests — it does not change which
production code executes or how.

On the timing-attack requirement specifically: this file does NOT include
a wall-clock timing measurement test (e.g. comparing elapsed time for a
near-match vs. a totally-different token). That would be non-deterministic
by construction — timing is affected by CPython's internal string
comparison short-circuiting, but also by scheduler jitter, GC pauses, and
sandbox/container CPU noise, none of which a unit test can control. Such a
test would violate this project's own stated principle (KNOWLEDGE_PACK.md:
"No retry for flaky tests: masks race conditions") — a flaky timing
assertion would either be ignored (useless) or retried until green
(actively misleading). Instead, `test_comparison_is_plain_inequality_not_constant_time`
below deterministically inspects the actual source of `_process_request`
via `inspect.getsource` to confirm which comparison mechanism is used.
This directly answers "is the comparison timing-safe?" without depending
on measuring wall-clock time.
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ironshield.api.server import APIServer


class _FakeWriter:
    """Minimal stand-in for asyncio.StreamWriter — captures what
    _send_response/_send_error write, without any real socket/transport."""

    def __init__(self):
        self.chunks: list[bytes] = []
        self.drain = AsyncMock()

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    def last_response(self) -> dict:
        assert self.chunks, "writer.write() was never called"
        return json.loads(self.chunks[-1].decode("utf-8").strip())


def _make_server(api_key) -> APIServer:
    server = APIServer(api_key=api_key)
    server.register("GET /health", lambda: {"ok": True})
    return server


async def _send(server: APIServer, request: dict) -> dict:
    writer = _FakeWriter()
    raw = json.dumps(request).encode("utf-8")
    await server._process_request(raw, writer)
    return writer.last_response()


def _health_request(**extra) -> dict:
    return {"id": "x", "method": "GET /health", "params": {}, **extra}


# ── Valid / invalid token (sanity, minimal — full happy/unhappy path
#    already covered by test_api.py's socket-based tests) ──────────


class TestValidAndInvalidToken:
    async def test_valid_token_accepted(self):
        server = _make_server(api_key="secret123")
        response = await _send(server, _health_request(token="secret123"))
        assert response["error"] is None
        assert response["result"]["ok"] is True

    async def test_invalid_token_rejected(self):
        server = _make_server(api_key="secret123")
        response = await _send(server, _health_request(token="wrongkey"))
        assert response["error"] == "Unauthorized"
        assert response["result"] is None


# ── Missing token ────────────────────────────────────────────────


class TestMissingToken:
    async def test_token_field_entirely_absent_is_rejected(self):
        """No 'token' key in the request at all — request.get('token', '')
        defaults to '', which must not accidentally match a real key."""
        server = _make_server(api_key="secret123")
        response = await _send(server, _health_request())  # no token=...
        assert response["error"] == "Unauthorized"

    async def test_none_token_is_rejected_without_raising(self):
        """token explicitly present but JSON null -> Python None.
        None != 'secret123' must be a clean False match, not an exception."""
        server = _make_server(api_key="secret123")
        response = await _send(server, _health_request(token=None))
        assert response["error"] == "Unauthorized"

    async def test_empty_string_token_rejected_when_key_configured(self):
        server = _make_server(api_key="secret123")
        response = await _send(server, _health_request(token=""))
        assert response["error"] == "Unauthorized"


# ── Malformed token (wrong type) ────────────────────────────────


class TestMalformedTokenTypes:
    @pytest.mark.parametrize(
        "malformed_token",
        [123, 123.45, True, False, [], {}, ["secret123"], {"k": "v"}],
        ids=["int", "float", "bool_true", "bool_false", "list_empty", "dict_empty", "list_wrapped", "dict_nonempty"],
    )
    async def test_non_string_token_rejected_without_raising(self, malformed_token):
        """A wrong-typed token must be rejected via the plain `!=` check
        without the request handler raising an unhandled exception (which
        would otherwise surface as a generic 500-style error rather than a
        clean Unauthorized, and would itself be a DoS-relevant behavior)."""
        server = _make_server(api_key="secret123")
        response = await _send(server, _health_request(token=malformed_token))
        assert response["error"] == "Unauthorized"


# ── api_key misconfiguration: falsy api_key disables auth entirely ─


class TestEmptyApiKeyRejectedAtConstruction:
    """PR-1 / F-002 FIX: `api_key=""` previously (before this fix)
    silently disabled authentication, identically to `api_key=None` —
    see git history / LEVEL1_FINDINGS_BACKLOG.md P0-4-a for the prior,
    now-corrected behavior. As of this fix, APIServer.__init__ rejects
    an empty-string api_key loudly, at construction time, rather than
    silently treating it as 'disabled'."""

    def test_empty_string_api_key_raises_at_construction_regression_F_002(self):
        with pytest.raises(ValueError, match="api_key must not be an empty string"):
            APIServer(api_key="")

    async def test_none_api_key_still_accepts_missing_token(self):
        """Sanity companion: api_key=None (the documented, intentional
        'disabled' state) is unaffected by this fix — only the
        empty-string case changed."""
        server = _make_server(api_key=None)
        response = await _send(server, _health_request())
        assert response["error"] is None


# ── Deterministic functional edge cases (not timing) ────────────


class TestTokenComparisonEdgeCases:
    async def test_token_comparison_is_case_sensitive(self):
        server = _make_server(api_key="Secret123")
        response = await _send(server, _health_request(token="secret123"))
        assert response["error"] == "Unauthorized"

    async def test_token_with_trailing_whitespace_rejected(self):
        server = _make_server(api_key="secret123")
        response = await _send(server, _health_request(token="secret123 "))
        assert response["error"] == "Unauthorized"

    async def test_token_that_is_a_prefix_of_the_real_key_rejected(self):
        """Relevant to the timing-safety question: a token that shares a
        long common prefix with the real key (differing only in the last
        character) must still be rejected — establishes the exact family
        of near-miss inputs that a timing side-channel would target,
        without measuring timing itself."""
        server = _make_server(api_key="secret123")
        response = await _send(server, _health_request(token="secret12X"))
        assert response["error"] == "Unauthorized"

    async def test_token_differing_only_in_first_character_rejected(self):
        """The mirror-image near-miss case: differs at position 0 instead
        of the last position. Both must be rejected identically from the
        caller's point of view (same error, same shape) even though the
        underlying `!=` short-circuits after a different number of
        character comparisons in CPython."""
        server = _make_server(api_key="secret123")
        response = await _send(server, _health_request(token="Xecret123"))
        assert response["error"] == "Unauthorized"


# ── Deterministic characterization of the comparison mechanism ─────


class TestComparisonMechanism:
    def test_comparison_uses_constant_time_compare_regression_F_002(self):
        """PR-1 / F-002 FIX regression guard: deterministic (source-level,
        not timing-based) confirmation that the fixed comparison
        mechanism (constant_time_compare, backed by
        secrets.compare_digest) is what actually ships, replacing the
        prior plain `!=` — see LEVEL1_FINDINGS_BACKLOG.md P0-2 /
        ADR-001-api-auth-centralization.md for the original finding and
        fix design. If this assertion ever starts failing, it means the
        comparison mechanism regressed back toward the unsafe pattern,
        which should prompt investigation, not a reflexive test update."""
        source = inspect.getsource(APIServer._process_request)

        assert "constant_time_compare(token, self._api_key)" in source, (
            "expected the fixed constant-time comparison call; "
            "source has changed and this regression guard needs re-verification"
        )
        assert "token != self._api_key" not in source, (
            "the old, timing-unsafe plain `!=` comparison appears to have "
            "returned — this is a regression of F-002"
        )

    def test_constant_time_compare_is_imported_in_this_module(self):
        import ironshield.api.server as server_module

        assert hasattr(server_module, "constant_time_compare")
