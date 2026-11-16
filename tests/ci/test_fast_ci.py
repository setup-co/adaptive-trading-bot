from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "fast_ci.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


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


def test_workflow_preserves_deliberate_exact_head_full_gate_and_fast_cancellation() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "types: [opened, synchronize, reopened, ready_for_review, labeled]" in workflow
    assert "github.event.label.name == 'ci-full'" in workflow
    assert "Verify exact full-suite SHA" in workflow
    assert "ref: ${{ github.event.pull_request.head.sha }}" in workflow
    assert "run: python -m pytest" in workflow
    assert "name: quality" in workflow
    assert "name: frontend-quality" in workflow
    assert "name: full-suite" in workflow
    assert "FULL_NOT_REQUESTED" in workflow
    full_gate = (
        "full-suite:\n"
        "    name: full-suite\n"
        "    needs: [quality, frontend-quality]\n"
        "    if: always()"
    )
    assert full_gate in workflow
    assert "format('fast-ci-pr-{0}', github.event.pull_request.number)" in workflow
    assert "format('full-ci-run-{0}', github.run_id)" in workflow
    assert "cancel-in-progress:" in workflow
    full_section = workflow[workflow.index("  quality:") :]
    assert "cancel-in-progress" not in full_section


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
