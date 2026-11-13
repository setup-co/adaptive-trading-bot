"""ETH-only completion of the retained Issue #237 Raschke comparison."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from adaptive_trading_bot.research.bollinger_method2_no_time_exit import FOLLOWUP_END
from adaptive_trading_bot.research.failed_breakout_reversion_screen import (
    DEFAULT_INPUT_AUTHORITY,
    AcceptedInput,
    ScreenError,
    ScreenInputAuthority,
    _canonical,
    _clean_sha,
    _file_sha,
    _replace,
    _sha,
    _text,
)
from adaptive_trading_bot.research.raschke_pullback_screen import (
    ARMS,
    _projection,
    _same_opportunity,
    run_member,
)
from adaptive_trading_bot.research.raschke_pullback_screen import (
    PROTOCOL_ID as SCIENTIFIC_PROTOCOL_ID,
)
from adaptive_trading_bot.research.raschke_pullback_screen import (
    PROTOCOL_SHA256 as SCIENTIFIC_PROTOCOL_SHA256,
)
from adaptive_trading_bot.research.raschke_pullback_screen import (
    protocol_record as scientific_protocol_record,
)

PROTOCOL_ID = "BTC_ETH_RASCHKE_PULLBACK_FIB_COMPLETION_V1"
PARENT_EXECUTABLE_SHA = "c956bfb41659404f88a87f23022b4b47cb8359e6"
PARENT_EXECUTABLE_TREE = "3c61a332cebbeba2336c22a407faecc161dcdb72"
PARENT_RUN_ID = "e6a0bf8d-6fe4-45e3-9382-4e5e8c69192f"
PARENT_GZIP_SHA256 = "7c62c0db5e9da5af9a18cb3d87208358a67ee6eb91005ba65d83d1c3a06ade20"
PARENT_RAW_SHA256 = "f261e385d261433dee1af9ed5a40a03684ce35d32522e4c11c46aa6c2f745fca"
PARENT_GZIP_BYTES = 49_266
PARENT_RAW_BYTES = 624_742
PARENT_ROWS = 174
EXPECTED_PARENT_COUNTS = {
    ("raschke_pullback_reference", "BASE"): 53,
    ("raschke_pullback_reference", "STRESS"): 53,
    ("raschke_pullback_fib_zone", "BASE"): 17,
    ("raschke_pullback_fib_zone", "STRESS"): 17,
    ("raschke_pullback_round_zone", "BASE"): 17,
    ("raschke_pullback_round_zone", "STRESS"): 17,
}
EXPECTED_RECOVERED_COUNTS = {
    "raschke_pullback_reference": 3,
    "raschke_pullback_fib_zone": 1,
    "raschke_pullback_round_zone": 1,
}
ALLOWED_COMPLETE_STATUSES = {
    "ENTRY_UNFILLED",
    "ORDINARY_CLOSED",
    "INVALID_GEOMETRY_RECOVERED",
    "TERMINAL_OPEN",
}


def protocol_record(
    authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY,
    *,
    followup_end: datetime = FOLLOWUP_END,
) -> dict[str, Any]:
    """Return the hash-bound composite completion contract."""

    return {
        "protocol": PROTOCOL_ID,
        "scientific_protocol": SCIENTIFIC_PROTOCOL_ID,
        "scientific_protocol_sha256": SCIENTIFIC_PROTOCOL_SHA256,
        "scientific_contract": scientific_protocol_record(authority, followup_end=followup_end),
        "scope": {
            "reused_member": "BTCUSDT",
            "newly_processed_member": "ETHUSDT",
            "btc_candle_loading": False,
            "followup_end": followup_end.isoformat(),
        },
        "parent": {
            "executable_sha": PARENT_EXECUTABLE_SHA,
            "executable_tree": PARENT_EXECUTABLE_TREE,
            "run_id": PARENT_RUN_ID,
            "gzip_sha256": PARENT_GZIP_SHA256,
            "raw_sha256": PARENT_RAW_SHA256,
            "rows": PARENT_ROWS,
            "reuse_rule": "ALL_ROWS_UNCHANGED_REUSED_BTC_PARENT_OUTCOME",
            "funnel_metadata": "NOT_RETAINED",
        },
        "row_provenance": ["REUSED_BTC_PARENT_OUTCOME", "NEW_ETH_OUTCOME"],
        "promotion": "NO_PROMOTION",
    }


PROTOCOL_SHA256 = _sha(_canonical(protocol_record()))


def load_parent_rows(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate and read the complete retained BTC scenario population once."""

    path = path.resolve(strict=True)
    if path.stat().st_size != PARENT_GZIP_BYTES or _file_sha(path) != PARENT_GZIP_SHA256:
        raise ScreenError("retained BTC compressed identity differs")
    raw_hash = hashlib.sha256()
    raw_bytes = 0
    rows: list[dict[str, Any]] = []
    with gzip.open(path, "rb") as source:
        for line in source:
            raw_hash.update(line)
            raw_bytes += len(line)
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ScreenError("retained BTC row is not an object")
            rows.append(value)
    if (
        raw_bytes != PARENT_RAW_BYTES
        or raw_hash.hexdigest() != PARENT_RAW_SHA256
        or len(rows) != PARENT_ROWS
    ):
        raise ScreenError("retained BTC raw identity/count differs")
    pair_keys = [tuple(str(item) for item in row["pair_key"]) for row in rows]
    if len(set(pair_keys)) != PARENT_ROWS:
        raise ScreenError("retained BTC pair keys are missing or duplicated")
    if {str(row["symbol"]) for row in rows} != {"BTCUSDT"}:
        raise ScreenError("retained parent population is not BTC-only")
    counts = Counter((str(row["arm"]), str(row["scenario"])) for row in rows)
    if dict(counts) != EXPECTED_PARENT_COUNTS:
        raise ScreenError("retained BTC arm/scenario population differs")
    episode_scenarios: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        status = str(row["new"]["status"])
        if status not in {"ORDINARY_CLOSED", "INVALID_GEOMETRY_RECOVERED"} or not bool(
            row["new"]["natural_closed"]
        ):
            raise ScreenError("retained BTC population contains a non-closed outcome")
        key = (str(row["arm"]), str(row["pair_key"][3]))
        episode_scenarios.setdefault(key, set()).add(str(row["scenario"]))
    if any(value != {"BASE", "STRESS"} for value in episode_scenarios.values()):
        raise ScreenError("retained BTC BASE/STRESS population is not paired")
    _same_opportunity(rows)
    recovered = Counter(
        str(row["arm"]) for row in rows if row["new"]["status"] == "INVALID_GEOMETRY_RECOVERED"
    )
    if dict(recovered) != EXPECTED_RECOVERED_COUNTS:
        raise ScreenError("retained BTC recovery population differs")
    reused = [
        {
            **row,
            "provenance": "REUSED_BTC_PARENT_OUTCOME",
            "origin_executable_sha": PARENT_EXECUTABLE_SHA,
            "origin_run_id": PARENT_RUN_ID,
        }
        for row in rows
    ]
    return reused, {
        "metadata_complete": False,
        "funnel_metadata": "NOT_RETAINED",
        "provenance": "REUSED_BTC_PARENT_OUTCOME",
        "rows": len(rows),
        "unique_pair_keys": len(set(pair_keys)),
        "unique_admitted_episodes": len({str(row["pair_key"][3]) for row in rows}),
        "unique_arm_episode_pairs": len(episode_scenarios),
        "recovered_records": sum(recovered.values()),
    }


def _btc_member(rows: list[dict[str, Any]], integrity: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        arm_rows = [row for row in rows if row["arm"] == arm and row["scenario"] == "BASE"]
        counts[arm] = {
            "observations": None,
            "strength_episodes": None,
            "underlying_opportunities": None,
            "anchor_valid_opportunities": None,
            "signals": None,
            "risk_rejected": None,
            "safety_rejected": None,
            "admitted": len(arm_rows),
            "retained_depth_available": sum(
                bool(row["audit"]["depth_available"]) for row in arm_rows
            ),
        }
    return {
        "symbol": "BTCUSDT",
        "metadata_complete": False,
        "funnel_metadata": "NOT_RETAINED",
        "expected_observation_slots_per_arm": 17_280,
        "counts": counts,
        "underlying_opportunity_ids": None,
        "retained_integrity": integrity,
    }


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    body = b"".join(_canonical(_text(row)) + b"\n" for row in rows)
    path.write_bytes(gzip.compress(body, mtime=0))


def run_completion(
    repo: Path,
    parent_rows_path: Path,
    output: Path,
    *,
    run_id: str,
    executable_sha: str,
    authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY,
    followup_end: datetime = FOLLOWUP_END,
    expected_protocol_sha256: str | None = None,
) -> dict[str, Any]:
    """Reuse retained BTC outcomes and process exactly the ETH member once."""

    repo, output = repo.resolve(strict=True), output.resolve()
    if output.exists():
        raise ScreenError("completion output already exists; retry is forbidden")
    _clean_sha(repo, executable_sha)
    protocol = protocol_record(authority, followup_end=followup_end)
    protocol_sha256 = _sha(_canonical(protocol))
    if expected_protocol_sha256 is not None and protocol_sha256 != expected_protocol_sha256:
        raise ScreenError("effective completion protocol differs from declared identity")
    output.mkdir(parents=True)
    _replace(
        output / "run-identity.json",
        {
            "run_id": run_id,
            "executable_sha": executable_sha,
            "protocol_sha256": protocol_sha256,
            "scope": "REUSE_BTC_PROCESS_ETH_ONLY",
        },
    )
    _replace(output / "protocol.json", protocol)

    btc_rows, parent_integrity = load_parent_rows(parent_rows_path)
    _replace(output / "btc-parent-integrity.json", parent_integrity)
    btc_member = _btc_member(btc_rows, parent_integrity)
    _replace(output / "btcusdt-member.json", btc_member)

    accepted = AcceptedInput(repo, authority)
    store, five = accepted.load("ETHUSDT", tail_end=followup_end)
    eth_member, eth_rows = run_member("ETHUSDT", store, five, output, authority=authority)
    _replace(output / "ethusdt-member.json", eth_member)
    new_eth_rows = [
        {
            **row,
            "provenance": "NEW_ETH_OUTCOME",
            "origin_executable_sha": executable_sha,
            "origin_run_id": run_id,
        }
        for row in eth_rows
    ]
    combined = [*btc_rows, *new_eth_rows]
    pair_keys = [tuple(str(item) for item in row["pair_key"]) for row in combined]
    if len(set(pair_keys)) != len(combined) or len(btc_rows) != PARENT_ROWS:
        raise ScreenError("composite result keys are duplicated or incomplete")
    _same_opportunity(combined)
    projection = _projection(
        run_id,
        executable_sha,
        [btc_member, eth_member],
        combined,
        authority,
        protocol_sha256,
    )
    projection["scientific_protocol"] = projection["protocol"]
    projection["protocol"] = PROTOCOL_ID
    projection["composite_provenance"] = {
        "reused_btc_rows": len(btc_rows),
        "new_eth_rows": len(new_eth_rows),
        "btc_origin_executable_sha": PARENT_EXECUTABLE_SHA,
        "eth_origin_executable_sha": executable_sha,
        "btc_funnel_metadata": "NOT_RETAINED",
    }
    unexpected = sorted(
        {
            str(row["new"]["status"])
            for row in combined
            if str(row["new"]["status"]) not in ALLOWED_COMPLETE_STATUSES
        }
    )
    if unexpected:
        projection["classification"] = "INCOMPLETE"
    projection["unexpected_execution_statuses"] = unexpected
    projection["promotion"] = "NO_PROMOTION"
    projection["limitations"] = [
        *projection["limitations"],
        "BTC_FINAL_FUNNEL_METADATA_NOT_RETAINED",
        "COMPOSITE_REUSES_PARENT_BTC_OUTCOMES",
        "PRE_FUNDING_MARK_LIQUIDATION_UNQUALIFIED",
    ]
    _replace(output / "projection.json", projection)
    _write_rows(output / "composite-rows.jsonl.gz", combined)
    _replace(
        output / "complete.json",
        {
            "status": "COMPLETE" if not unexpected else "INCOMPLETE",
            "reused_btc_rows": len(btc_rows),
            "new_eth_rows": len(new_eth_rows),
            "total_rows": len(combined),
            "projection_sha256": _file_sha(output / "projection.json"),
            "composite_rows_sha256": _file_sha(output / "composite-rows.jsonl.gz"),
        },
    )
    return projection
