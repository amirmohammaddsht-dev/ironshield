"""
Tests for Phase 11 — GitHub Actions Workflows.
Validates: YAML syntax correctness, required jobs/steps presence,
           trigger configuration, and security settings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).parent.parent.parent / ".github" / "workflows"


def _load_workflow(name: str) -> Dict:
    """Load and parse a workflow YAML file."""
    path = WORKFLOWS_DIR / name
    with open(path) as f:
        return yaml.safe_load(f)


def _all_workflow_files() -> List[Path]:
    """Find all workflow YAML files."""
    if not WORKFLOWS_DIR.exists():
        return []
    return sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml"))


# ── Structure Tests ────────────────────────────


class TestWorkflowStructure:
    def test_workflows_directory_exists(self):
        assert WORKFLOWS_DIR.exists()

    def test_ci_workflow_exists(self):
        assert (WORKFLOWS_DIR / "ci.yml").exists()

    def test_security_workflow_exists(self):
        assert (WORKFLOWS_DIR / "security.yml").exists()

    def test_plugin_check_workflow_exists(self):
        assert (WORKFLOWS_DIR / "plugin_check.yml").exists()

    def test_release_workflow_exists(self):
        assert (WORKFLOWS_DIR / "release.yml").exists()

    def test_docs_workflow_exists(self):
        assert (WORKFLOWS_DIR / "docs.yml").exists()

    def test_at_least_five_workflows(self):
        files = _all_workflow_files()
        assert len(files) >= 5


# ── YAML Syntax Tests ───────────────────────────


class TestWorkflowSyntax:
    """Every workflow file must be valid YAML."""

    @pytest.mark.parametrize("workflow_path", _all_workflow_files(), ids=lambda p: p.name)
    def test_valid_yaml(self, workflow_path: Path):
        try:
            with open(workflow_path) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            pytest.fail(f"Invalid YAML in {workflow_path.name}: {e}")

        assert data is not None
        assert isinstance(data, dict)

    @pytest.mark.parametrize("workflow_path", _all_workflow_files(), ids=lambda p: p.name)
    def test_has_name(self, workflow_path: Path):
        with open(workflow_path) as f:
            data = yaml.safe_load(f)
        assert "name" in data, f"{workflow_path.name} missing 'name' field"

    @pytest.mark.parametrize("workflow_path", _all_workflow_files(), ids=lambda p: p.name)
    def test_has_jobs(self, workflow_path: Path):
        with open(workflow_path) as f:
            data = yaml.safe_load(f)
        assert "jobs" in data, f"{workflow_path.name} missing 'jobs' field"
        assert len(data["jobs"]) > 0

    @pytest.mark.parametrize("workflow_path", _all_workflow_files(), ids=lambda p: p.name)
    def test_has_trigger(self, workflow_path: Path):
        """YAML parses 'on' as boolean True — check raw content instead."""
        content = workflow_path.read_text()
        assert "on:" in content, f"{workflow_path.name} missing trigger ('on:')"


# ── CI Workflow Tests ────────────────────────────


class TestCIWorkflow:
    @pytest.fixture
    def workflow(self):
        return _load_workflow("ci.yml")

    def test_triggers_on_push_and_pr(self, workflow):
        content = (WORKFLOWS_DIR / "ci.yml").read_text()
        assert "push:" in content
        assert "pull_request:" in content

    def test_has_test_job(self, workflow):
        assert "test" in workflow["jobs"]

    def test_has_lint_job(self, workflow):
        assert "lint" in workflow["jobs"]

    def test_test_job_matrix_python(self, workflow):
        test_job = workflow["jobs"]["test"]
        matrix = test_job.get("strategy", {}).get("matrix", {})
        versions = matrix.get("python-version", [])
        assert "3.11" in versions
        assert "3.12" in versions

    def test_lint_job_checks_black(self, workflow):
        lint_job = workflow["jobs"]["lint"]
        steps_text = str(lint_job.get("steps", []))
        assert "black" in steps_text.lower()

    def test_lint_job_checks_flake8(self, workflow):
        lint_job = workflow["jobs"]["lint"]
        steps_text = str(lint_job.get("steps", []))
        assert "flake8" in steps_text.lower()

    def test_black_version_pinned(self, workflow):
        """Black version should be pinned to match local dev environment."""
        content = (WORKFLOWS_DIR / "ci.yml").read_text()
        assert "black==23.12.1" in content


# ── Security Workflow Tests ──────────────────────


class TestSecurityWorkflow:
    @pytest.fixture
    def workflow(self):
        return _load_workflow("security.yml")

    def test_runs_weekly(self):
        content = (WORKFLOWS_DIR / "security.yml").read_text()
        assert "cron:" in content

    def test_has_dependency_scan(self, workflow):
        assert "dependency_scan" in workflow["jobs"]

    def test_has_secret_scan(self, workflow):
        assert "secret_scan" in workflow["jobs"]

    def test_has_code_scan(self, workflow):
        assert "code_scan" in workflow["jobs"]

    def test_has_shellcheck_scan(self, workflow):
        assert "shellcheck_scan" in workflow["jobs"]

    def test_uses_trufflehog(self, workflow):
        content = str(workflow["jobs"]["secret_scan"])
        assert "trufflehog" in content.lower()

    def test_uses_bandit(self, workflow):
        content = str(workflow["jobs"]["code_scan"])
        assert "bandit" in content.lower()


# ── Plugin Check Workflow Tests ──────────────────


class TestPluginCheckWorkflow:
    @pytest.fixture
    def workflow(self):
        return _load_workflow("plugin_check.yml")

    def test_triggers_on_plugin_changes(self):
        content = (WORKFLOWS_DIR / "plugin_check.yml").read_text()
        assert "plugins/**" in content

    def test_has_validate_job(self, workflow):
        assert "validate_plugins" in workflow["jobs"]

    def test_has_update_check_job(self, workflow):
        assert "check_plugin_updates" in workflow["jobs"]

    def test_validates_required_fields(self):
        content = (WORKFLOWS_DIR / "plugin_check.yml").read_text()
        for field in ["name", "display_name", "version", "category", "priority"]:
            assert field in content

    def test_validates_required_files(self):
        content = (WORKFLOWS_DIR / "plugin_check.yml").read_text()
        for fname in ["install.sh", "uninstall.sh", "update.sh", "service.py"]:
            assert fname in content

    def test_runs_shellcheck_on_plugins(self, workflow):
        content = str(workflow["jobs"]["validate_plugins"])
        assert "shellcheck" in content.lower()

    def test_creates_github_issue_for_updates(self, workflow):
        content = str(workflow["jobs"]["check_plugin_updates"])
        assert "github-script" in content
        assert "createIssue" in content.replace(" ", "") or "issues.create" in content


# ── Release Workflow Tests ───────────────────────


class TestReleaseWorkflow:
    @pytest.fixture
    def workflow(self):
        return _load_workflow("release.yml")

    def test_triggers_on_version_tags(self):
        content = (WORKFLOWS_DIR / "release.yml").read_text()
        assert "v*.*.*" in content

    def test_has_write_permissions(self, workflow):
        assert workflow.get("permissions", {}).get("contents") == "write"

    def test_runs_tests_before_release(self, workflow):
        assert "test_before_release" in workflow["jobs"]

    def test_build_depends_on_tests(self, workflow):
        build_job = workflow["jobs"]["build_release"]
        assert build_job.get("needs") == "test_before_release"

    def test_computes_checksums(self, workflow):
        content = str(workflow["jobs"]["build_release"])
        assert "sha256sum" in content

    def test_creates_github_release(self, workflow):
        content = str(workflow["jobs"]["build_release"])
        assert "action-gh-release" in content

    def test_includes_install_script_in_release(self, workflow):
        content = str(workflow["jobs"]["build_release"])
        assert "install.sh" in content

    def test_updates_readme_after_release(self, workflow):
        assert "update_readme" in workflow["jobs"]


# ── Docs Workflow Tests ──────────────────────────


class TestDocsWorkflow:
    @pytest.fixture
    def workflow(self):
        return _load_workflow("docs.yml")

    def test_triggers_on_docs_changes(self):
        content = (WORKFLOWS_DIR / "docs.yml").read_text()
        assert "docs/**" in content

    def test_has_validate_job(self, workflow):
        assert "validate_docs" in workflow["jobs"]

    def test_checks_fa_en_parity(self, workflow):
        content = str(workflow["jobs"]["validate_docs"])
        assert "fa" in content.lower()
        assert "en" in content.lower()


# ── Documentation Files Tests ────────────────────


class TestDocumentationFiles:
    DOCS_ROOT = Path(__file__).parent.parent.parent / "docs"

    def test_fa_directory_exists(self):
        assert (self.DOCS_ROOT / "fa").exists()

    def test_en_directory_exists(self):
        assert (self.DOCS_ROOT / "en").exists()

    def test_en_install_doc_exists(self):
        assert (self.DOCS_ROOT / "en" / "install.md").exists()

    def test_en_configuration_doc_exists(self):
        assert (self.DOCS_ROOT / "en" / "configuration.md").exists()

    def test_en_troubleshooting_doc_exists(self):
        assert (self.DOCS_ROOT / "en" / "troubleshooting.md").exists()

    def test_fa_install_doc_exists(self):
        assert (self.DOCS_ROOT / "fa" / "install.md").exists()

    def test_fa_en_have_same_files(self):
        """FA and EN docs should have matching filenames."""
        fa_files = {f.name for f in (self.DOCS_ROOT / "fa").glob("*.md")}
        en_files = {f.name for f in (self.DOCS_ROOT / "en").glob("*.md")}
        assert fa_files == en_files

    def test_install_doc_mentions_curl_command(self):
        content = (self.DOCS_ROOT / "en" / "install.md").read_text()
        assert "curl -sSL" in content
        assert "install.sh" in content

    def test_install_doc_mentions_requirements(self):
        content = (self.DOCS_ROOT / "en" / "install.md").read_text()
        assert "Ubuntu" in content
        assert "RAM" in content
