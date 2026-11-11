"""Read-only Issue #231 projection export from retained compact result records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from time import perf_counter
from typing import Any

from adaptive_trading_bot.research.failed_breakout_reversion_screen import (
    _CONTEXT,
    _clean_sha,
    _decision_time_identity,
    _file_sha,
    _projection,
    _replace,
    _spacing_mask,
)

SOURCE_RUN_ID = "5708360d-0b81-437d-a888-6956bda16ed6"
SOURCE_EXECUTABLE_SHA = "cef2384568e8c007ad20139bbd736382f32002b7"
RECOVERY_STATUS = "REPORT_RECONSTRUCTED_FROM_SAVED_ROWS_ORIGINAL_EXIT_1"


class SavedExportError(ValueError):
    """Retained records are incomplete, inconsistent, or outside the frozen authority."""


@dataclass(frozen=True, slots=True)
class SavedMemberAuthority:
    wire: str
    path: str
    size: int
    sha256: str
    admissions: int
    scenario_rows: int
    spaced: int


@dataclass(frozen=True, slots=True)
class SavedRowsAuthority:
    root: Path
    source_publication_head: str
    artifact_index_sha256: str
    trades_size: int
    trades_sha256: str
    total_rows: int
    members: tuple[SavedMemberAuthority, ...]


DEFAULT_SAVED_ROWS_AUTHORITY = SavedRowsAuthority(
    Path("data/research_evidence/issue-231-failed-breakout-reversion-cef23845"),
    "d7d9a2898fdafdedcbbc0f5a921b2985bf192794",
    "7d706308bc7fa80774db0904d3592e1c13a648112141972dc9da61100f5fac97",
    1_181_723,
    "e389d2cc2598ef7193d9bbb4a279d41bf3f041d0b4618b664018a825931600ed",
    590,
    (
        SavedMemberAuthority(
            "BTCUSDT",
            "output/btcusdt-member.json",
            2_485,
            "ba2633083df4d963994bf7878fea389094dbd3e7f24fbf4c418669d60d50f8d7",
            99,
            198,
            76,
        ),
        SavedMemberAuthority(
            "ETHUSDT",
            "output/ethusdt-member.json",
            4_839,
            "5dbda9d742beb72008e9a79894c2049df98714ef3db6bdd4535e4065c48b2a0d",
            196,
            392,
            160,
        ),
    ),
)


def _verify_file(path: Path, *, size: int, sha256: str) -> None:
    if not path.is_file() or path.stat().st_size != size or _file_sha(path) != sha256:
        raise SavedExportError(f"retained authority changed: {path.name}")


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SavedExportError(f"expected JSON object: {path.name}")
    return value


def _finite(value: object, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as error:
        raise SavedExportError(f"{field} is not Decimal-compatible") from error
    if not parsed.is_finite():
        raise SavedExportError(f"{field} must be finite")
    return parsed


def _validate_row(row: dict[str, Any]) -> tuple[str, str, str]:
    wire = str(row.get("symbol"))
    scenario = str(row.get("scenario"))
    decision_at = _decision_time_identity(row.get("decision_at"))
    if scenario not in {"BASE", "STRESS"}:
        raise SavedExportError("saved scenario is outside BASE/STRESS")
    if row.get("status") not in {"ORDINARY_CLOSED", "INVALID_GEOMETRY_RECOVERED"}:
        raise SavedExportError("saved result is not a supported closed terminal status")
    if _finite(row.get("unresolved_quantity"), field="unresolved_quantity") != 0:
        raise SavedExportError("saved result retains unresolved exposure")
    if not isinstance(row.get("entry"), dict) or not isinstance(row.get("exit"), dict):
        raise SavedExportError("closed saved result must retain entry and exit fills")
    if not isinstance(row.get("trigger"), dict):
        raise SavedExportError("closed saved result must retain its exit trigger")

    approved = _finite(row.get("approved_risk_quote"), field="approved_risk_quote")
    quantity = _finite(row.get("quantity"), field="quantity")
    gross = _finite(row.get("gross_pnl_quote"), field="gross_pnl_quote")
    fees = _finite(row.get("total_fees_quote"), field="total_fees_quote")
    net = _finite(row.get("net_pnl_quote"), field="net_pnl_quote")
    net_r = _finite(row.get("closed_net_r"), field="closed_net_r")
    entry_fee = _finite(row["entry"].get("fee_quote"), field="entry.fee_quote")
    exit_fee = _finite(row["exit"].get("fee_quote"), field="exit.fee_quote")
    wait = _finite(row.get("entry_grid_wait_seconds"), field="entry_grid_wait_seconds")
    holding_lower = _finite(row.get("holding_lower_seconds"), field="holding_lower_seconds")
    holding_upper = _finite(row.get("holding_upper_seconds"), field="holding_upper_seconds")
    if approved <= 0 or quantity <= 0 or fees < 0 or entry_fee < 0 or exit_fee < 0:
        raise SavedExportError("saved risk, quantity, or fee sign is invalid")
    if wait < 0 or holding_lower < 0 or holding_upper < holding_lower:
        raise SavedExportError("saved wait/holding interval is invalid")
    with localcontext(_CONTEXT):
        if entry_fee + exit_fee != fees or gross - fees != net or net / approved != net_r:
            raise SavedExportError("saved per-row accounting relationship changed")
    return wire, decision_at, scenario


def load_saved_records(
    repo: Path,
    authority: SavedRowsAuthority = DEFAULT_SAVED_ROWS_AUTHORITY,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    """Validate and return the retained member/row records in their saved order."""

    root = repo.resolve(strict=True) / authority.root
    index_path = root / "artifact-index.json"
    _verify_file(
        index_path,
        size=index_path.stat().st_size if index_path.is_file() else -1,
        sha256=authority.artifact_index_sha256,
    )
    index = _json_object(index_path)
    indexed = {
        str(item.get("path")): (int(item.get("size", -1)), str(item.get("sha256")))
        for item in index.get("files", [])
        if isinstance(item, dict)
    }

    trades_path = root / "output/trades.jsonl"
    _verify_file(trades_path, size=authority.trades_size, sha256=authority.trades_sha256)
    if indexed.get("output/trades.jsonl") != (
        authority.trades_size,
        authority.trades_sha256,
    ):
        raise SavedExportError("artifact index does not bind the retained trades")

    members: list[dict[str, Any]] = []
    member_by_wire: dict[str, tuple[SavedMemberAuthority, dict[str, Any]]] = {}
    source_hashes = {
        "artifact_index": authority.artifact_index_sha256,
        "trades": authority.trades_sha256,
    }
    for member_authority in authority.members:
        member_path = root / member_authority.path
        _verify_file(
            member_path,
            size=member_authority.size,
            sha256=member_authority.sha256,
        )
        if indexed.get(member_authority.path) != (
            member_authority.size,
            member_authority.sha256,
        ):
            raise SavedExportError("artifact index does not bind a retained member report")
        member = _json_object(member_path)
        if member.get("symbol") != member_authority.wire:
            raise SavedExportError("member report symbol changed")
        members.append(member)
        member_by_wire[member_authority.wire] = (member_authority, member)
        source_hashes[f"{member_authority.wire}_member"] = member_authority.sha256

    rows: list[dict[str, Any]] = []
    with trades_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            value = json.loads(line)
            if not isinstance(value, dict):
                raise SavedExportError(f"trade row {line_number} is not a JSON object")
            rows.append(value)
    if len(rows) != authority.total_rows:
        raise SavedExportError("retained trade-row count changed")

    keys: set[tuple[str, str, str]] = set()
    decisions: dict[str, dict[str, set[str]]] = {wire: {} for wire in member_by_wire}
    decision_order: dict[str, list[datetime]] = {wire: [] for wire in member_by_wire}
    for row in rows:
        wire, decision_at, scenario = _validate_row(row)
        if wire not in member_by_wire:
            raise SavedExportError("retained row has an unexpected member")
        key = (wire, decision_at, scenario)
        if key in keys:
            raise SavedExportError("duplicate retained decision/scenario identity")
        keys.add(key)
        if decision_at not in decisions[wire]:
            decisions[wire][decision_at] = set()
            decision_order[wire].append(datetime.fromisoformat(decision_at))
        decisions[wire][decision_at].add(scenario)

    for wire, (member_authority, member) in member_by_wire.items():
        wire_rows = sum(row.get("symbol") == wire for row in rows)
        if wire_rows != member_authority.scenario_rows:
            raise SavedExportError("member scenario-row count changed")
        if len(decisions[wire]) != member_authority.admissions or any(
            scenarios != {"BASE", "STRESS"} for scenarios in decisions[wire].values()
        ):
            raise SavedExportError("member BASE/STRESS admission pairing changed")
        counts = member.get("counts")
        if not isinstance(counts, dict) or counts.get("admitted") != member_authority.admissions:
            raise SavedExportError("member admitted count changed")
        saved_mask = member.get("spacing_mask")
        expected_mask = [item.isoformat() for item in _spacing_mask(decision_order[wire])]
        if (
            saved_mask != expected_mask
            or member.get("spacing_mask_count") != member_authority.spaced
            or len(expected_mask) != member_authority.spaced
        ):
            raise SavedExportError("saved outcome-independent spacing mask changed")

    return members, rows, source_hashes


def export_saved_projection(
    repo: Path,
    output: Path,
    *,
    exporter_sha: str,
    authority: SavedRowsAuthority = DEFAULT_SAVED_ROWS_AUTHORITY,
) -> dict[str, Any]:
    """Write one derived projection without invoking historical-input or simulation paths."""

    started_at = datetime.now(UTC)
    started_clock = perf_counter()
    repo = repo.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise SavedExportError("saved-row export output already exists")
    _clean_sha(repo, exporter_sha)
    members, rows, source_hashes = load_saved_records(repo, authority)
    projection = _projection(SOURCE_RUN_ID, SOURCE_EXECUTABLE_SHA, members, rows)
    output.mkdir(parents=True)
    projection_path = output / "projection.json"
    _replace(projection_path, projection)
    report = {
        "status": RECOVERY_STATUS,
        "architect_acceptance": "PENDING",
        "original_run_exit": 1,
        "source_run_id": SOURCE_RUN_ID,
        "source_executable_sha": SOURCE_EXECUTABLE_SHA,
        "exporter_sha": exporter_sha,
        "classification": projection["classification"],
        "promotion": projection["promotion"],
        "members": members,
        "economic_table": projection["economic_table"],
        "chronological_60d_blocks": projection["chronological_60d_blocks"],
        "limitations": projection["limitations"],
    }
    report_path = output / "report.json"
    _replace(report_path, report)
    ended_at = datetime.now(UTC)
    attestation = {
        "schema": "issue-231-saved-row-export-attestation-v1",
        "status": RECOVERY_STATUS,
        "architect_acceptance": "PENDING",
        "source_publication_head": authority.source_publication_head,
        "source_run_id": SOURCE_RUN_ID,
        "source_run_exit": 1,
        "source_executable_sha": SOURCE_EXECUTABLE_SHA,
        "exporter_sha": exporter_sha,
        "source_hashes": source_hashes,
        "validated_member_count": len(members),
        "validated_row_count": len(rows),
        "started_at": started_at,
        "ended_at": ended_at,
        "elapsed_seconds": str(Decimal(str(perf_counter() - started_clock))),
        "projection_sha256": _file_sha(projection_path),
        "report_sha256": _file_sha(report_path),
    }
    _replace(output / "attestation.json", attestation)
    return attestation
