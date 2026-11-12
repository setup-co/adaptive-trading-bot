import gzip
import json
import runpy
import subprocess
from hashlib import sha256
from pathlib import Path

from adaptive_trading_bot.research.bollinger_method2_no_time_exit import (
    ParentAuthority,
)

ROOT = Path(__file__).parents[2]
OLD_CLI = runpy.run_path(str(ROOT / "scripts" / "research" / "bollinger_method2_screen.py"))
NEW_CLI = runpy.run_path(str(ROOT / "scripts" / "research" / "bollinger_method2_no_time_exit.py"))
FIXTURE = runpy.run_path(str(Path(__file__).with_name("test_trend_impulse_pullback_screen.py")))
old_main = OLD_CLI["main"]
new_main = NEW_CLI["main"]
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


def test_actual_cli_reuses_parent_rows_and_writes_paired_projection(tmp_path: Path) -> None:
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
        old_main(
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

    parent = repo / "parent"
    parent.mkdir()
    projection_bytes = (parent_output / "projection.json").read_bytes()
    (parent / "projection.json").write_bytes(projection_bytes)
    raw_rows = (parent_output / "trades.jsonl").read_bytes()
    (parent / "trades.jsonl.gz").write_bytes(gzip.compress(raw_rows, mtime=0))
    manifest_bytes = b'{"fixture":"synthetic-parent-publication"}'
    (parent / "publication-manifest.json").write_bytes(manifest_bytes)
    parent_projection = json.loads(projection_bytes)
    expected_rows = len(raw_rows.splitlines())
    parent_authority = ParentAuthority(
        execution_sha=parent_executable,
        run_id="synthetic-parent",
        protocol_sha256=parent_projection["protocol_sha256"],
        projection_sha256=sha256(projection_bytes).hexdigest(),
        publication_manifest_sha256=sha256(manifest_bytes).hexdigest(),
        trades_bytes=len(raw_rows),
        trades_sha256=sha256(raw_rows).hexdigest(),
        expected_rows=expected_rows,
    )
    executable = _commit(repo, "bind synthetic parent")
    output = tmp_path / "paired-output"

    assert (
        new_main(
            [
                "--repo",
                str(repo),
                "--output",
                str(output),
                "--run-id",
                "synthetic-no-time-exit",
                "--executable-sha",
                executable,
            ],
            authority=authority,
            parent=parent,
            parent_authority=parent_authority,
        )
        == 0
    )

    projection = json.loads((output / "projection.json").read_text())
    complete = json.loads((output / "complete.json").read_text())
    rows = [json.loads(line) for line in (output / "paired-rows.jsonl").read_text().splitlines()]
    assert complete["status"] == "COMPLETE"
    assert projection["paired_rows"] == expected_rows == len(rows)
    assert projection["parent"]["run_id"] == "synthetic-parent"
    assert projection["classification"] == "POST_RESULT_EXPLORATORY"
    assert projection["promotion"] == "NO_PROMOTION"
    assert all(row["parent"]["closed_net_r"] is not None for row in rows)
    assert all("terminal_minus_parent_net_r" in row for row in rows)


def test_parent_loader_rejects_missing_or_duplicate_pair_rows(tmp_path: Path) -> None:
    # The actual-command test exercises the positive boundary. This regression fixes the
    # complete/unique pair contract without invoking engines or loading market data.
    from adaptive_trading_bot.research.bollinger_method2_no_time_exit import (
        NoTimeExitError,
        load_parent,
    )

    root = tmp_path / "parent"
    root.mkdir()
    projection = {
        "run_id": "parent",
        "executable_sha": "a" * 40,
        "protocol_sha256": "b" * 64,
    }
    projection_bytes = json.dumps(projection, separators=(",", ":")).encode()
    (root / "projection.json").write_bytes(projection_bytes)
    manifest = b"{}"
    (root / "publication-manifest.json").write_bytes(manifest)
    row = {
        "symbol": "BTCUSDT",
        "arm": "PRICE_CONTROL",
        "scenario": "BASE",
        "decision_at": "2026-01-01T00:00:00+00:00",
    }
    raw = (json.dumps(row) + "\n") * 2
    raw_bytes = raw.encode()
    (root / "trades.jsonl.gz").write_bytes(gzip.compress(raw_bytes, mtime=0))
    authority = ParentAuthority(
        "a" * 40,
        "parent",
        "b" * 64,
        sha256(projection_bytes).hexdigest(),
        sha256(manifest).hexdigest(),
        len(raw_bytes),
        sha256(raw_bytes).hexdigest(),
        2,
    )
    try:
        load_parent(tmp_path, root, authority=authority)
    except NoTimeExitError as error:
        assert "duplicated" in str(error)
    else:
        raise AssertionError("duplicated parent rows were accepted")
