"""
Tests for Phase 10 — Bash Scripts.
Validates: syntax correctness (bash -n), presence of required scripts,
           executable permissions, shebang lines, and basic structure.
No actual installation is performed — these are static validation tests.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List

import pytest

SCRIPTS_ROOT = Path(__file__).parent.parent.parent / "scripts"


def _bash_syntax_check(script_path: Path) -> tuple[bool, str]:
    """Run bash -n on a script to check syntax validity."""
    result = subprocess.run(
        ["bash", "-n", str(script_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.returncode == 0, result.stderr


def _all_scripts() -> List[Path]:
    """Find all .sh files in the scripts directory."""
    return sorted(SCRIPTS_ROOT.rglob("*.sh"))


# ── Structure Tests ────────────────────────────


class TestScriptStructure:
    def test_scripts_directory_exists(self):
        assert SCRIPTS_ROOT.exists()
        assert SCRIPTS_ROOT.is_dir()

    def test_main_install_script_exists(self):
        assert (SCRIPTS_ROOT / "install.sh").exists()

    def test_uninstall_script_exists(self):
        assert (SCRIPTS_ROOT / "uninstall.sh").exists()

    def test_update_script_exists(self):
        assert (SCRIPTS_ROOT / "update.sh").exists()

    def test_utils_scripts_exist(self):
        utils = SCRIPTS_ROOT / "utils"
        assert (utils / "check_deps.sh").exists()
        assert (utils / "backup.sh").exists()
        assert (utils / "restore.sh").exists()

    def test_all_plugin_install_scripts_exist(self):
        """Every plugin should have a corresponding install script."""
        services = SCRIPTS_ROOT / "services"
        expected = [
            "install_openvpn.sh",
            "install_phormal.sh",
            "install_gost.sh",
            "install_frp.sh",
            "install_backhaul.sh",
            "install_vxlan.sh",
            "install_storm_dns.sh",
        ]
        for script_name in expected:
            assert (services / script_name).exists(), f"Missing: {script_name}"

    def test_at_least_seven_scripts_found(self):
        """Should have at least 7 shell scripts total."""
        scripts = _all_scripts()
        assert len(scripts) >= 7


# ── Syntax Tests ────────────────────────────────


class TestScriptSyntax:
    """Every shell script must have valid bash syntax."""

    @pytest.mark.parametrize("script_path", _all_scripts(), ids=lambda p: p.name)
    def test_script_syntax_valid(self, script_path: Path):
        """bash -n should report no syntax errors."""
        valid, error = _bash_syntax_check(script_path)
        assert valid, f"Syntax error in {script_path.name}: {error}"


# ── Shebang Tests ───────────────────────────────


class TestScriptShebang:
    """Every script must start with a proper shebang line."""

    @pytest.mark.parametrize("script_path", _all_scripts(), ids=lambda p: p.name)
    def test_has_bash_shebang(self, script_path: Path):
        first_line = script_path.read_text().split("\n")[0]
        assert first_line.startswith("#!"), f"{script_path.name} missing shebang"
        assert "bash" in first_line, f"{script_path.name} shebang is not bash"


# ── Safety Tests ────────────────────────────────


class TestScriptSafety:
    """Scripts should follow safe bash practices."""

    @pytest.mark.parametrize("script_path", _all_scripts(), ids=lambda p: p.name)
    def test_has_set_euo_pipefail_or_set_uo(self, script_path: Path):
        """Scripts should set strict error handling (except check_deps which checks errors manually)."""
        content = script_path.read_text()
        has_strict = "set -euo pipefail" in content or "set -uo pipefail" in content
        assert has_strict, f"{script_path.name} missing 'set -euo pipefail'"


# ── Install Script Content Tests ────────────────


class TestInstallScript:
    """Tests for the main install.sh content and structure."""

    @pytest.fixture
    def content(self):
        return (SCRIPTS_ROOT / "install.sh").read_text()

    def test_checks_root(self, content):
        assert "check_root" in content
        assert "EUID" in content

    def test_checks_os(self, content):
        assert "check_os" in content
        assert "ubuntu" in content.lower()

    def test_checks_ram(self, content):
        assert "check_ram" in content

    def test_checks_disk(self, content):
        assert "check_disk" in content

    def test_creates_system_user(self, content):
        assert "create_user" in content
        assert "useradd" in content
        assert "ironshield" in content

    def test_sets_up_sudoers(self, content):
        assert "setup_sudoers" in content
        assert "/etc/sudoers.d/ironshield" in content

    def test_installs_python(self, content):
        assert "install_python" in content
        assert "python3.11" in content

    def test_downloads_repo(self, content):
        assert "download_repo" in content
        assert "git clone" in content or "git pull" in content

    def test_sets_up_venv(self, content):
        assert "setup_venv" in content
        assert "venv" in content

    def test_installs_systemd_services(self, content):
        assert "install_systemd_services" in content
        assert "systemctl daemon-reload" in content

    def test_initializes_ufw(self, content):
        assert "init_ufw" in content
        assert "ufw" in content
        assert "deny incoming" in content

    def test_launches_cli_installer(self, content):
        assert "launch_installer" in content
        assert "ironshield.cli.main" in content

    def test_calls_main_function(self, content):
        assert 'main "$@"' in content


# ── Uninstall Script Content Tests ──────────────


class TestUninstallScript:
    @pytest.fixture
    def content(self):
        return (SCRIPTS_ROOT / "uninstall.sh").read_text()

    def test_has_purge_flag(self, content):
        assert "--purge" in content

    def test_stops_services(self, content):
        assert "systemctl stop" in content
        assert "ironshield-core" in content

    def test_removes_systemd_units(self, content):
        assert "/etc/systemd/system/ironshield-*.service" in content

    def test_removes_sudoers(self, content):
        assert "/etc/sudoers.d/ironshield" in content

    def test_purge_removes_user_data(self, content):
        assert "userdel" in content
        assert "rm -rf" in content

    def test_confirms_before_purge(self, content):
        assert "confirm" in content.lower()


# ── Update Script Content Tests ─────────────────


class TestUpdateScript:
    @pytest.fixture
    def content(self):
        return (SCRIPTS_ROOT / "update.sh").read_text()

    def test_backs_up_config_before_update(self, content):
        assert "Backing up" in content or "backup" in content.lower()

    def test_pulls_latest_code(self, content):
        assert "git fetch" in content or "git pull" in content

    def test_updates_dependencies(self, content):
        assert "pip" in content and "install" in content

    def test_restarts_services(self, content):
        assert "systemctl start" in content


# ── Backup/Restore Script Tests ─────────────────


class TestBackupRestoreScripts:
    def test_backup_creates_tar_archive(self):
        content = (SCRIPTS_ROOT / "utils" / "backup.sh").read_text()
        assert "tar -czf" in content
        assert ".tar.gz" in content

    def test_backup_includes_critical_dirs(self):
        content = (SCRIPTS_ROOT / "utils" / "backup.sh").read_text()
        assert "configs" in content
        assert "db" in content
        assert "keys" in content

    def test_backup_limits_retention(self):
        content = (SCRIPTS_ROOT / "utils" / "backup.sh").read_text()
        # Should clean old backups
        assert "tail -n +11" in content or "keeping last" in content.lower()

    def test_restore_requires_argument(self):
        content = (SCRIPTS_ROOT / "utils" / "restore.sh").read_text()
        assert 'BACKUP_FILE="${1:-}"' in content

    def test_restore_confirms_before_overwrite(self):
        content = (SCRIPTS_ROOT / "utils" / "restore.sh").read_text()
        assert "confirm" in content.lower()

    def test_restore_creates_safety_backup(self):
        content = (SCRIPTS_ROOT / "utils" / "restore.sh").read_text()
        assert "pre_restore" in content or "safety" in content.lower()


# ── Plugin Install Scripts Content Tests ────────


class TestPluginInstallScripts:
    def test_openvpn_installs_easyrsa(self):
        content = (SCRIPTS_ROOT / "services" / "install_openvpn.sh").read_text()
        assert "openvpn" in content
        assert "easy-rsa" in content

    def test_phormal_downloads_from_github(self):
        content = (SCRIPTS_ROOT / "services" / "install_phormal.sh").read_text()
        assert "Schmi7zz/Phormal" in content

    def test_gost_detects_architecture(self):
        content = (SCRIPTS_ROOT / "services" / "install_gost.sh").read_text()
        assert "uname -m" in content
        assert "x86_64" in content
        assert "aarch64" in content

    def test_gost_downloads_from_github_api(self):
        content = (SCRIPTS_ROOT / "services" / "install_gost.sh").read_text()
        assert "api.github.com/repos/ginuerzh/gost" in content

    def test_frp_installs_both_binaries(self):
        content = (SCRIPTS_ROOT / "services" / "install_frp.sh").read_text()
        assert "frps" in content
        assert "frpc" in content

    def test_backhaul_downloads_from_github(self):
        content = (SCRIPTS_ROOT / "services" / "install_backhaul.sh").read_text()
        assert "Musixal/Backhaul" in content

    def test_vxlan_checks_kernel_module(self):
        content = (SCRIPTS_ROOT / "services" / "install_vxlan.sh").read_text()
        assert "modprobe vxlan" in content

    def test_storm_dns_warns_about_domain(self):
        content = (SCRIPTS_ROOT / "services" / "install_storm_dns.sh").read_text()
        assert "domain" in content.lower() or "NS record" in content


# ── Executable Permission Tests (Post-Set) ──────


class TestExecutablePermissions:
    """
    After chmod +x is applied (done during repo setup),
    scripts should be marked executable. This test validates
    the chmod was applied correctly in this environment.
    """

    @pytest.mark.parametrize("script_path", _all_scripts(), ids=lambda p: p.name)
    def test_script_is_executable(self, script_path: Path):
        import os

        is_executable = os.access(script_path, os.X_OK)
        assert is_executable, f"{script_path.name} is not executable"
