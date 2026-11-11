import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest


def _load_planner() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "ci" / "plan_pytest_shards.py"
    spec = importlib.util.spec_from_file_location("plan_pytest_shards", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PLANNER = _load_planner()
partition_nodeids = cast(Any, PLANNER.partition_nodeids)
collect_nodeids = cast(Any, PLANNER.collect_nodeids)
write_plan = cast(Any, PLANNER.write_plan)


def _inventory() -> list[str]:
    explicit = [
        f"{module}::test_assigned"
        for modules in PLANNER.EXPLICIT_MODULES.values()
        for module in modules
    ]
    return [*explicit, "tests/test_safety.py::test_remaining", "tests/test_api.py::test_remaining"]


def test_partition_is_complete_disjoint_and_keeps_remaining_tests() -> None:
    inventory = _inventory()
    shards = partition_nodeids(inventory)
    flattened = [nodeid for values in shards.values() for nodeid in values]
    assert len(flattened) == len(inventory)
    assert set(flattened) == set(inventory)
    assert shards[4] == [
        "tests/test_safety.py::test_remaining",
        "tests/test_api.py::test_remaining",
    ]


def test_partition_rejects_duplicate_node_ids_and_missing_named_modules() -> None:
    inventory = _inventory()
    with pytest.raises(ValueError, match="duplicate collected node IDs"):
        partition_nodeids([*inventory, inventory[0]])
    with pytest.raises(ValueError, match="explicit shard modules missing"):
        partition_nodeids(inventory[1:])


def test_argument_files_select_whole_modules_and_retain_exact_nodeids(tmp_path: Path) -> None:
    inventory = _inventory()
    write_plan(tmp_path / "plan", inventory)
    first_module = PLANNER.EXPLICIT_MODULES[1][0]
    assert (tmp_path / "plan" / "shard-1.txt").read_text().splitlines() == [first_module]
    assert (tmp_path / "plan" / "shard-1-nodeids.txt").read_text().splitlines() == [
        f"{first_module}::test_assigned"
    ]
    assert (tmp_path / "plan" / "shard-4.txt").read_text().splitlines() == [
        "tests/test_safety.py",
        "tests/test_api.py",
    ]


def test_root_bound_python_module_invocation_imports_checkout_namespace(tmp_path: Path) -> None:
    (tmp_path / "scripts" / "research").mkdir(parents=True)
    (tmp_path / "scripts" / "research" / "fixture_helper.py").write_text(
        "VALUE = 'ROOT_NAMESPACE_OK'\n", encoding="utf-8"
    )
    test_path = tmp_path / "tests" / "research" / "test_nested_import.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "from scripts.research import fixture_helper\n\n"
        "def test_root_namespace_import():\n"
        "    assert fixture_helper.VALUE == 'ROOT_NAMESPACE_OK'\n",
        encoding="utf-8",
    )
    nodeid = "tests/research/test_nested_import.py::test_root_namespace_import"
    argument_file = tmp_path / "selected.txt"
    argument_file.write_text(f"{nodeid}\n", encoding="utf-8")

    assert collect_nodeids(tmp_path, (f"@{argument_file}",)) == [nodeid]
