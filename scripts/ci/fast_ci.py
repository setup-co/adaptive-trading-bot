"""Deterministic changed-file planning for the bounded PR FAST CI tier."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

_SHA = re.compile(r"[0-9a-f]{40}")
_GRID_TESTS = (
    "tests/research/test_grid_approval_enrollment.py",
    "tests/research/test_grid_history_runner.py",
    "tests/research/test_grid_history_preflight.py",
    "tests/research/test_grid_v1.py",
    "tests/research/test_grid_v1_metadata_calendar.py",
    "tests/research/test_binance_grid_history.py",
)
_SAFETY_TESTS = (
    "tests/accounting/test_positions.py",
    "tests/admission/test_portfolio.py",
    "tests/backtest/test_accounting.py",
    "tests/backtest/test_execution.py",
    "tests/execution/test_simulation.py",
    "tests/orders/test_exit_intent.py",
    "tests/orders/test_intent.py",
    "tests/risk/test_operational_safety.py",
    "tests/risk/test_risk_core.py",
)
_CI_TEST = "tests/ci/test_fast_ci.py"
_SENSITIVE_DOCS = {
    "AGENTS.md",
    "docs/architect_workflow.md",
    "docs/project_state.md",
    "docs/project_state_current.md",
    "docs/trading_bot_master_plan.md",
}
_CRITICAL_AREAS = {"accounting", "admission", "execution", "live", "orders", "risk"}


@dataclass(frozen=True, slots=True)
class FastPlan:
    """Fail-closed plan derived only from canonical repository paths."""

    python: bool
    frontend: bool
    docs: bool
    pytest_mode: Literal["none", "targeted", "full"]
    collect_all: bool
    pytest_targets: tuple[str, ...]
    reasons: tuple[str, ...]


def _path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or "\0" in normalized:
        raise ValueError(f"unsafe changed path: {value!r}")
    return path.as_posix()


def changed_files(base: str, head: str) -> tuple[str, ...]:
    """Read exact PR changed paths without shell interpolation."""

    if _SHA.fullmatch(base) is None or _SHA.fullmatch(head) is None:
        raise ValueError("base and head must be lowercase 40-character Git SHAs")
    result = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--diff-filter=ACMRTUXBD", f"{base}...{head}"],
        check=True,
        capture_output=True,
    )
    return tuple(_path(item.decode("utf-8")) for item in result.stdout.split(b"\0") if item)


def classify(paths: tuple[str, ...]) -> FastPlan:
    """Classify representative PR paths; unknown paths deliberately broaden checks."""

    if not paths:
        raise ValueError("an empty PR diff cannot be classified safely")
    normalized = tuple(_path(path) for path in paths)
    python = False
    frontend = False
    collect_all = False
    force_full = False
    pure_docs = True
    targets: set[str] = set()
    reasons: set[str] = set()

    for path in normalized:
        suffix = PurePosixPath(path).suffix
        if path.startswith("web/"):
            frontend = True
            pure_docs = False
            reasons.add("frontend")
            continue
        if path.startswith(".github/workflows/"):
            python = frontend = collect_all = True
            pure_docs = False
            targets.add(_CI_TEST)
            reasons.add("workflow-config")
            continue
        if path in _SENSITIVE_DOCS or path.startswith("docs/research/"):
            python = True
            pure_docs = False
            reasons.add("source-sensitive-document")
            if "grid" in path.lower():
                targets.update(_GRID_TESTS)
                targets.update(_SAFETY_TESTS)
            else:
                force_full = True
            continue
        if path.startswith("docs/") and suffix in {".md", ".rst", ".txt"}:
            reasons.add("documentation-only")
            continue
        pure_docs = False
        if path == "pyproject.toml" or path.startswith("scripts/ci/"):
            python = collect_all = True
            targets.add(_CI_TEST)
            reasons.add("python-ci-config")
            continue
        if path.startswith("tests/") and suffix == ".py":
            python = True
            reasons.add("changed-test")
            if not Path(path).is_file() or path == "tests/conftest.py":
                force_full = True
            else:
                targets.add(path)
            if "grid" in path.lower():
                targets.update(_GRID_TESTS)
                targets.update(_SAFETY_TESTS)
            continue
        if path.startswith("src/adaptive_trading_bot/") and suffix == ".py":
            python = True
            parts = PurePosixPath(path).parts
            area = parts[2] if len(parts) >= 4 else ""
            if "grid" in path.lower():
                targets.update(_GRID_TESTS)
                targets.update(_SAFETY_TESTS)
                reasons.add("grid-or-history-authority")
            elif area in _CRITICAL_AREAS:
                targets.update(_SAFETY_TESTS)
                targets.add(f"tests/{area}")
                reasons.add("shared-safety-authority")
            elif area and Path(f"tests/{area}").is_dir():
                targets.add(f"tests/{area}")
                reasons.add("matched-python-area")
            else:
                force_full = True
                reasons.add("unclassified-python")
            continue
        if path.startswith("migrations/") and suffix == ".py":
            python = collect_all = True
            targets.add("tests/test_database.py")
            reasons.add("migration")
            continue
        python = frontend = True
        force_full = True
        reasons.add("unknown-fail-closed")

    if pure_docs:
        return FastPlan(False, False, True, "none", False, (), tuple(sorted(reasons)))
    if force_full:
        return FastPlan(
            python,
            frontend,
            False,
            "full" if python else "none",
            collect_all,
            (),
            tuple(sorted(reasons)),
        )
    mode: Literal["none", "targeted", "full"] = "targeted" if python else "none"
    return FastPlan(
        python,
        frontend,
        False,
        mode,
        collect_all,
        tuple(sorted(targets)),
        tuple(sorted(reasons)),
    )


def _write_outputs(path: str, plan: FastPlan) -> None:
    output = {
        "python": str(plan.python).lower(),
        "frontend": str(plan.frontend).lower(),
        "docs": str(plan.docs).lower(),
        "pytest_mode": plan.pytest_mode,
        "plan_json": json.dumps(asdict(plan), separators=(",", ":"), sort_keys=True),
    }
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        for key, value in output.items():
            handle.write(f"{key}={value}\n")


def _run_tests(plan: FastPlan) -> None:
    if not plan.python or plan.pytest_mode == "none":
        raise ValueError("the plan contains no Python regression work")
    if plan.collect_all:
        subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"], check=True)
    command = [sys.executable, "-m", "pytest"]
    if plan.pytest_mode == "targeted":
        if not plan.pytest_targets:
            raise ValueError("targeted Python plan has no tests")
        command.extend(plan.pytest_targets)
    subprocess.run(command, check=True)


def _gate(arguments: argparse.Namespace) -> None:
    if arguments.plan_result != "success":
        raise ValueError("FAST plan did not succeed")
    checks = (
        ("python", arguments.python_planned, arguments.python_result),
        ("frontend", arguments.frontend_planned, arguments.frontend_result),
        ("docs", arguments.docs_planned, arguments.docs_result),
    )
    for name, planned, result in checks:
        expected = "success" if planned == "true" else "skipped"
        if result != expected:
            raise ValueError(f"FAST {name} result {result!r} differs from {expected!r}")
    durations = tuple(int(value) for value in arguments.duration if value)
    if any(value < 0 for value in durations):
        raise ValueError("FAST duration cannot be negative")
    runner_seconds = sum(durations)
    wall_seconds = max(durations, default=0)
    summary = (
        "## FAST CI usage\n\n"
        f"- job runner time: {runner_seconds / 60:.2f} minutes\n"
        f"- longest measured job: {wall_seconds / 60:.2f} minutes\n"
        "- scope: change-aware; this is not the FULL regression gate\n"
    )
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with open(destination, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(summary)
    else:
        print(summary, end="")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("classify", "require-docs", "run-tests"):
        command = commands.add_parser(name)
        command.add_argument("--base", required=True)
        command.add_argument("--head", required=True)
        if name == "classify":
            command.add_argument("--github-output")
    paths = commands.add_parser("classify-paths")
    paths.add_argument("paths", nargs="+")
    gate = commands.add_parser("gate")
    gate.add_argument("--plan-result", required=True)
    for name in ("python", "frontend", "docs"):
        gate.add_argument(f"--{name}-planned", required=True)
        gate.add_argument(f"--{name}-result", required=True)
    gate.add_argument("--duration", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "gate":
        _gate(arguments)
        return 0
    if arguments.command == "classify-paths":
        print(json.dumps(asdict(classify(tuple(arguments.paths))), sort_keys=True))
        return 0
    plan = classify(changed_files(arguments.base, arguments.head))
    if arguments.command == "classify":
        print(json.dumps(asdict(plan), indent=2, sort_keys=True))
        if arguments.github_output:
            _write_outputs(arguments.github_output, plan)
        return 0
    if arguments.command == "require-docs":
        if not plan.docs:
            raise ValueError("FAST documentation job received a non-documentation plan")
        return 0
    _run_tests(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
