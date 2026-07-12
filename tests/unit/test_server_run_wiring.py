"""
Level 1 (Mocked Integration) — api/server.py

Target: the fire-and-forget `asyncio.create_task` pattern used by the
`_on_service_failure` / `_on_system_alert` closures defined locally inside
`ironshield.api.server._run()` (see KNOWLEDGE_PACK.md §3/§10 and
KNOWLEDGE_GRAPH.md "Class Relationship Graph").

Why this file exists (coverage gap, confirmed by direct inspection):
- `_on_service_failure` and `_on_system_alert` are LOCAL closures, not module-
  level functions or class methods. They cannot be imported directly; the
  only way to obtain a reference to the real, production closures is to run
  `_run()` itself and intercept the object it hands to `HealthCheckEngine`.
- `tests/unit/test_api.py` (the only existing test file that touches
  `api/server.py`) never calls `_run()` and has no reference to these
  closures anywhere. This is a genuine, not merely nominal, coverage gap.

Testing strategy:
- Every class `_run()` imports/instantiates (ConfigEngine, Database,
  PluginManager, ServiceManager, TunnelManager, SmartRoutingEngine,
  FailoverEngine, BenchmarkEngine, MonitoringEngine, HealthCheckEngine,
  APIHandlers) plus the module-level `APIServer` is patched at its *origin*
  module (not at `ironshield.api.server`), because `_run()` performs the
  `from X import Y` imports itself, at call time, inside the function body.
  Patching the origin attribute is what a `from-import` picks up.
- `_run()` is started as a real `asyncio.Task`. It is allowed to execute far
  enough to construct `HealthCheckEngine` (which is where the two real
  closures get handed over) and is then cancelled — we do not need it to
  reach the `stop_event.wait()` steady state or the shutdown path for this
  investigation.
- The captured objects are the *actual* closures defined inside the *actual*
  `_run()` function body — not re-implementations, not stand-ins. This
  means the tests exercise the real production fire-and-forget pattern.

Explicitly out of scope for this file (per phase rules — no production code
changes, no bug fixes):
- Whether the swallowed-exception behavior is desirable.
- Any fix (storing the Task, adding `add_done_callback`, wrapping in
  `asyncio.shield`/`gather`, etc.).
- The internals of `FailoverEngine.handle_service_failure` /
  `handle_system_alert` themselves (mocked here as opaque coroutines) — this
  file tests the *wiring pattern* in `_run()`, not `FailoverEngine`'s
  business logic.
"""

from __future__ import annotations

import asyncio
import gc
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ironshield.services.base import HealthResult, ServiceStatus


def _mock_class(async_methods=()):
    """Build a MagicMock 'class' whose instantiation returns a MagicMock
    instance, with the given method names pre-set to AsyncMock so that
    `asyncio.create_task(instance.method())` works like it would on the
    real (async) engine methods."""
    instance = MagicMock()
    for name in async_methods:
        setattr(instance, name, AsyncMock())
    cls = MagicMock(return_value=instance)
    return cls, instance


@asynccontextmanager
async def _running_wired_environment():
    """
    Patch every dependency `_run()` wires together, start `_run()` as a
    background task, wait until `HealthCheckEngine` has been constructed
    (this is where the real `_on_service_failure`/`_on_system_alert`
    closures are captured), then cancel `_run()`.

    Yields a dict with:
      - 'on_service_failure' / 'on_system_alert': the real closures
      - 'failover': the mocked FailoverEngine instance the closures call
      - 'run_task': the (already-cancelled) `_run()` task
    """
    cfg_cls, cfg_inst = _mock_class()
    cfg_inst.get.side_effect = lambda key, default=None: {
        "ironshield.role": "iran",
        "server.foreign.ip": "1.2.3.4",
    }.get(key, default)
    cfg_inst.get_all.return_value = {}

    db_cls, db_inst = _mock_class()
    pm_cls, pm_inst = _mock_class()
    sm_cls, sm_inst = _mock_class()
    tm_cls, tm_inst = _mock_class(async_methods=["start_monitoring"])
    routing_cls, routing_inst = _mock_class()
    failover_cls, failover_inst = _mock_class(
        async_methods=["handle_service_failure", "handle_system_alert"]
    )
    be_cls, be_inst = _mock_class(async_methods=["start"])
    monitoring_cls, monitoring_inst = _mock_class(async_methods=["start"])
    health_cls, health_inst = _mock_class(async_methods=["start"])
    handlers_cls, handlers_inst = _mock_class()
    apiserver_cls, apiserver_inst = _mock_class(async_methods=["start", "stop"])

    captured: dict = {}

    def health_side_effect(**kwargs):
        captured.update(kwargs)
        return health_inst

    health_cls.side_effect = health_side_effect

    patches = [
        patch("ironshield.core.config_engine.ConfigEngine", cfg_cls),
        patch("ironshield.db.database.Database", db_cls),
        patch("ironshield.core.plugin_manager.PluginManager", pm_cls),
        patch("ironshield.core.service_manager.ServiceManager", sm_cls),
        patch("ironshield.core.tunnel_manager.TunnelManager", tm_cls),
        patch("ironshield.core.smart_routing.SmartRoutingEngine", routing_cls),
        patch("ironshield.core.failover_engine.FailoverEngine", failover_cls),
        patch("ironshield.core.benchmark_engine.BenchmarkEngine", be_cls),
        patch("ironshield.core.monitoring.MonitoringEngine", monitoring_cls),
        patch("ironshield.core.health_check.HealthCheckEngine", health_cls),
        patch("ironshield.api.handlers.APIHandlers", handlers_cls),
        patch("ironshield.api.server.APIServer", apiserver_cls),
    ]
    for p in patches:
        p.start()

    run_task = None
    try:
        from ironshield.api.server import _run

        run_task = asyncio.create_task(_run())

        # Deterministically wait for HealthCheckEngine to be constructed
        # rather than sleeping a fixed wall-clock amount: yield control to
        # the loop repeatedly until the side_effect above has fired, or the
        # task ends early (which would itself be a test failure below).
        for _ in range(200):
            if captured or run_task.done():
                break
            await asyncio.sleep(0)

        if run_task.done() and not captured:
            # Surface the real exception instead of a confusing timeout.
            exc = run_task.exception()
            if exc is not None:
                raise exc
            raise AssertionError(
                "_run() completed without ever constructing HealthCheckEngine"
            )

        assert captured, "HealthCheckEngine was never constructed by _run()"

        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass

        yield {
            "on_service_failure": captured["on_service_failure"],
            "on_system_alert": captured["on_system_alert"],
            "failover": failover_inst,
            "run_task": run_task,
        }
    finally:
        for p in patches:
            p.stop()


def _make_health_result() -> HealthResult:
    return HealthResult(
        healthy=False, status=ServiceStatus.FAILED, message="down", error="down"
    )


class TestRunWiresRealClosures:
    """Sanity checks that we are testing the real production objects, not
    stand-ins, before making any claims about their behavior."""

    async def test_health_check_engine_receives_real_callables(self):
        async with _running_wired_environment() as env:
            assert callable(env["on_service_failure"])
            assert callable(env["on_system_alert"])
            # These must be the actual closures from _run(), not bound
            # methods of some other object — confirmed by qualname.
            assert env["on_service_failure"].__qualname__.endswith(
                "_run.<locals>._on_service_failure"
            )
            assert env["on_system_alert"].__qualname__.endswith(
                "_run.<locals>._on_system_alert"
            )


class TestFireAndForgetSilentFailure:
    """Core investigation: can exceptions raised inside the awaited
    FailoverEngine coroutines be silently lost when triggered through the
    _on_service_failure / _on_system_alert closures?"""

    async def test_on_service_failure_swallows_exception_synchronously(self):
        """The closure itself must return normally (None) even though the
        coroutine it schedules will raise — proving the closure cannot
        propagate the failure to its caller (HealthCheckEngine)."""
        async with _running_wired_environment() as env:
            env["failover"].handle_service_failure = AsyncMock(
                side_effect=RuntimeError("db write failed")
            )

            result = env["on_service_failure"](
                service_name="openvpn",
                health=_make_health_result(),
                consecutive_failures=3,
            )

            # No exception raised here — this is the fire-and-forget symptom.
            assert result is None

    async def test_on_service_failure_exception_is_never_retrieved_by_production_code(
        self,
    ):
        """Prove the exception is not merely delayed but genuinely
        unobserved by any production code path: the only way to see it in
        this test is to manually enumerate asyncio's live tasks, which
        `_run()` itself never does (it discards the Task returned by
        `asyncio.create_task`)."""
        async with _running_wired_environment() as env:
            env["failover"].handle_service_failure = AsyncMock(
                side_effect=RuntimeError("db write failed")
            )

            tasks_before = asyncio.all_tasks()
            env["on_service_failure"](
                service_name="openvpn",
                health=_make_health_result(),
                consecutive_failures=3,
            )
            new_tasks = asyncio.all_tasks() - tasks_before
            assert len(new_tasks) == 1, (
                "expected exactly one Task to be created by "
                "asyncio.create_task inside the closure"
            )
            created_task = new_tasks.pop()

            # Let the loop actually run the scheduled coroutine.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert created_task.done()
            # The exception exists on the Task object...
            assert isinstance(created_task.exception(), RuntimeError)
            # ...but nothing in `_run()` ever calls `.exception()`,
            # `.result()`, or `add_done_callback()` on it — the reference
            # returned by `asyncio.create_task()` in the closure is discarded
            # immediately (see api/server.py:314-316). We only found it here
            # because the test manually diffed `asyncio.all_tasks()`.

    async def test_on_system_alert_swallows_exception_synchronously(self):
        async with _running_wired_environment() as env:
            env["failover"].handle_system_alert = AsyncMock(
                side_effect=ValueError("notify failed")
            )

            result = env["on_system_alert"](resource="CPU", value=99.2, level="CRITICAL")

            assert result is None

    async def test_on_system_alert_exception_is_never_retrieved_by_production_code(
        self,
    ):
        async with _running_wired_environment() as env:
            env["failover"].handle_system_alert = AsyncMock(
                side_effect=ValueError("notify failed")
            )

            tasks_before = asyncio.all_tasks()
            env["on_system_alert"](resource="Disk", value=97.0, level="CRITICAL")
            new_tasks = asyncio.all_tasks() - tasks_before
            assert len(new_tasks) == 1
            created_task = new_tasks.pop()

            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert created_task.done()
            assert isinstance(created_task.exception(), ValueError)

    async def test_unretrieved_exception_only_surfaces_via_asyncio_default_handler(
        self,
    ):
        """Demonstrate precisely WHERE the exception goes if nobody holds a
        reference to the Task at all (the real production situation, since
        `_run()` discards the return value of `asyncio.create_task`):
        it reaches the event loop's exception handler with the exact message
        "Task exception was never retrieved" — never IronShield's own
        structured JSON logger (`ironshield.utils.logger.get_logger`), and
        never at the call site where the failure actually happened
        (`api/server.py:314-316`).

        Note on timing: because nothing anywhere holds a reference to the
        Task (matching production exactly — the closure discards the
        return value of `asyncio.create_task`), CPython's reference
        counting can reclaim it as soon as the loop finishes processing it,
        sometimes even without an explicit `gc.collect()`. This test does
        not assert *when* the handler fires (that is a CPython
        implementation detail and exactly what makes the pattern "silent"
        and non-deterministic in practice) — only that it fires, with this
        message, instead of any IronShield-level error handling ever
        running for this failure."""
        async with _running_wired_environment() as env:
            env["failover"].handle_service_failure = AsyncMock(
                side_effect=RuntimeError("db write failed")
            )

            loop = asyncio.get_running_loop()
            handler_calls = []
            original_handler = loop.get_exception_handler()
            loop.set_exception_handler(lambda lp, ctx: handler_calls.append(ctx))
            try:
                # Call exactly as HealthCheckEngine does: discard the
                # return value, matching api/server.py:314-316 verbatim.
                env["on_service_failure"](
                    service_name="openvpn",
                    health=_make_health_result(),
                    consecutive_failures=3,
                )

                # Give the task a chance to run/raise, and give the
                # collector a chance to reclaim the now-unreferenced task.
                for _ in range(5):
                    await asyncio.sleep(0)
                gc.collect()
                for _ in range(5):
                    await asyncio.sleep(0)

                assert len(handler_calls) == 1
                assert handler_calls[0]["message"] == (
                    "Task exception was never retrieved"
                )
                assert isinstance(handler_calls[0]["exception"], RuntimeError)
            finally:
                loop.set_exception_handler(original_handler)

    async def test_default_exception_handler_bypasses_ironshield_logger(self, caplog):
        """Confirm the swallowed exception does not appear through any
        IronShield-named logger (e.g. 'api.server', 'failover_engine'),
        reinforcing that operators watching IronShield's own structured
        logs would see nothing for this failure."""
        async with _running_wired_environment() as env:
            env["failover"].handle_service_failure = AsyncMock(
                side_effect=RuntimeError("db write failed")
            )

            with caplog.at_level(logging.DEBUG):
                env["on_service_failure"](
                    service_name="openvpn",
                    health=_make_health_result(),
                    consecutive_failures=3,
                )
                await asyncio.sleep(0)
                gc.collect()
                await asyncio.sleep(0)

            ironshield_records = [
                r for r in caplog.records if r.name.startswith("ironshield")
                or r.name in ("api.server", "failover_engine", "health_check")
            ]
            assert ironshield_records == [], (
                "no IronShield-namespaced logger should have recorded "
                "this exception — it is only visible via asyncio's own "
                "'asyncio' logger / default exception handler"
            )


class TestFireAndForgetSuccessPath:
    """Confirm the closures forward arguments correctly on the (more common)
    success path, so the failure-path tests above are read as testing an
    edge case, not the only path through this code."""

    async def test_on_service_failure_forwards_arguments(self):
        async with _running_wired_environment() as env:
            health = _make_health_result()
            env["on_service_failure"](
                service_name="gost", health=health, consecutive_failures=5
            )
            await asyncio.sleep(0)

            env["failover"].handle_service_failure.assert_awaited_once_with(
                "gost", health, 5
            )

    async def test_on_system_alert_forwards_arguments(self):
        async with _running_wired_environment() as env:
            env["on_system_alert"](resource="RAM", value=88.5, level="WARNING")
            await asyncio.sleep(0)

            # api/server.py:319 calls handle_system_alert positionally
            # (unlike handle_service_failure, which uses keyword args at
            # line 314-316) — asserted here exactly as written in production.
            env["failover"].handle_system_alert.assert_awaited_once_with(
                "RAM", 88.5, "WARNING"
            )


# ── PR-4 / F-005: SmartRouting DB config source wiring ──────────
#
# _load_routing_config(db) is tested directly against a real, temporary
# SQLite Database (not the mocked-class environment used above) because
# it specifically exercises Database.get_setting()'s real type-casting
# and fallback behavior — mocking Database here would defeat the point
# of these tests.


@pytest.fixture
def routing_config_db(tmp_path):
    from ironshield.db.database import Database

    db = Database(tmp_path / "routing_config_test.db")
    db.init()
    yield db
    db.close()


class TestLoadRoutingConfig:
    def test_routing_config_loaded_from_db_settings_regression_F_005(
        self, routing_config_db
    ):
        """Database Setting values (seeded by _seed_default_settings,
        then explicitly overridden here via set_setting — matching how
        an operator would customize them) are loaded and reach the
        constructed RoutingConfig's corresponding fields."""
        from ironshield.api.server import _load_routing_config

        routing_config_db.set_setting("routing.mode", "manual")
        routing_config_db.set_setting("routing.cooldown_minutes", 25)
        routing_config_db.set_setting("routing.min_score_diff", 15.5)
        routing_config_db.set_setting("routing.consecutive_failures", 7)

        config = _load_routing_config(routing_config_db)

        assert config.mode == "manual"
        assert config.cooldown_sec == 25 * 60
        assert config.min_score_diff == 15.5
        assert config.consecutive_failures == 7

    def test_malformed_routing_setting_falls_back_to_default_regression_F_005(
        self, routing_config_db
    ):
        """A malformed DB value (wrong type after casting — e.g. a
        cooldown_minutes row whose value_type says 'int' but whose
        stored value doesn't parse as one, so Database._cast_value
        returns the raw string unchanged) must not crash startup, must
        never reach RoutingConfig with the wrong type, must fall back
        to RoutingConfig's own code default, and must log a warning."""
        import logging

        from ironshield.api.server import _load_routing_config
        from ironshield.core.smart_routing import RoutingConfig
        from ironshield.db.models import Setting

        with routing_config_db.session() as s:
            setting = s.get(Setting, "routing.cooldown_minutes")
            setting.value = "not_a_number"  # value_type stays "int"

        default_config = RoutingConfig()

        records = []

        class _CollectingHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        target_logger = logging.getLogger("ironshield.api.server")
        handler = _CollectingHandler(level=logging.WARNING)
        target_logger.addHandler(handler)
        try:
            config = _load_routing_config(routing_config_db)  # must not raise
        finally:
            target_logger.removeHandler(handler)

        # Wrong type never reached RoutingConfig — falls back to the
        # exact code default, not a string, not a crash.
        assert config.cooldown_sec == default_config.cooldown_sec
        assert isinstance(config.cooldown_sec, int)

        warning_records = [r for r in records if r.levelno == logging.WARNING]
        assert any("routing.cooldown_minutes" in r.getMessage() for r in warning_records)

    def test_stability_bonus_remains_default_regression_F_005(
        self, routing_config_db
    ):
        """F-005 scope boundary: routing.stability_bonus is not seeded
        by _seed_default_settings and is intentionally not read by
        _load_routing_config. Even if a Setting row for it is added
        directly (simulating a future/manual DB edit),
        _load_routing_config must not pick it up — stability_bonus
        must always come from RoutingConfig's code default. This locks
        in the scope boundary so a future change can't silently expand
        it without this test failing first."""
        from ironshield.api.server import _load_routing_config
        from ironshield.core.smart_routing import RoutingConfig
        from ironshield.db.models import Setting

        with routing_config_db.session() as s:
            s.add(
                Setting(
                    key="routing.stability_bonus",
                    value="999",
                    value_type="float",
                    description="manually added, should be ignored by F-005",
                )
            )

        config = _load_routing_config(routing_config_db)
        default_config = RoutingConfig()

        assert config.stability_bonus == default_config.stability_bonus
        assert config.stability_bonus != 999
