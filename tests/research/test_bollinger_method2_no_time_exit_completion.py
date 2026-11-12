import gzip
import json
import runpy
import subprocess
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from pytest import MonkeyPatch

from adaptive_trading_bot.research.bollinger_method2_no_time_exit import ParentAuthority
from adaptive_trading_bot.research.bollinger_method2_no_time_exit_completion import (
    PROTOCOL_SHA256,
    ShorterAuthority,
)

ROOT = Path(__file__).parents[2]
PARENT_CLI = runpy.run_path(str(ROOT / "scripts" / "research" / "bollinger_method2_screen.py"))
SHORTER_CLI = runpy.run_path(
    str(ROOT / "scripts" / "research" / "bollinger_method2_no_time_exit.py")
)
COMPLETION_CLI = runpy.run_path(
    str(ROOT / "scripts" / "research" / "bollinger_method2_no_time_exit_completion.py")
)
FIXTURE = runpy.run_path(str(Path(__file__).with_name("test_trend_impulse_pullback_screen.py")))
parent_main = PARENT_CLI["main"]
shorter_main = SHORTER_CLI["main"]
completion_main = COMPLETION_CLI["main"]
write_authority = FIXTURE["_write_authority"]


def _commit(repo: Path, message: str) -> str:
    subprocess.run(("git", "add", "."), cwd=repo, check=True)
    subprocess.run(("git", "commit", "-qm", message), cwd=repo, check=True)
    return subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _publish_parent(repo: Path, output: Path, executable: str) -> tuple[Path, ParentAuthority]:
    projection_bytes = (output / "projection.json").read_bytes()
    raw = (output / "trades.jsonl").read_bytes()
    root = repo / "parent"
    root.mkdir()
    (root / "projection.json").write_bytes(projection_bytes)
    (root / "trades.jsonl.gz").write_bytes(gzip.compress(raw, mtime=0))
    manifest = b'{"fixture":"completion-parent"}'
    (root / "publication-manifest.json").write_bytes(manifest)
    projection = json.loads(projection_bytes)
    return root, ParentAuthority(
        executable,
        "synthetic-parent",
        projection["protocol_sha256"],
        sha256(projection_bytes).hexdigest(),
        sha256(manifest).hexdigest(),
        len(raw),
        sha256(raw).hexdigest(),
        len(raw.splitlines()),
    )


def test_actual_selective_cli_reuses_closed_rows_and_replays_only_open_keys(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    authority = write_authority(repo)
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(
        ("git", "config", "user.email", "synthetic@example.invalid"),
        cwd=repo,
        check=True,
    )
    subprocess.run(("git", "config", "user.name", "Synthetic Fixture"), cwd=repo, check=True)
    parent_executable = _commit(repo, "synthetic corpus")
    parent_output = tmp_path / "parent-output"
    assert (
        parent_main(
            [
                "--repo",
                str(repo),
                "--output",
                str(parent_output),
                "--run-id",
                "synthetic-parent",
                "--executable-sha",
                parent_executable,
            ],
            authority=authority,
        )
        == 0
    )
    parent_root, parent_authority = _publish_parent(repo, parent_output, parent_executable)
    shorter_executable = _commit(repo, "bind parent")
    full_output = tmp_path / "full-output"
    assert (
        shorter_main(
            [
                "--repo",
                str(repo),
                "--output",
                str(full_output),
                "--run-id",
                "synthetic-full",
                "--executable-sha",
                shorter_executable,
            ],
            authority=authority,
            parent=parent_root,
            parent_authority=parent_authority,
        )
        == 0
    )
    rows = [
        json.loads(line) for line in (full_output / "paired-rows.jsonl").read_text().splitlines()
    ]
    selected_decision = rows[0]["decision_at"]
    selected = [
        row
        for row in rows
        if row["symbol"] == rows[0]["symbol"] and row["decision_at"] == selected_decision
    ]
    assert len(selected) == 4
    cutoff = datetime.fromisoformat(selected[0]["entry"]["fill_at"])
    selected_keys = {tuple(row["pair_key"]) for row in selected}
    for row in rows:
        if tuple(row["pair_key"]) not in selected_keys:
            parent = row["parent"]
            row["new"].update(
                {
                    "status": "ORDINARY_CLOSED",
                    "trigger": "TARGET",
                    "natural_closed": True,
                    "gross_pnl_quote": parent["gross_pnl_quote"],
                    "total_fees_quote": parent["total_fees_quote"],
                    "net_pnl_quote": parent["net_pnl_quote"],
                    "closed_net_r": parent["closed_net_r"],
                    "unresolved_quantity": "0",
                    "holding_lower_seconds": parent["holding_lower_seconds"],
                    "holding_upper_seconds": parent["holding_upper_seconds"],
                }
            )
            continue
        entry_fee = row["entry"]["fee_quote"]
        row["new"].update(
            {
                "status": "TERMINAL_OPEN",
                "trigger": None,
                "natural_closed": False,
                "gross_pnl_quote": "0",
                "exit_commission_quote": None,
                "total_fees_quote": entry_fee,
                "net_pnl_quote": f"-{entry_fee}",
                "closed_net_r": None,
                "unresolved_quantity": row["entry"]["quantity"],
                "holding_lower_seconds": None,
                "holding_upper_seconds": None,
            }
        )
        row["terminal_valuation"]["valuation_at"] = cutoff.isoformat()
        row["terminal_valuation"]["depends_on_open_position"] = True
    raw = b"".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n" for row in rows
    )
    shorter_root = repo / "shorter"
    shorter_root.mkdir()
    compressed = gzip.compress(raw, mtime=0)
    (shorter_root / "paired-rows.jsonl.gz").write_bytes(compressed)
    protocol = json.loads((full_output / "protocol.json").read_bytes())
    protocol["treatment"]["data_end"] = cutoff.isoformat()
    protocol_bytes = json.dumps(protocol, sort_keys=True, separators=(",", ":")).encode()
    (shorter_root / "protocol.json").write_bytes(protocol_bytes)
    projection = {"fixture": "selective-shorter", "rows": len(rows)}
    projection_bytes = json.dumps(projection, sort_keys=True, separators=(",", ":")).encode()
    (shorter_root / "projection.json").write_bytes(projection_bytes)
    manifest = b'{"fixture":"selective-shorter-publication"}'
    (shorter_root / "publication-manifest.json").write_bytes(manifest)
    shorter_authority = ShorterAuthority(
        shorter_executable,
        "synthetic-shorter",
        cutoff,
        sha256(protocol_bytes).hexdigest(),
        sha256(projection_bytes).hexdigest(),
        sha256(manifest).hexdigest(),
        len(raw),
        sha256(raw).hexdigest(),
        sha256(compressed).hexdigest(),
        len(rows),
        len(rows) - 4,
        4,
    )
    completion_executable = _commit(repo, "bind shorter")
    output = tmp_path / "completion-output"
    assert (
        completion_main(
            [
                "--repo",
                str(repo),
                "--output",
                str(output),
                "--run-id",
                "synthetic-completion",
                "--executable-sha",
                completion_executable,
            ],
            authority=authority,
            comparator_root=parent_root,
            comparator_authority=parent_authority,
            shorter_root=shorter_root,
            shorter_authority=shorter_authority,
            endpoint=authority.tail_end,
            enforce_default_counts=False,
        )
        == 0
    )

    complete = json.loads((output / "complete.json").read_text())
    projection = json.loads((output / "projection.json").read_text())
    records = [
        json.loads(line) for line in (output / "composite-rows.jsonl").read_text().splitlines()
    ]
    assert complete["status"] == "COMPLETE"
    assert complete["pair_keys"] == len(rows)
    assert complete["reused"] == len(rows) - 4
    assert complete["replayed"] == 4
    assert projection["reuse_provenance"]["replayed_previously_open"] == 4
    reused = next(record for record in records if record["provenance"] == "REUSED_NATURAL_CLOSE")
    replayed = [record for record in records if record["provenance"] == "REPLAYED_PREVIOUSLY_OPEN"]
    assert reused["source_shorter_row"] == reused["composite_row"]
    assert len(replayed) == 4
    for record in replayed:
        assert record["source_shorter_row"]["entry"] == record["composite_row"]["entry"]
        assert record["source_shorter_row"]["new"]["status"] == "TERMINAL_OPEN"
    statuses = {record["composite_row"]["new"]["natural_closed"] for record in replayed}
    assert statuses == {False}


def test_default_cli_binds_declared_production_protocol(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {}

    monkeypatch.setitem(completion_main.__globals__, "run", fake_run)
    assert (
        completion_main(
            [
                "--repo",
                str(tmp_path),
                "--output",
                str(tmp_path / "output"),
                "--run-id",
                "default-binding",
                "--executable-sha",
                "0" * 40,
            ]
        )
        == 0
    )
    assert captured["expected_protocol_sha256"] == PROTOCOL_SHA256
