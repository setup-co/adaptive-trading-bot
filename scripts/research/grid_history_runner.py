"""Guarded Issue #247 one-shot Grid history runner CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from adaptive_trading_bot.research.grid_history_preflight import (
    CalendarEpisode,
    CalendarStatus,
    GridHistoryWeek,
    V3GridHistoryAdapter,
    V3SourceIdentity,
    VerifiedCalendar,
)
from adaptive_trading_bot.research.grid_history_runner import (
    GridStudyGrant,
    run_one_shot,
    verify_finalized,
)
from adaptive_trading_bot.research.grid_v1 import GridMinute


def _git_identity(repo: Path) -> tuple[str, str]:
    def read(revision: str) -> str:
        completed = subprocess.run(
            ["git", "rev-parse", revision],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    return read("HEAD"), read("HEAD^{tree}")


def _synthetic_fixture(
    path: Path,
    grant: GridStudyGrant,
) -> tuple[VerifiedCalendar, Callable[[CalendarEpisode], GridHistoryWeek]]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or value.get("schema") != "grid-history-synthetic-fixture-v1":
        raise ValueError("synthetic fixture schema differs")
    rows: list[CalendarEpisode] = []
    prices: dict[tuple[str, datetime], Decimal] = {}
    items = value.get("weeks")
    if not isinstance(items, list):
        raise ValueError("synthetic fixture weeks differ")
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("synthetic fixture week differs")
        start = datetime.fromisoformat(str(item["episode_start"]).replace("Z", "+00:00"))
        status = CalendarStatus(str(item["status"]))
        row = CalendarEpisode(
            str(item["symbol"]),
            start,
            status,
            str(item["partition"]),
            start - timedelta(days=7),
            start + timedelta(days=6, hours=20, minutes=2),
            tuple(str(reason) for reason in item.get("reason_codes", [])),
            tuple(str(flag) for flag in item.get("flag_codes", [])),
        )
        rows.append(row)
        if status is CalendarStatus.METADATA_ELIGIBLE:
            prices[(row.symbol, row.episode_start)] = Decimal(str(item["price"]))
    calendar = VerifiedCalendar.synthetic(tuple(rows), fixture_id=str(value["fixture_id"]))
    fixture_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    source_identity = V3SourceIdentity(
        grant.manifest_sha256,
        grant.report_sha256,
        grant.dataset_version,
        grant.qualification_protocol,
        "FIXTURE_ONLY",
    )

    def load(row: CalendarEpisode) -> GridHistoryWeek:
        price = prices[(row.symbol, row.episode_start)]
        minutes = tuple(
            GridMinute(
                row.episode_start + timedelta(minutes=index),
                price,
                price,
                price,
                price,
                row.episode_start + timedelta(minutes=index + 1),
                price,
            )
            for index in range(6 * 24 * 60 + 20 * 60 + 2)
        )
        return GridHistoryWeek(
            source_identity=source_identity,
            calendar=row,
            completed_4h_closes=(price,) * 42,
            minutes=minutes,
            source_file_sha256=((path.name, fixture_sha),),
        )

    return calendar, load


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-grant")
    validate.add_argument("--grant", required=True, type=Path)
    run = commands.add_parser("run")
    run.add_argument("--grant", required=True, type=Path)
    run.add_argument("--data-root", required=True, type=Path)
    run.add_argument("--ledger-root", required=True, type=Path)
    synthetic = commands.add_parser("synthetic-run")
    synthetic.add_argument("--grant", required=True, type=Path)
    synthetic.add_argument("--fixture", required=True, type=Path)
    synthetic.add_argument("--ledger-root", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("--ledger", required=True, type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    if args.command == "verify":
        print(json.dumps(verify_finalized(args.ledger), sort_keys=True, separators=(",", ":")))
        return 0
    grant = GridStudyGrant.from_file(args.grant)
    actual_sha, actual_tree = _git_identity(repo)
    if args.command == "validate-grant":
        grant.validate(actual_sha=actual_sha, actual_tree=actual_tree, synthetic=False)
        print(json.dumps({"grant_sha256": grant.sha256, "status": "VALID"}, sort_keys=True))
        return 0
    if args.command == "synthetic-run":
        grant.validate(actual_sha=actual_sha, actual_tree=actual_tree, synthetic=True)
        calendar, loader = _synthetic_fixture(args.fixture, grant)
        preflight = {
            "schema": "grid-history-synthetic-preflight-v1",
            "fixture": str(args.fixture.resolve(strict=True)),
            "network_requests": 0,
            "calendar_rows": len(calendar.rows),
        }
        _, projection = run_one_shot(
            grant=grant,
            calendar=calendar,
            ledger_root=args.ledger_root,
            actual_sha=actual_sha,
            actual_tree=actual_tree,
            week_loader=loader,
            preflight=preflight,
            synthetic=True,
        )
    else:
        grant.validate(actual_sha=actual_sha, actual_tree=actual_tree, synthetic=False)
        calendar = VerifiedCalendar.accepted(
            repo / "docs/research/btc_eth_grid_4h_v1_eligibility_calendar.json"
        )
        adapter = V3GridHistoryAdapter(args.data_root, calendar=calendar)
        files, byte_count = adapter.preflight_sources()
        preflight = {
            "schema": "grid-history-retained-preflight-v1",
            "network_requests": 0,
            "validated_source_files": files,
            "validated_source_bytes": byte_count,
            "calendar_sha256": calendar.identity,
        }
        _, projection = run_one_shot(
            grant=grant,
            calendar=calendar,
            ledger_root=args.ledger_root,
            actual_sha=actual_sha,
            actual_tree=actual_tree,
            week_loader=adapter.load_week,
            preflight=preflight,
            synthetic=False,
        )
    print(json.dumps(projection, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
