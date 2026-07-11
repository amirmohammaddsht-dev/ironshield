"""
Level 1 (Mocked Integration) — ironshield.services.base.BaseService contract

Target: the CONCRETE (non-abstract) default methods `BaseService` provides
to every plugin — `validate_config`, `get_ufw_rules`, `get_metrics`,
`is_installed`, `get_latest_version`, and `update()`. TEST_MATRIX.md lists
"services/base.py::BaseService (contract)" as a P0 row, distinct from the
already-covered `calculate_score`/`_score_*` row (closed at Level 0 per
LEVEL0_REPORT.md). TEST_EXECUTION_ORDER.md's Level 1 scope explicitly
includes "Contract test تمام ۷ پلاگین از طریق BaseService".

Existing coverage (NOT duplicated here):
- `tests/unit/test_plugin_system.py::TestBaseService` — meta, lifecycle
  (start/stop/restart), health_check, get_logs, supports_role,
  benchmark-not-supported default, repr, install/uninstall,
  apply_config — all via a synthetic `MockPlugin`.
- `tests/unit/test_plugins_phase4.py` — deep behavioral tests for the 3
  non-stub real plugins (openvpn, gost, phormal): meta fields, config
  writing, status parsing, benchmark, metrics.

Confirmed gaps addressed here (by direct inspection of
`ironshield/services/base.py`, none of these methods/branches are called
anywhere in the existing test suite — verified via
`grep -rn "validate_config\\|get_ufw_rules\\|get_metrics\\|is_installed\\|get_latest_version\\|\\.update()" tests/`
before writing this file):

1. `validate_config()` default (always returns `Result.ok`) — untested.
2. `get_ufw_rules()` default (returns `self.meta.ufw_ports` verbatim) —
   untested.
3. `get_metrics()` default (dict of plugin/status/version) — untested.
4. `is_installed()` — only the "running" family status is exercised
   elsewhere (via `is_running`); the NOT_INSTALLED boundary itself is
   untested.
5. `get_latest_version()` default (`None`) — untested.
6. `update()` default implementation — ENTIRELY untested anywhere in the
   suite (confirmed via the same grep). This is the most consequential
   default method: it builds a filesystem path from
   `self.meta.category.value` and runs a shell script via
   `ironshield.utils.system.run_command`.

CONFIRMED FINDING (new, discovered while writing these tests — documented,
NOT fixed, per phase rules):

`BaseService.update()` builds the update-script path as:
    plugins/<category.value>/<plugin_name>/update.sh
using the plugin's `PluginCategory` enum *value* as the directory segment
(e.g. "tunnel_reliable", "tunnel_fast"). The REAL on-disk plugin directory
layout is `plugins/tunnels/<name>/...` and `plugins/vpn/<name>/...` — the
literal words "tunnels"/"vpn", not category enum values. Cross-referencing
every real plugin's declared `category` against the actual directory tree
(both re-verified this session):

    plugin      | declared category   | category.value     | real dir
    ------------|----------------------|---------------------|--------------------------
    openvpn     | VPN                  | "vpn"               | plugins/vpn/openvpn        (coincidental match)
    gost        | TUNNEL_RELIABLE      | "tunnel_reliable"   | plugins/tunnels/gost       (MISMATCH)
    frp         | TUNNEL_RELIABLE      | "tunnel_reliable"   | plugins/tunnels/frp        (MISMATCH)
    backhaul    | TUNNEL_RELIABLE      | "tunnel_reliable"   | plugins/tunnels/backhaul   (MISMATCH)
    storm_dns   | TUNNEL_RELIABLE      | "tunnel_reliable"   | plugins/tunnels/storm_dns  (MISMATCH)
    vxlan       | TUNNEL_RELIABLE      | "tunnel_reliable"   | plugins/tunnels/vxlan      (MISMATCH)
    phormal     | TUNNEL_FAST          | "tunnel_fast"       | plugins/tunnels/phormal    (overrides update() itself — unaffected)

Of the 7 real plugins, only `phormal` overrides `update()` with its own
implementation (confirmed via `grep -n "def update" plugins/*/*/service.py`
across all 7 — see report). The other 6 all rely on the inherited
default. Of those 6, only `openvpn` happens to resolve to the correct path
by coincidence (its category value "vpn" matches the real directory
segment "vpn"). The remaining 5 (gost, frp, backhaul, storm_dns, vxlan)
would call `run_command` with a path that never exists on disk, and
`update()` would unconditionally return
`Result.fail("Update script not found: ...")` for every one of them if
invoked in production today. This is reproduced below with the REAL
plugin classes (not a synthetic mock), loaded from their actual
`service.py` files, with NO filesystem mocking — the path-mismatch is
real, not simulated.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from ironshield.services.base import (
    BaseService,
    HealthResult,
    PluginCategory,
    PluginMeta,
    Result,
    ServerRole,
    ServiceStatus,
)

REPO_ROOT = Path(__file__).parent.parent.parent


def load_plugin(rel_path: str, module_key: str):
    """Dynamically load a plugin service.py file (same helper pattern as
    tests/unit/test_plugins_phase4.py::load_plugin, duplicated locally to
    keep this file self-contained and independently runnable)."""
    full_path = REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(module_key, full_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    spec.loader.exec_module(module)
    return module


class _ContractPlugin(BaseService):
    """Minimal concrete BaseService subclass — used only to exercise the
    DEFAULT (non-overridden) methods under test. Distinct from
    test_plugin_system.py's MockPlugin so this file has no cross-file
    fixture dependency; kept intentionally tiny."""

    def __init__(self, *args, ufw_ports=None, category=PluginCategory.TUNNEL_RELIABLE, name="contract_plugin", **kwargs):
        self._ufw_ports = ufw_ports or []
        self._category = category
        self._name = name
        self._status = ServiceStatus.RUNNING
        self.stop_should_fail = False
        super().__init__(*args, **kwargs)

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta(
            name=self._name,
            display_name="Contract Plugin",
            version="9.9.9",
            author="Test",
            source_url="https://example.com",
            license="MIT",
            roles=[ServerRole.BOTH],
            category=self._category,
            priority=1,
            ufw_ports=self._ufw_ports,
        )

    def install(self) -> Result:
        return Result.ok("Installed")

    def uninstall(self) -> Result:
        return Result.ok("Uninstalled")

    def start(self) -> Result:
        return Result.ok("Started")

    def stop(self) -> Result:
        if self.stop_should_fail:
            return Result.fail("simulated stop failure")
        return Result.ok("Stopped")

    def status(self) -> ServiceStatus:
        return self._status

    def health_check(self) -> HealthResult:
        return HealthResult(healthy=True, status=self._status)

    def get_config(self) -> Dict[str, Any]:
        return {}

    def apply_config(self, config: Dict[str, Any]) -> Result:
        return Result.ok("Applied")

    def get_logs(self, lines: int = 100) -> List[str]:
        return []


@pytest.fixture
def plugin():
    return _ContractPlugin(server_role=ServerRole.IRAN, config={})


# ── validate_config default ─────────────────────────────────────


class TestValidateConfigDefault:
    def test_default_validate_config_is_always_ok(self, plugin):
        result = plugin.validate_config({"anything": "goes", "even": ["nested", "junk"]})
        assert result.success is True

    def test_default_validate_config_ok_even_for_empty_dict(self, plugin):
        result = plugin.validate_config({})
        assert result.success is True


# ── get_ufw_rules default ───────────────────────────────────────


class TestGetUfwRulesDefault:
    def test_returns_meta_ufw_ports_verbatim(self):
        ports = [{"port": 443, "protocol": "tcp", "from_ip": None}]
        plugin = _ContractPlugin(server_role=ServerRole.IRAN, config={}, ufw_ports=ports)
        assert plugin.get_ufw_rules() == ports

    def test_returns_empty_list_when_meta_has_no_ports(self, plugin):
        assert plugin.get_ufw_rules() == []


# ── get_metrics default ─────────────────────────────────────────


class TestGetMetricsDefault:
    def test_default_metrics_shape(self, plugin):
        metrics = plugin.get_metrics()
        assert metrics == {
            "plugin": "contract_plugin",
            "status": "RUNNING",
            "version": "9.9.9",
        }


# ── is_installed / get_latest_version defaults ──────────────────


class TestIsInstalledDefault:
    def test_is_installed_true_when_status_is_running(self, plugin):
        assert plugin.is_installed() is True

    @pytest.mark.parametrize(
        "status",
        [
            ServiceStatus.STOPPED,
            ServiceStatus.FAILED,
            ServiceStatus.DEGRADED,
            ServiceStatus.INSTALLING,
            ServiceStatus.DISABLED,
            ServiceStatus.UNKNOWN,
        ],
    )
    def test_is_installed_true_for_any_status_other_than_not_installed(self, plugin, status):
        """is_installed() only excludes the single NOT_INSTALLED value —
        every other status (even FAILED/DISABLED) counts as 'installed'.
        This is the exact, slightly counter-intuitive contract as written;
        documented here, not altered."""
        plugin._status = status
        assert plugin.is_installed() is True

    def test_is_installed_false_when_status_is_not_installed(self, plugin):
        plugin._status = ServiceStatus.NOT_INSTALLED
        assert plugin.is_installed() is False


class TestGetLatestVersionDefault:
    def test_default_returns_none(self, plugin):
        assert plugin.get_latest_version() is None


# ── restart() default: warning-on-stop-failure branch ───────────


class TestRestartDefaultWarningBranch:
    """restart()'s default implementation (stop -> start) has an
    untested branch: when stop() itself fails, it logs a warning but
    still proceeds to call start() unconditionally. Existing coverage
    (test_plugin_system.py::test_restart_default_implementation) only
    exercises the stop-succeeds path."""

    def test_restart_still_starts_even_when_stop_fails(self, plugin):
        plugin.stop_should_fail = True
        result = plugin.restart()
        # Per the source: start() is called unconditionally regardless
        # of stop()'s outcome — documented here, not changed.
        assert result.success is True

    def test_restart_logs_warning_when_stop_fails(self, plugin):
        """IronShield's get_logger() sets `propagate = False`
        (ironshield/utils/logger.py:104). pytest's caplog fixture only
        attaches its capturing handler to the root logger; `at_level`'s
        `logger=` argument merely adjusts levels, it does not attach the
        handler to that logger (confirmed via `inspect.getsource` on
        pytest's own `LogCaptureFixture.at_level` this session). Since
        propagate=False stops records from ever reaching root, caplog
        cannot observe this logger under any configuration — a genuine
        pytest/IronShield-logger interaction gap, not a caplog bug. A
        handler is attached directly to the named logger instead."""
        import logging

        records = []

        class _CollectingHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        target_logger = logging.getLogger("ironshield.plugin.contract_plugin")
        handler = _CollectingHandler(level=logging.WARNING)
        target_logger.addHandler(handler)
        try:
            plugin.stop_should_fail = True
            plugin.restart()
        finally:
            target_logger.removeHandler(handler)

        warning_records = [r for r in records if r.levelno == logging.WARNING]
        assert any("Stop failed during restart" in r.getMessage() for r in warning_records)


# ── update() default implementation ─────────────────────────────


class TestUpdateDefaultImplementation:
    def test_returns_fail_when_script_does_not_exist(self, plugin):
        """No _plugin_dir is injected on the `plugin` fixture (it isn't
        constructed via PluginManager), so update() falls back to
        Option C (inspect.getfile(type(self)).parent) — which, for the
        synthetic _ContractPlugin class, resolves to this test file's
        own directory (tests/unit/), which has no update.sh. Confirms
        the fallback path still reports a clean failure, not a raise,
        when no script is found by either mechanism."""
        with patch("ironshield.utils.system.run_command") as mock_run:
            result = plugin.update()
        assert result.success is False
        assert "Update script not found" in result.error
        mock_run.assert_not_called()

    def test_runs_script_and_succeeds_when_present(self, tmp_path, plugin):
        """PR-3 / F-004 FIX: update.sh resolution no longer depends on
        `category`/`name` at all (see TestRealPluginUpdatePathMismatch's
        replacement below for why that mapping was removed entirely).
        To exercise the 'script found, success' branch for the
        synthetic _ContractPlugin, _plugin_dir is now set explicitly —
        this exercises the exact same injection mechanism
        PluginManager uses in production."""
        (tmp_path / "update.sh").write_text("#!/bin/bash\necho ok\n")
        plugin._plugin_dir = tmp_path

        with patch(
            "ironshield.utils.system.run_command",
            return_value=(0, "updated ok", ""),
        ) as mock_run:
            result = plugin.update()
        assert result.success is True
        mock_run.assert_called_once()

    def test_returns_fail_when_script_exits_nonzero(self, tmp_path, plugin):
        (tmp_path / "update.sh").write_text("#!/bin/bash\nexit 1\n")
        plugin._plugin_dir = tmp_path

        with patch(
            "ironshield.utils.system.run_command",
            return_value=(1, "", "permission denied"),
        ):
            result = plugin.update()
        assert result.success is False
        assert "permission denied" in result.error


# ── PR-3 / F-004 FIX: update() path resolution ──────────────────
# ── (Option F: PluginManager-injected _plugin_dir, with          ──
# ── inspect.getfile() fallback for direct construction)          ──


class TestRealPluginUpdatePathFixed:
    """PR-3 / F-004 FIX regression tests. This class previously
    reproduced the CONFIRMED, unfixed finding (LEVEL1_FINDINGS_BACKLOG.md
    P1-1): update()'s path construction used
    `self.meta.category.value` as a directory segment, which does not
    match the real on-disk layout (`plugins/tunnels/<name>`,
    `plugins/vpn/<name>`) for 5 of 7 plugins. The fix removes any
    dependency on `category`/`name` from path resolution entirely —
    these tests are inverted to confirm the 5 previously-broken
    plugins now succeed, using the real plugin classes loaded from
    their actual service.py files (no synthetic plugin, no
    _plugin_dir injection — this exercises the Option C fallback,
    exactly as any direct construction outside PluginManager would)."""

    @pytest.mark.parametrize(
        "rel_path,module_key,class_name,ctor_kwargs",
        [
            ("plugins/tunnels/gost/service.py", "gost_svc_contract_f004", "GOSTService",
             {"local_port": 8080, "remote_host": "1.2.3.4", "remote_port": 8080}),
            ("plugins/tunnels/frp/service.py", "frp_svc_contract_f004", "FrpService", {}),
            ("plugins/tunnels/backhaul/service.py", "backhaul_svc_contract_f004", "BackhaulService", {}),
            ("plugins/tunnels/storm_dns/service.py", "storm_dns_svc_contract_f004", "StormDnsService", {}),
            ("plugins/tunnels/vxlan/service.py", "vxlan_svc_contract_f004", "VxlanService", {}),
        ],
        ids=["gost", "frp", "backhaul", "storm_dns", "vxlan"],
    )
    def test_previously_broken_plugin_update_succeeds_via_fallback_regression_F_004(
        self, rel_path, module_key, class_name, ctor_kwargs
    ):
        module = load_plugin(rel_path, module_key)
        cls = getattr(module, class_name)
        svc = cls(server_role=ServerRole.IRAN, config=ctor_kwargs)

        # No _plugin_dir injected (direct construction, same as
        # PluginManager would never do here) — exercises the
        # inspect.getfile() fallback. Sanity precondition: confirm the
        # real update.sh this fallback must find actually exists.
        real_script = REPO_ROOT / rel_path.rsplit("/", 1)[0] / "update.sh"
        assert real_script.exists(), f"expected the real update.sh at {real_script} to exist"

        with patch(
            "ironshield.utils.system.run_command",
            return_value=(0, "updated ok", ""),
        ) as mock_run:
            result = svc.update()

        assert result.success is True
        mock_run.assert_called_once_with(f"bash {real_script}", timeout=120)

    def test_openvpn_update_still_succeeds_via_fallback_regression_F_004(self):
        """openvpn worked before this fix too (by coincidence — its
        category value happened to equal its real directory segment).
        Confirms it still works after the fix, now for the correct
        reason (fallback introspection), not by coincidence."""
        module = load_plugin("plugins/vpn/openvpn/service.py", "openvpn_svc_contract_f004")
        svc = module.OpenVPNService(server_role=ServerRole.IRAN, config={})

        real_script = REPO_ROOT / "plugins/vpn/openvpn/update.sh"
        assert real_script.exists()

        with patch(
            "ironshield.utils.system.run_command",
            return_value=(0, "ok", ""),
        ) as mock_run:
            result = svc.update()

        assert result.success is True
        mock_run.assert_called_once_with(f"bash {real_script}", timeout=120)

    def test_phormal_overrides_update_and_is_unaffected(self):
        """phormal is the one plugin among the 7 that overrides update()
        itself, so it never touches BaseService's default path
        resolution (neither the old broken version nor this fix) —
        unchanged from before PR-3, included for the 7/7 contract
        picture."""
        module = load_plugin("plugins/tunnels/phormal/service.py", "phormal_svc_contract_f004")
        svc = module.PhormalService(server_role=ServerRole.IRAN, config={})
        assert BaseService.update is not type(svc).update


class TestPluginDirInjectionPrecedence:
    """PR-3 / F-004: explicit tests for the two-tier resolution
    strategy itself (Option F) — injected _plugin_dir takes precedence
    over introspection when present; introspection is used only as a
    fallback when absent."""

    def test_injected_plugin_dir_takes_precedence_over_introspection(self, tmp_path):
        """Load a REAL plugin (gost) whose introspected location is
        plugins/tunnels/gost/ (correct, per the fallback tests above),
        but explicitly inject a DIFFERENT _plugin_dir pointing at a
        fake update.sh in a tmp directory — exactly as PluginManager
        would do at discovery time. Confirms the injected path wins,
        not the introspected one, by asserting run_command is called
        with the tmp path's script, not gost's real one."""
        module = load_plugin(
            "plugins/tunnels/gost/service.py", "gost_svc_precedence_f004"
        )
        svc = module.GOSTService(
            server_role=ServerRole.IRAN,
            config={"local_port": 8080, "remote_host": "1.2.3.4", "remote_port": 8080},
        )

        fake_script = tmp_path / "update.sh"
        fake_script.write_text("#!/bin/bash\necho fake\n")
        svc._plugin_dir = tmp_path

        with patch(
            "ironshield.utils.system.run_command",
            return_value=(0, "ok", ""),
        ) as mock_run:
            result = svc.update()

        assert result.success is True
        mock_run.assert_called_once_with(f"bash {fake_script}", timeout=120)

    def test_fallback_to_introspection_when_plugin_dir_not_set(self, plugin):
        """Companion, explicit-intent version of
        TestUpdateDefaultImplementation::test_returns_fail_when_script_does_not_exist
        — confirms getattr(self, "_plugin_dir", None) is None on a
        plainly-constructed instance (no PluginManager involved), so
        resolution falls through to inspect.getfile()."""
        assert getattr(plugin, "_plugin_dir", None) is None

        with patch("ironshield.utils.system.run_command") as mock_run:
            result = plugin.update()

        # No update.sh sibling to this test file -> clean failure via
        # the fallback path, not an exception.
        assert result.success is False
        mock_run.assert_not_called()

    def test_plugin_manager_injects_plugin_dir_at_load_time(self, tmp_path):
        """Integration-shaped unit test for the PluginManager side of
        Option F: confirms _load_plugin actually sets instance._plugin_dir
        to the real discovered directory, using a minimal fake plugin
        on disk (not one of the 7 real plugins, to keep this isolated
        from their real service.py contents)."""
        import textwrap

        from ironshield.core.plugin_manager import PluginManager

        plugin_dir = tmp_path / "fake_plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.yaml").write_text(
            textwrap.dedent(
                """\
                name: fake_plugin
                display_name: Fake Plugin
                version: "1.0"
                author: test
                source: ""
                license: MIT
                roles: [both]
                category: tunnel_reliable
                """
            )
        )
        (plugin_dir / "install.sh").write_text("#!/bin/bash\n")
        (plugin_dir / "uninstall.sh").write_text("#!/bin/bash\n")
        (plugin_dir / "update.sh").write_text("#!/bin/bash\n")
        (plugin_dir / "service.py").write_text(
            textwrap.dedent(
                """\
                from ironshield.services.base import (
                    BaseService, PluginMeta, PluginCategory, ServerRole,
                    ServiceStatus, HealthResult, Result,
                )


                class FakePluginService(BaseService):
                    @property
                    def meta(self):
                        return PluginMeta(
                            name="fake_plugin", display_name="Fake Plugin",
                            version="1.0", author="test", source_url="",
                            license="MIT", roles=[ServerRole.BOTH],
                            category=PluginCategory.TUNNEL_RELIABLE, priority=1,
                        )

                    def install(self): return Result.ok("installed")
                    def uninstall(self): return Result.ok("uninstalled")
                    def start(self): return Result.ok("started")
                    def stop(self): return Result.ok("stopped")
                    def status(self): return ServiceStatus.RUNNING
                    def health_check(self):
                        return HealthResult(healthy=True, status=ServiceStatus.RUNNING)
                    def get_config(self): return {}
                    def apply_config(self, config): return Result.ok("applied")
                    def get_logs(self, lines=100): return []
                """
            )
        )

        pm = PluginManager(server_role=ServerRole.IRAN, global_config={})
        import ironshield.core.plugin_manager as pm_module

        original_root = pm_module.PLUGINS_ROOT
        pm_module.PLUGINS_ROOT = tmp_path
        try:
            loaded = pm.discover()
        finally:
            pm_module.PLUGINS_ROOT = original_root

        assert "fake_plugin" in loaded
        instance = pm.get("fake_plugin")
        assert getattr(instance, "_plugin_dir", None) == plugin_dir
