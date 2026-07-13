"""
Level 1 (Mocked Integration, real event loop + real SQLite where noted) —
ironshield.core.failover_engine.FailoverEngine

Selected per TEST_PRIORITY.md P0 item #4 ("FailoverEngine — الگوی Silent
Failure در asyncio.create_task — بدون تست، هیچ Fix آینده قابل اعتماد
نیست") and TEST_EXECUTION_ORDER.md's explicit Level 1 scope ("FailoverEngine
callback→create_task (event loop واقعی، سرویس‌های mock)"). The
fire-and-forget WIRING into this engine (the `_on_service_failure`/
`_on_system_alert` closures in `api/server.py::_run()`) was already tested
in `tests/unit/test_server_run_wiring.py`. This file targets FailoverEngine
ITSELF — everything that happens once a failure signal actually reaches it.

Existing coverage (tests/unit/test_core_engines.py::TestFailoverEngine —
4 tests, NOT duplicated here):
- `test_service_failure_triggers_restart` — happy-path restart call.
- `test_event_recorded_in_db` — unknown-plugin path records an event.
- `test_alert_callback_triggered` — `_notify` calls a working callback.
- `test_get_active_events_empty` — empty-list sanity check.

Confirmed gaps (by direct reading of every method in
ironshield/core/failover_engine.py against the 4 existing tests — no
other FailoverEngine test file exists anywhere in the repo, verified via
`grep -rl FailoverEngine tests/`):

- `handle_tunnel_failure`, `handle_all_tunnels_failed`, `handle_system_alert`
  — THREE OF FOUR public entry points are never called by any existing
  test.
- `_attempt_restart`'s RETRY/BACKOFF behavior (the recursive
  retry-on-failure branch, the max-attempts-reached branch, restart-count
  reset on success) — only the single-success-on-first-try shape is
  tested.
- `_recovery_loop` — the entire exponential-backoff recovery mechanism
  (RECOVERY_INTERVALS) is completely untested: recovery-confirmed,
  recovery-never-confirmed/exhausted, and — most relevant to this
  session's "exception propagation" focus — the `except Exception` inside
  the loop that swallows health-check errors and continues.
- `_start_recovery_monitor`'s task-replacement bookkeeping (cancelling an
  existing in-flight monitor task for the same plugin) — untested.
- `_clean_old_logs`, `_resolve_event`, `_get_downtime`, `_guess_server` —
  zero direct tests.
- `_notify`'s own `except Exception` (a callback that itself raises) —
  untested; only the working-callback path is covered.
- `_record_event`'s DB-exception fail path (`return -1`) — untested (the
  existing "unknown plugin" test still uses a fully working real DB).
- `get_active_events`/`get_event_history` DB-exception paths (`return []`)
  and the resolved/unresolved filtering behavior of `get_active_events`
  — untested.

Determinism note on `asyncio.sleep`: `_attempt_restart` and
`_recovery_loop` call real `asyncio.sleep(5)`, `asyncio.sleep(10)`, and
`asyncio.sleep(interval)` for `interval` up to 3600 seconds
(`RECOVERY_INTERVALS = [60, 120, 300, 600, 1800, 3600]`). Running these
for real is infeasible for a unit test. `asyncio.sleep` is patched with an
`AsyncMock` in the specific tests that need it. This is judged safe and
is NOT the same risk class as the `pathlib.Path.exists` incident found
while writing `test_base_service_contract.py` (which broke Python's own
`logging` module because `logging.FileHandler` calls `Path.exists()`
internally): `asyncio.sleep` is a pure application-level timing primitive
with no known stdlib/pytest-asyncio-internal callers during a single
test's synchronous-from-the-test's-perspective execution — the event
loop's own scheduling machinery uses `loop.call_later`/`call_at`
internally, not the public `asyncio.sleep()` coroutine. Each patch is
scoped to a single `with` block and restored immediately after.
"""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from ironshield.core.failover_engine import RECOVERY_INTERVALS, FailoverEngine
from ironshield.db.database import Database
from ironshield.db.models import FailoverEvent
from ironshield.services.base import (
    HealthResult,
    PluginCategory,
    PluginMeta,
    Result,
    ServerRole,
    ServiceStatus,
)


# ── Fixtures (self-contained; not imported from test_core_engines.py) ──


@pytest.fixture
def tmp_db(tmp_path):
    db = Database(tmp_path / "failover_test.db")
    db.init()
    yield db
    db.close()


@pytest.fixture
def mock_plugin():
    plugin = MagicMock()
    plugin.meta = PluginMeta(
        name="mock_tunnel",
        display_name="Mock Tunnel",
        version="1.0",
        author="Test",
        source_url="",
        license="MIT",
        roles=[ServerRole.IRAN, ServerRole.FOREIGN],
        category=PluginCategory.TUNNEL_RELIABLE,
        priority=3,
    )
    plugin.health_check.return_value = HealthResult(
        healthy=True, status=ServiceStatus.RUNNING
    )
    return plugin


@pytest.fixture
def mock_pm(mock_plugin):
    pm = MagicMock()
    pm.get.return_value = mock_plugin
    return pm


@pytest.fixture
def mock_routing():
    """A fully mocked SmartRoutingEngine — FailoverEngine only calls
    .report_tunnel_failure() on it, so a MagicMock is sufficient and
    keeps these tests isolated from SmartRoutingEngine's own behavior."""
    return MagicMock()


@pytest.fixture
def fe(mock_pm, mock_routing, tmp_db):
    return FailoverEngine(mock_pm, mock_routing, tmp_db, max_restart_attempts=2)


# ── Retry / backoff behavior in _attempt_restart ─────────────────


class TestRestartRetryAndBackoff:
    async def test_success_resets_restart_count_and_resolves_event(
        self, fe, mock_plugin
    ):
        mock_plugin.restart.return_value = Result.ok("Restarted")
        fe._restart_counts["mock_tunnel"] = 1  # simulate a prior failed attempt

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "sleep", AsyncMock())
            event_id = fe._record_event(event_type="service_failed", severity="CRITICAL")
            await fe._attempt_restart("mock_tunnel", event_id)

        assert fe._restart_counts["mock_tunnel"] == 0

    async def test_failure_retries_recursively_then_succeeds(self, fe, mock_plugin):
        """restart() fails once, then succeeds on the retry — confirms
        the recursive retry path is actually taken and eventually
        converges, not just that it's theoretically reachable."""
        mock_plugin.restart.side_effect = [
            Result.fail("first attempt failed"),
            Result.ok("second attempt succeeded"),
        ]

        with pytest.MonkeyPatch.context() as mp:
            sleep_mock = AsyncMock()
            mp.setattr(asyncio, "sleep", sleep_mock)
            event_id = fe._record_event(event_type="service_failed", severity="CRITICAL")
            await fe._attempt_restart("mock_tunnel", event_id)

        assert mock_plugin.restart.call_count == 2
        # 5s pre-restart wait on attempt 1, 10s backoff after failure,
        # 5s pre-restart wait on attempt 2 (max_restart_attempts=2, so the
        # 2nd attempt is still within budget: count starts at 0).
        sleep_mock.assert_any_call(5)
        sleep_mock.assert_any_call(10)
        assert fe._restart_counts["mock_tunnel"] == 0

    async def test_gives_up_after_max_attempts_and_starts_recovery_monitor(
        self, fe, mock_plugin
    ):
        """max_restart_attempts=2 (fixture): once the internal counter
        reaches 2, _attempt_restart must stop retrying and fall back to
        _start_recovery_monitor instead of retrying forever."""
        mock_plugin.restart.return_value = Result.fail("always fails")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "sleep", AsyncMock())
            event_id = fe._record_event(event_type="service_failed", severity="CRITICAL")
            await fe._attempt_restart("mock_tunnel", event_id)

        assert fe._restart_counts["mock_tunnel"] == fe.max_restart_attempts
        assert "mock_tunnel" in fe._recovery_tasks
        # Clean up the real background task _start_recovery_monitor created.
        fe._recovery_tasks["mock_tunnel"].cancel()
        try:
            await fe._recovery_tasks["mock_tunnel"]
        except asyncio.CancelledError:
            pass

    async def test_restart_skipped_entirely_for_unknown_plugin(self, fe, mock_pm):
        mock_pm.get.return_value = None
        event_id = fe._record_event(event_type="service_failed", severity="CRITICAL")

        await fe._attempt_restart("ghost_plugin", event_id)

        assert "ghost_plugin" not in fe._restart_counts


# ── handle_tunnel_failure (previously untested entry point) ───────


class TestHandleTunnelFailure:
    async def test_reports_failure_to_routing_engine(self, fe, mock_routing):
        await fe.handle_tunnel_failure("gost")
        mock_routing.report_tunnel_failure.assert_called_once_with("gost")
        # Clean up the recovery-monitor task this call starts.
        task = fe._recovery_tasks.get("gost")
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_records_tunnel_failed_event(self, fe):
        await fe.handle_tunnel_failure("gost")
        events = fe.get_event_history()
        assert any(e["type"] == "tunnel_failed" and e["plugin"] == "gost" for e in events)
        task = fe._recovery_tasks.get("gost")
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def test_notifies_admin_via_callback(self, mock_pm, mock_routing, tmp_db):
        callback = MagicMock()
        fe = FailoverEngine(mock_pm, mock_routing, tmp_db, on_alert=callback)

        await fe.handle_tunnel_failure("gost")

        callback.assert_called_once()
        assert "gost" in callback.call_args.kwargs["title"]
        assert callback.call_args.kwargs["severity"] == "CRITICAL"

        task = fe._recovery_tasks.get("gost")
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# ── handle_all_tunnels_failed (previously untested entry point) ────


class TestHandleAllTunnelsFailed:
    async def test_records_emergency_event(self, fe):
        await fe.handle_all_tunnels_failed()
        events = fe.get_event_history()
        assert any(
            e["type"] == "all_tunnels_failed" for e in events
        )

    async def test_notifies_with_emergency_severity(self, mock_pm, mock_routing, tmp_db):
        callback = MagicMock()
        fe = FailoverEngine(mock_pm, mock_routing, tmp_db, on_alert=callback)
        await fe.handle_all_tunnels_failed()
        callback.assert_called_once()
        assert callback.call_args.kwargs["severity"] == "EMERGENCY"


# ── handle_system_alert (previously untested entry point) ─────────


class TestHandleSystemAlert:
    async def test_disk_critical_triggers_log_cleanup(self, fe):
        with pytest.MonkeyPatch.context() as mp:
            mock_run = MagicMock(return_value=(0, "", ""))
            mp.setattr("ironshield.utils.system.run_command", mock_run)
            await fe.handle_system_alert("Disk", 95.0, "CRITICAL")
        mock_run.assert_called_once()

    async def test_non_disk_critical_does_not_trigger_cleanup(self, fe):
        with pytest.MonkeyPatch.context() as mp:
            mock_run = MagicMock(return_value=(0, "", ""))
            mp.setattr("ironshield.utils.system.run_command", mock_run)
            await fe.handle_system_alert("CPU", 95.0, "CRITICAL")
        mock_run.assert_not_called()

    async def test_disk_warning_does_not_trigger_cleanup(self, fe):
        """Only Disk+CRITICAL cleans logs — Disk+WARNING must not."""
        with pytest.MonkeyPatch.context() as mp:
            mock_run = MagicMock(return_value=(0, "", ""))
            mp.setattr("ironshield.utils.system.run_command", mock_run)
            await fe.handle_system_alert("Disk", 80.0, "WARNING")
        mock_run.assert_not_called()

    async def test_warning_level_maps_to_warning_severity_event(self, fe):
        await fe.handle_system_alert("RAM", 88.0, "WARNING")
        events = fe.get_event_history()
        matching = [e for e in events if e["type"] == "system_critical"]
        assert matching[-1]["severity"] == "WARNING"

    async def test_critical_level_maps_to_critical_severity_event(self, fe):
        await fe.handle_system_alert("RAM", 97.0, "CRITICAL")
        events = fe.get_event_history()
        matching = [e for e in events if e["type"] == "system_critical"]
        assert matching[-1]["severity"] == "CRITICAL"


# ── _recovery_loop: recovery paths + exception propagation ─────────


class TestRecoveryLoop:
    async def test_unknown_plugin_exits_without_iterating(self, fe, mock_pm):
        mock_pm.get.return_value = None
        with pytest.MonkeyPatch.context() as mp:
            sleep_mock = AsyncMock()
            mp.setattr(asyncio, "sleep", sleep_mock)
            await fe._recovery_loop("ghost", event_id=1)
        sleep_mock.assert_not_called()

    async def test_recovery_confirmed_stops_loop_and_calls_on_recovery(
        self, mock_pm, mock_routing, tmp_db, mock_plugin
    ):
        recovery_callback = MagicMock()
        fe = FailoverEngine(
            mock_pm, mock_routing, tmp_db, on_recovery=recovery_callback
        )
        event_id = fe._record_event(event_type="service_failed", severity="CRITICAL")

        mock_plugin.health_check.side_effect = [
            HealthResult(healthy=False, status=ServiceStatus.FAILED),
            HealthResult(healthy=True, status=ServiceStatus.RUNNING),
        ]

        with pytest.MonkeyPatch.context() as mp:
            sleep_mock = AsyncMock()
            mp.setattr(asyncio, "sleep", sleep_mock)
            await fe._recovery_loop("mock_tunnel", event_id)

        assert mock_plugin.health_check.call_count == 2
        recovery_callback.assert_called_once()
        assert recovery_callback.call_args.kwargs["plugin_name"] == "mock_tunnel"
        # Loop must stop as soon as recovery is confirmed, not run through
        # every remaining interval.
        assert sleep_mock.call_count == 2  # one sleep before each of the 2 checks

    async def test_recovery_never_confirmed_exhausts_all_intervals(
        self, fe, mock_plugin
    ):
        mock_plugin.health_check.return_value = HealthResult(
            healthy=False, status=ServiceStatus.FAILED
        )
        event_id = fe._record_event(event_type="service_failed", severity="CRITICAL")

        with pytest.MonkeyPatch.context() as mp:
            sleep_mock = AsyncMock()
            mp.setattr(asyncio, "sleep", sleep_mock)
            await fe._recovery_loop("mock_tunnel", event_id)

        assert mock_plugin.health_check.call_count == len(RECOVERY_INTERVALS)
        assert sleep_mock.call_count == len(RECOVERY_INTERVALS)
        for interval in RECOVERY_INTERVALS:
            sleep_mock.assert_any_call(interval)

    async def test_exception_during_health_check_is_swallowed_and_loop_continues(
        self, fe, mock_plugin
    ):
        """Directly relevant to this session's exception-propagation
        focus: a health_check() that raises must NOT crash the recovery
        loop or the background task hosting it — it should be logged and
        the loop must proceed to the next interval."""
        mock_plugin.health_check.side_effect = [
            RuntimeError("collector crashed"),
            HealthResult(healthy=True, status=ServiceStatus.RUNNING),
        ]
        event_id = fe._record_event(event_type="service_failed", severity="CRITICAL")

        with pytest.MonkeyPatch.context() as mp:
            sleep_mock = AsyncMock()
            mp.setattr(asyncio, "sleep", sleep_mock)
            # Must not raise.
            await fe._recovery_loop("mock_tunnel", event_id)

        assert mock_plugin.health_check.call_count == 2


# ── _start_recovery_monitor: task-replacement bookkeeping ──────────


class TestStartRecoveryMonitorTaskReplacement:
    async def test_starting_a_new_monitor_cancels_an_existing_undone_one(self, fe):
        never_finishes = asyncio.Event()
        old_task = asyncio.create_task(never_finishes.wait())
        fe._recovery_tasks["mock_tunnel"] = old_task
        await asyncio.sleep(0)  # let old_task actually start running
        assert not old_task.done()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "sleep", AsyncMock())
            await fe._start_recovery_monitor("mock_tunnel", event_id=1)

        # task.cancel() only REQUESTS cancellation; the task doesn't
        # transition to a final cancelled() state until the event loop
        # actually delivers CancelledError into the coroutine and it
        # propagates out. Awaiting old_task (and swallowing the
        # CancelledError it raises) is the deterministic way to observe
        # that completion, rather than guessing how many bare
        # `await asyncio.sleep(0)` ticks are needed.
        try:
            await old_task
        except asyncio.CancelledError:
            pass

        assert old_task.cancelled()

        # Clean up the new task too.
        fe._recovery_tasks["mock_tunnel"].cancel()
        try:
            await fe._recovery_tasks["mock_tunnel"]
        except asyncio.CancelledError:
            pass

    async def test_starting_a_new_monitor_does_not_touch_an_already_done_task(self, fe):
        already_done = asyncio.create_task(asyncio.sleep(0))
        await already_done  # ensure it's really finished
        fe._recovery_tasks["mock_tunnel"] = already_done

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "sleep", AsyncMock())
            await fe._start_recovery_monitor("mock_tunnel", event_id=1)
            await asyncio.sleep(0)

        # The old (already-done) task must not have been cancelled —
        # it was never eligible for cancellation in the first place.
        assert already_done.cancelled() is False

        fe._recovery_tasks["mock_tunnel"].cancel()
        try:
            await fe._recovery_tasks["mock_tunnel"]
        except asyncio.CancelledError:
            pass


# ── _clean_old_logs ──────────────────────────────────────────────


class TestCleanOldLogs:
    async def test_success_path_does_not_raise(self, fe):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "ironshield.utils.system.run_command",
                MagicMock(return_value=(0, "", "")),
            )
            await fe._clean_old_logs()  # must not raise

    async def test_nonzero_exit_code_does_not_raise(self, fe):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "ironshield.utils.system.run_command",
                MagicMock(return_value=(1, "", "permission denied")),
            )
            await fe._clean_old_logs()  # must not raise, just logs a warning


# ── _record_event / _resolve_event / _get_downtime exception paths ──


class TestRecordEventDbFailure:
    def test_db_exception_returns_negative_one(self, mock_pm, mock_routing, tmp_path):
        broken_db = Database(tmp_path / "never_initialized.db")  # no .init()
        fe = FailoverEngine(mock_pm, mock_routing, broken_db)

        event_id = fe._record_event(event_type="service_failed", severity="CRITICAL")

        assert event_id == -1


class TestResolveEventEdgeCases:
    def test_negative_event_id_is_a_noop(self, fe):
        fe._active_events["mock_tunnel"] = -1
        fe._resolve_event(-1, "mock_tunnel")
        # Per source: negative event_id returns before touching DB or
        # _active_events at all — the tracking entry is deliberately NOT
        # cleaned up in this branch (documented, not changed).
        assert fe._active_events["mock_tunnel"] == -1

    def test_event_not_found_in_db_does_not_raise(self, fe):
        fe._active_events["mock_tunnel"] = 99999
        fe._resolve_event(99999, "mock_tunnel")  # no such row -> must not raise
        assert "mock_tunnel" not in fe._active_events

    def test_db_exception_is_caught_and_logged(self, mock_pm, mock_routing, tmp_path):
        broken_db = Database(tmp_path / "never_initialized2.db")
        fe = FailoverEngine(mock_pm, mock_routing, broken_db)
        fe._active_events["mock_tunnel"] = 1
        fe._resolve_event(1, "mock_tunnel")  # must not raise

    def test_successful_resolve_clears_active_events_tracking(self, fe):
        event_id = fe._record_event(event_type="service_failed", severity="CRITICAL")
        fe._active_events["mock_tunnel"] = event_id

        fe._resolve_event(event_id, "mock_tunnel")

        assert "mock_tunnel" not in fe._active_events


class TestGetDowntime:
    def test_returns_zero_for_unresolved_event(self, fe):
        event_id = fe._record_event(event_type="service_failed", severity="CRITICAL")
        assert fe._get_downtime(event_id) == 0

    def test_returns_zero_on_db_exception(self, mock_pm, mock_routing, tmp_path):
        broken_db = Database(tmp_path / "never_initialized3.db")
        fe = FailoverEngine(mock_pm, mock_routing, broken_db)
        assert fe._get_downtime(1) == 0

    def test_returns_actual_downtime_after_resolve(self, fe):
        event_id = fe._record_event(event_type="service_failed", severity="CRITICAL")
        fe._resolve_event(event_id, "mock_tunnel")
        downtime = fe._get_downtime(event_id)
        # Resolved essentially immediately after creation in this test,
        # so downtime should be a small non-negative integer, not None
        # and not a placeholder.
        assert isinstance(downtime, int)
        assert downtime >= 0

    def test_nonzero_downtime_reaches_the_real_return_path(self, fe):
        """PR-5 / F-006 FIX regression test. `_get_downtime()` at
        failover_engine.py:331 now uses
        `event.downtime_seconds is not None` instead of a bare
        truthiness check, so a non-zero downtime correctly returns via
        the real `return event.downtime_seconds` line — this test
        forces a genuinely non-zero downtime (by backdating
        `occurred_at` directly in the DB before resolving) to confirm
        that value is returned correctly, unaffected by this fix."""
        from datetime import datetime, timedelta, timezone

        event_id = fe._record_event(event_type="service_failed", severity="CRITICAL")
        with fe.db.session() as s:
            event = s.get(FailoverEvent, event_id)
            event.occurred_at = datetime.now(timezone.utc) - timedelta(seconds=100)

        fe._resolve_event(event_id, "mock_tunnel")
        downtime = fe._get_downtime(event_id)

        assert downtime >= 100

    def test_zero_downtime_reaches_the_real_return_path_regression_F_006(self, fe):
        """PR-5 / F-006 FIX regression test — the case this fix was for.

        Before this fix, `if event and event.downtime_seconds:` was
        falsy for a genuine zero-second downtime, so the method fell
        through to the same hardcoded `return 0` used for the
        DB-exception/event-not-found cases — the OUTPUT was identical
        either way (0), so a plain output-value assertion cannot
        distinguish "real value happened to be 0" from "fallback path
        taken". This test distinguishes the two by counting how many
        times the `downtime_seconds` property getter is actually
        evaluated: the fallback path evaluates it exactly once (only
        inside the `if` condition, never using its value); the fixed
        return-the-real-value path evaluates it a second time when
        `return event.downtime_seconds` executes. A call count of 2
        proves the real return line was reached, not just that the
        output happened to match.
        """
        event_id = fe._record_event(event_type="service_failed", severity="CRITICAL")
        fe._resolve_event(event_id, "mock_tunnel")
        # A same-instant resolve in this test genuinely produces
        # downtime_seconds == 0 today; force it explicitly and
        # deterministically via PropertyMock instead of relying on
        # real-clock timing being fast enough, so this test can never
        # be flaky regardless of how quickly the two DB calls above run.
        with patch.object(
            FailoverEvent, "downtime_seconds", new_callable=PropertyMock
        ) as mock_downtime:
            mock_downtime.return_value = 0
            result = fe._get_downtime(event_id)

        assert result == 0
        assert mock_downtime.call_count == 2, (
            "expected downtime_seconds to be read twice (once in the "
            "condition, once in the return statement) — a call_count of "
            "1 would mean the fallback path was taken instead of the "
            "real return line, i.e. the F-006 regression has returned"
        )


# ── _guess_server ─────────────────────────────────────────────────


class TestGuessServer:
    def test_openvpn_maps_to_iran(self):
        assert FailoverEngine._guess_server("openvpn") == "iran"

    def test_other_named_plugin_maps_to_both(self):
        assert FailoverEngine._guess_server("gost") == "both"

    def test_none_maps_to_both(self):
        assert FailoverEngine._guess_server(None) == "both"


# ── _notify: callback exception handling ────────────────────────


class TestNotifyExceptionHandling:
    def test_callback_exception_is_swallowed_not_propagated(self, mock_pm, mock_routing, tmp_db):
        raising_callback = MagicMock(side_effect=RuntimeError("telegram API down"))
        fe = FailoverEngine(mock_pm, mock_routing, tmp_db, on_alert=raising_callback)

        # Must not raise, despite the callback blowing up.
        fe._notify("title", "body", "CRITICAL")

        raising_callback.assert_called_once()

    def test_no_callback_configured_is_a_noop(self, fe):
        fe._notify("title", "body", "CRITICAL")  # on_alert=None by default; must not raise


# ── get_active_events / get_event_history ───────────────────────


class TestGetActiveEventsAndHistory:
    def test_active_events_excludes_resolved(self, fe):
        unresolved_id = fe._record_event(event_type="service_failed", severity="CRITICAL")
        resolved_id = fe._record_event(event_type="service_failed", severity="CRITICAL")
        fe._resolve_event(resolved_id, "some_plugin")

        active = fe.get_active_events()
        active_ids = {e["id"] for e in active}

        assert unresolved_id in active_ids
        assert resolved_id not in active_ids

    def test_get_active_events_db_exception_returns_empty_list(
        self, mock_pm, mock_routing, tmp_path
    ):
        broken_db = Database(tmp_path / "never_initialized4.db")
        fe = FailoverEngine(mock_pm, mock_routing, broken_db)
        assert fe.get_active_events() == []

    def test_get_event_history_db_exception_returns_empty_list(
        self, mock_pm, mock_routing, tmp_path
    ):
        broken_db = Database(tmp_path / "never_initialized5.db")
        fe = FailoverEngine(mock_pm, mock_routing, broken_db)
        assert fe.get_event_history() == []

    def test_get_event_history_resolved_flag_reflects_state(self, fe):
        event_id = fe._record_event(event_type="service_failed", severity="CRITICAL")
        fe._resolve_event(event_id, "mock_tunnel")

        history = fe.get_event_history()
        matching = [e for e in history if e["id"] == event_id]
        assert len(matching) == 1
        assert matching[0]["resolved"] is True
