from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "fast_ci.py"
FAST_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
FULL_WORKFLOW = ROOT / ".github" / "workflows" / "ci-full.yml"


def _plan(*paths: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "classify-paths", *paths],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def test_public_key_only_change_selects_grid_and_safety_without_frontend() -> None:
    plan = _plan(
        "src/adaptive_trading_bot/research/grid_history_runner.py",
        "tests/research/test_grid_approval_enrollment.py",
        "docs/research/btc_eth_grid_4h_v1_owner_key_enrollment.md",
    )

    assert plan["python"] is True
    assert plan["frontend"] is False
    assert plan["pytest_mode"] == "targeted"
    targets = plan["pytest_targets"]
    assert isinstance(targets, list)
    assert "tests/research/test_grid_history_runner.py" in targets
    assert "tests/risk/test_risk_core.py" in targets


def test_grid_and_risk_changes_include_shared_authority_regressions() -> None:
    plan = _plan(
        "src/adaptive_trading_bot/research/grid_v1.py",
        "src/adaptive_trading_bot/risk/core.py",
    )

    assert plan["python"] is True
    assert plan["frontend"] is False
    targets = plan["pytest_targets"]
    assert isinstance(targets, list)
    assert "tests/research/test_grid_v1.py" in targets
    assert "tests/accounting/test_positions.py" in targets
    assert "tests/admission/test_portfolio.py" in targets


def test_issue_255_cumulative_diff_is_targeted_not_full() -> None:
    plan = _plan(
        ".github/workflows/ci-full.yml",
        ".github/workflows/ci.yml",
        "docs/ci_policy.md",
        "scripts/ci/fast_ci.py",
        "scripts/research/grid_history_offline.py",
        "src/adaptive_trading_bot/research/grid_history_offline.py",
        "src/adaptive_trading_bot/research/grid_v1.py",
        "tests/ci/test_fast_ci.py",
        "tests/research/test_grid_history_offline.py",
        "tests/research/test_grid_history_preflight.py",
        "tests/research/test_grid_v1.py",
    )

    assert plan["python"] is True
    assert plan["frontend"] is True  # Workflow changes still validate frontend CI wiring.
    assert plan["collect_all"] is True
    assert plan["pytest_mode"] == "targeted"
    reasons = plan["reasons"]
    assert isinstance(reasons, list)
    assert "unknown-fail-closed" not in reasons
    targets = plan["pytest_targets"]
    assert isinstance(targets, list)
    assert "tests/ci/test_fast_ci.py" in targets
    assert "tests/research/test_grid_history_offline.py" in targets
    assert "tests/research/test_grid_history_preflight.py" in targets
    assert "tests/research/test_grid_v1.py" in targets
    assert "tests/accounting/test_positions.py" in targets
    assert "tests/risk/test_operational_safety.py" in targets


def test_frontend_only_change_skips_python_and_documentation() -> None:
    plan = _plan("web/src/App.tsx", "web/package-lock.json")

    assert plan["frontend"] is True
    assert plan["python"] is False
    assert plan["docs"] is False
    assert plan["pytest_mode"] == "none"


def test_pure_documentation_change_uses_minimal_preflight() -> None:
    plan = _plan("docs/operator/windows_setup.md")

    assert plan == {
        "collect_all": False,
        "docs": True,
        "frontend": False,
        "pytest_mode": "none",
        "pytest_targets": [],
        "python": False,
        "reasons": ["documentation-only"],
    }


def test_workflow_test_and_config_changes_are_not_silently_skipped() -> None:
    workflow = _plan(".github/workflows/ci.yml")
    test = _plan("tests/research/test_grid_v1.py")
    config = _plan("pyproject.toml")

    assert workflow["python"] is workflow["frontend"] is workflow["collect_all"] is True
    workflow_targets = workflow["pytest_targets"]
    test_targets = test["pytest_targets"]
    assert isinstance(workflow_targets, list)
    assert isinstance(test_targets, list)
    assert "tests/ci/test_fast_ci.py" in workflow_targets
    assert "tests/research/test_grid_v1.py" in test_targets
    assert config["python"] is config["collect_all"] is True


def test_unknown_path_fails_closed_to_full_backend_and_frontend() -> None:
    plan = _plan("unclassified/new_safety_boundary.conf")

    assert plan["python"] is True
    assert plan["frontend"] is True
    assert plan["pytest_mode"] == "full"
    assert plan["reasons"] == ["unknown-fail-closed"]


def test_deleted_or_unmatched_python_test_fails_closed_to_full_suite() -> None:
    plan = _plan("tests/research/test_removed_safety_contract.py")

    assert plan["python"] is True
    assert plan["pytest_mode"] == "full"


def test_source_sensitive_document_is_not_treated_as_docs_only() -> None:
    plan = _plan("docs/research/btc_eth_grid_4h_v1_protocol.md")

    assert plan["docs"] is False
    assert plan["python"] is True
    targets = plan["pytest_targets"]
    assert isinstance(targets, list)
    assert "tests/research/test_grid_v1.py" in targets


def test_fast_gate_rejects_a_failed_or_missing_planned_job() -> None:
    command = [
        sys.executable,
        str(SCRIPT),
        "gate",
        "--plan-result",
        "success",
        "--python-planned",
        "true",
        "--python-result",
        "failure",
        "--frontend-planned",
        "false",
        "--frontend-result",
        "skipped",
        "--docs-planned",
        "false",
        "--docs-result",
        "skipped",
    ]

    assert subprocess.run(command, cwd=ROOT, check=False).returncode != 0


def test_fast_gate_accepts_an_exact_successful_plan() -> None:
    command = [
        sys.executable,
        str(SCRIPT),
        "gate",
        "--plan-result",
        "success",
        "--python-planned",
        "true",
        "--python-result",
        "success",
        "--frontend-planned",
        "false",
        "--frontend-result",
        "skipped",
        "--docs-planned",
        "false",
        "--docs-result",
        "skipped",
    ]

    assert subprocess.run(command, cwd=ROOT, check=False).returncode == 0


def test_fast_workflow_is_green_only_and_cancels_obsolete_fast_runs() -> None:
    workflow = FAST_WORKFLOW.read_text(encoding="utf-8")

    assert "types: [opened, synchronize, reopened, ready_for_review]" in workflow
    assert "group: fast-ci-pr-${{ github.event.pull_request.number }}" in workflow
    assert "cancel-in-progress: true" in workflow
    assert "name: fast-ci" in workflow
    assert "FULL_NOT_REQUESTED" not in workflow
    assert "name: full-suite" not in workflow
    assert "name: quality" not in workflow
    assert "exit 1" not in workflow


def test_full_workflow_is_absent_until_label_and_binds_exact_head() -> None:
    workflow = FULL_WORKFLOW.read_text(encoding="utf-8")

    assert "types: [labeled]" in workflow
    assert "branches: [main]" in workflow
    assert "github.event.label.name == 'ci-full'" in workflow
    assert "Verify exact full-suite SHA" in workflow
    assert "ref: ${{ github.event_name == 'push' && github.sha" in workflow
    assert "run: python -m pytest" in workflow
    assert "'quality' || 'full-not-requested / quality'" in workflow
    assert "'frontend-quality' || 'full-not-requested / frontend-quality'" in workflow
    assert "'full-suite' || 'full-not-requested / full-suite'" in workflow
    assert "synchronize" not in workflow
    assert "concurrency:" not in workflow
    assert "cancel-in-progress" not in workflow


@pytest.mark.parametrize("unsafe", ("../secret.py", "/absolute.py"))
def test_unsafe_paths_are_rejected(unsafe: str) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "classify-paths", unsafe],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
