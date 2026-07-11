"""
Level (per TEST_EXECUTION_ORDER.md this is Level 5 "Agent Integration" —
noted explicitly here since this session's instruction requested it as
the "next Level 1 priority"; the mismatch is documented, not silently
resolved, per this project's own Contradiction Protocol) —
ironshield.agent.api.AgentAPIServer._route

Target: `_route`, radon-confirmed this session as the project's sole
Cyclomatic Complexity outlier:

    radon cc ironshield/agent/api.py -s -a
    M 149:4 AgentAPIServer._route - D (21)

(next highest in the same file is `_parse_request` at B (7); everything
else in the project is A-graded — re-verified this session, matching
KNOWLEDGE_PACK.md/TEST_PRIORITY.md's "تنها Complexity outlier پروژه"
claim exactly.)

Existing coverage (tests/unit/test_agent.py::TestAgentAPIServer — NOT
duplicated here): a happy-path test for every documented route (health,
metrics, services, service detail found/not-found, logs, ping,
service start/stop/restart, invalid action, generic 404, snapshot) plus
three auth tests (wrong key rejected, correct key accepted, no key
configured passes). This is good happy-path coverage but is NOT
branch/decision-table coverage of a D-21 function — TEST_ROADMAP.md
explicitly calls for "تجزیه‌ی decision-table کامل قبل از نوشتن هر
تستی... نه فقط چند تست تصادفی" for this exact function.

This file targets the untested DECISION branches and FALL-THROUGH paths
identified by manually tracing every `if` in `_route` against inputs the
existing suite never sends it (verified by reading
tests/unit/test_agent.py in full before writing this file — no existing
test sends a multi-segment/malformed path, an empty service name, a
lowercase HTTP method, a mismatched method for an otherwise-valid path,
or an empty/malformed X-Agent-Key).

This file also covers TEST_PRIORITY.md P0 item #2 (`X-Agent-Key`
comparison, `agent/api.py:161`), since that check lives inside `_route`
itself and cannot be meaningfully separated from it — combining the two
P0 items avoids re-deriving the same fixture/harness twice.

No global monkeypatching is used anywhere in this file (lesson carried
from tests/unit/test_base_service_contract.py, where a global
`patch("pathlib.Path.exists", ...)` was found to leak into Python's own
`logging` internals and cause order-dependent flakiness). Every mock here
is scoped to the `collector` fixture (a plain MagicMock, following the
same pattern as the existing test_agent.py suite) or to explicit
`mock.assert_called_with(...)` assertions — nothing patches a builtin or
stdlib module.
"""

from __future__ import annotations

import inspect

import pytest

from ironshield.agent.api import AgentAPIServer
from ironshield.agent.collector import ServiceSnapshot, SystemSnapshot


@pytest.fixture
def collector():
    """Same shape as tests/unit/test_agent.py's collector fixture,
    duplicated locally to keep this file independently runnable and to
    avoid a cross-file fixture dependency."""
    from unittest.mock import MagicMock

    c = MagicMock()
    c.get_system_metrics.return_value = SystemSnapshot(
        cpu_percent=35.0, ram_percent=50.0, disk_percent=20.0
    )
    c.get_service_status.return_value = [
        ServiceSnapshot("gost", "GOST", "ironshield-gost", True),
    ]
    c.get_service_by_name.return_value = None  # default: unknown -> 404
    c.get_service_logs.return_value = []
    c.get_full_snapshot.return_value = {"agent_version": "1.0.0", "system": {}, "services": []}
    c.start_service.return_value = {"success": True, "message": "started"}
    c.stop_service.return_value = {"success": True, "message": "stopped"}
    c.restart_service.return_value = {"success": True, "message": "restarted"}
    return c


@pytest.fixture
def server(collector):
    return AgentAPIServer(collector=collector, host="127.0.0.1", port=9998)


# ── Decision-table: fall-through / non-matching path shapes ────────


class TestRouteFallThroughBranches:
    """Every case here traces a path through `_route` that does NOT hit
    any of the intended handler branches, ending in the generic 404 —
    each for a DIFFERENT reason, confirmed by reading the source."""

    async def test_get_services_with_logs_substring_falls_through_to_404(self, server):
        """path.startswith('/services/') is True but '/logs' in path
        makes the GET-service-detail condition False (api/agent/api.py:174);
        it also doesn't start with '/logs/' (that check is prefix-based,
        not substring), so this specific shape matches NEITHER handler."""
        status, body = await server._route("GET", "/services/gost/logs", {}, b"")
        assert status == 404
        assert "error" in body

    async def test_post_services_single_segment_no_action_falls_through(self, server):
        """POST /services/{name} with no /{action} suffix: parts has
        length 1, the `len(parts) == 2` guard (agent/api.py:187) fails,
        and there is no other branch that matches POST /services/<x> —
        falls all the way to 404, not to a service-action handler."""
        status, body = await server._route("POST", "/services/gost", {}, b"")
        assert status == 404

    async def test_post_services_too_many_segments_falls_through(self, server):
        status, body = await server._route("POST", "/services/gost/start/extra", {}, b"")
        assert status == 404

    async def test_get_snapshot_wrong_method_falls_through(self, server):
        """/snapshot only matches GET; POST to the same path is not a
        recognized route at all (no method-mismatch-specific error)."""
        status, body = await server._route("POST", "/snapshot", {}, b"")
        assert status == 404

    async def test_get_health_wrong_method_falls_through(self, server):
        status, body = await server._route("POST", "/health", {}, b"")
        assert status == 404

    async def test_get_ping_wrong_method_falls_through(self, server):
        """/ping only matches POST; GET falls through to 404."""
        status, body = await server._route("GET", "/ping", {}, b"")
        assert status == 404

    async def test_lowercase_method_never_matches_any_route(self, server):
        """_route does no case-normalization of `method` itself (that is
        done by the caller, _parse_request, via .upper() at
        agent/api.py:134). Calling _route directly with a lowercase verb
        — exactly as any future caller that skips _parse_request would —
        must not match /health despite the path being exactly right."""
        status, body = await server._route("get", "/health", {}, b"")
        assert status == 404

    async def test_trailing_slash_on_exact_match_route_falls_through(self, server):
        """'/health/' != '/health', and it doesn't match any
        startswith-based branch either -> 404, not treated the same as
        '/health'."""
        status, body = await server._route("GET", "/health/", {}, b"")
        assert status == 404


# ── Decision-table: paths that DO match, but with surprising/edge args ──


class TestRouteEdgeCaseMatches:
    """Cases that DO reach a handler, but with an argument shape the
    happy-path tests never exercise (empty name, slash-containing name,
    method/path shape collisions between the GET and POST branches)."""

    async def test_post_services_double_slash_reaches_handler_with_empty_name(
        self, server, collector
    ):
        """POST /services//start : removeprefix('/services/') leaves
        '/start'... wait — removeprefix only strips the literal
        '/services/' once, from '/services//start' that yields '/start',
        which split('/') gives ['', 'start'] (empty string before the
        second slash) -> len == 2 -> IS treated as a valid
        (name='', action='start') service-action call. This means an
        empty service name reaches AgentCollector.start_service()
        completely unvalidated by the routing layer."""
        status, body = await server._route("POST", "/services//start", {}, b"")
        collector.start_service.assert_called_once_with("")
        assert status == 200  # because the mocked collector reports success=True

    async def test_get_services_trailing_slash_reaches_handler_with_empty_name(
        self, server, collector
    ):
        """GET /services/ (trailing slash, nothing after it):
        startswith('/services/') is True, '/logs' not in path is True,
        so this IS treated as a service-detail lookup with name=''."""
        status, body = await server._route("GET", "/services/", {}, b"")
        collector.get_service_by_name.assert_called_once_with("")
        assert status == 404  # because the fixture's default is "not found"

    async def test_get_request_to_action_shaped_path_treated_as_service_detail(
        self, server, collector
    ):
        """GET /services/gost/start does NOT reach the POST action
        handler (wrong method) — but it DOES match the GET
        service-detail branch (starts with '/services/', no '/logs'
        substring), with the ENTIRE remainder ('gost/start', slash and
        all) passed as a single opaque `name` to
        collector.get_service_by_name(). This is a genuine path-shape
        collision between the GET-detail and POST-action route families:
        the same URL shape means two different things depending on
        HTTP method, and the GET side does no segment-count validation
        at all."""
        status, body = await server._route("GET", "/services/gost/start", {}, b"")
        collector.get_service_by_name.assert_called_once_with("gost/start")
        assert status == 404  # fixture default: unknown name -> None -> 404


# ── X-Agent-Key auth (TEST_PRIORITY.md P0 item #2) ──────────────────


class TestAgentKeyAuthBoundaries:
    async def test_missing_header_rejected_when_key_configured(self, collector):
        """Existing test_agent.py only sends an explicit wrong value
        ('wrongkey'); the header-entirely-absent case (dict has no
        'x-agent-key' key at all -> headers.get(..., '') defaults to '')
        is untested."""
        server = AgentAPIServer(collector=collector, api_key="secret123")
        status, body = await server._route("GET", "/health", {}, b"")
        assert status == 401
        assert body["error"] == "Unauthorized"

    def test_empty_string_api_key_raises_at_construction_regression_F_003(
        self, collector
    ):
        """PR-1 / F-003 FIX: same class of finding as api/server.py's
        `if self._api_key:` (see test_api_token_comparison.py for the
        F-002 equivalent). `agent/api.py`'s AgentAPIServer.__init__ now
        rejects api_key='' loudly at construction, rather than silently
        treating it as identical to api_key=None. See
        LEVEL1_FINDINGS_BACKLOG.md P0-4-b for the prior, now-corrected
        behavior."""
        with pytest.raises(ValueError, match="api_key must not be an empty string"):
            AgentAPIServer(collector=collector, api_key="")

    @pytest.mark.parametrize(
        "malformed_key",
        [123, 123.4, True, False, [], {}, ["secret123"]],
        ids=["int", "float", "bool_true", "bool_false", "list_empty", "dict_empty", "list_wrapped"],
    )
    async def test_malformed_header_value_types_rejected_without_raising(
        self, collector, malformed_key
    ):
        """_route is called directly here (bypassing _parse_request,
        which always yields str values from real HTTP text) to confirm
        the `!=` comparison at agent/api.py:161 degrades cleanly — no
        unhandled exception — for any Python object a future caller
        might pass, not just strings."""
        server = AgentAPIServer(collector=collector, api_key="secret123")
        status, body = await server._route(
            "GET", "/health", {"x-agent-key": malformed_key}, b""
        )
        assert status == 401

    async def test_auth_is_checked_before_route_existence(self, collector):
        """Security-relevant ordering: a wrong key on a COMPLETELY
        NONEXISTENT path must still return 401, not 404 — confirming the
        auth check (agent/api.py:159-162) runs unconditionally before any
        route matching, and therefore does not leak which paths exist to
        an unauthenticated/mis-keyed caller."""
        server = AgentAPIServer(collector=collector, api_key="secret123")
        status, body = await server._route(
            "GET", "/this/path/does/not/exist/anywhere", {"x-agent-key": "wrong"}, b""
        )
        assert status == 401
        assert body["error"] == "Unauthorized"

    def test_comparison_uses_constant_time_compare_regression_F_003(self):
        """PR-1 / F-003 FIX regression guard: deterministic, source-level
        confirmation that agent/api.py now uses constant_time_compare
        (backed by secrets.compare_digest) instead of the prior plain
        `!=` — see LEVEL1_FINDINGS_BACKLOG.md P0-3 /
        ADR-001-api-auth-centralization.md for the original finding and
        fix design."""
        source = inspect.getsource(AgentAPIServer._route)
        assert "constant_time_compare(provided, self.api_key)" in source, (
            "expected the fixed constant-time comparison call; "
            "source has changed and this regression guard needs re-verification"
        )
        assert "provided != self.api_key" not in source, (
            "the old, timing-unsafe plain `!=` comparison appears to have "
            "returned — this is a regression of F-003"
        )

    def test_constant_time_compare_is_imported_in_agent_api(self):
        import ironshield.agent.api as agent_api_module

        assert hasattr(agent_api_module, "constant_time_compare")
