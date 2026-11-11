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
