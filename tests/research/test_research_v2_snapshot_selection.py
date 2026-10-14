"""Synthetic-only characterization of the frozen public panel selector."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.research import research_v2_snapshot_selection as panel
from scripts.research import research_v2_universe_feasibility as prior


def daily(quote: str = "10", *, count: int = 30, duplicate: bool = False) -> bytes:
    rows: list[list[object]] = []
    for day in range(count):
        opened = prior.ms(panel.START + timedelta(days=day))
        base = "0" if quote == "0" else "1"
        rows.append([opened, "2", "3", "1", "2", base, opened + 86_400_000, quote, 1, base, quote])
    if duplicate:
        rows.append(rows[-1])
    return panel.canonical(rows)


def plan_for(symbols: list[str], evidence: Path) -> dict[str, Any]:
    records = [{"symbol": symbol, "metadata_status": "METADATA_ELIGIBLE"} for symbol in symbols]
    plan: dict[str, Any] = {
        "queries": [{"path": panel.ROUTE, "query": panel.query_for(symbol)} for symbol in symbols],
        "outer_eligibility": records,
        "considered_catalog": [],
        "reused_recent": {},
        "ranking_cutoff": prior.iso(panel.END),
        "attempt_budget": 2 * len(symbols),
    }
    (evidence / "plan.json").write_bytes(panel.canonical(plan))
    return plan


def attempts_for(
    evidence: Path, symbols: list[str], values: dict[str, str]
) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for number, symbol in enumerate(symbols, start=1):
        raw = daily(values[symbol])
        raw_file = f"{number:03d}.raw.json"
        (evidence / raw_file).write_bytes(raw)
        attempts.append(
            {
                "started": {
                    "query": panel.query_for(symbol),
                    "path": panel.ROUTE,
                    "retry": 0,
                    "plan_sha256": panel.sha((evidence / "plan.json").read_bytes()),
                },
                "result": {
                    "status": "HTTP",
                    "http_status": 200,
                    "raw_file": raw_file,
                    "sha256": panel.sha(raw),
                    "byte_length": len(raw),
                },
            }
        )
    return attempts


def test_rank_ties_shuffled_and_anchors(tmp_path: Path) -> None:
    symbols = [
        "ETHUSDT",
        "JUSDT",
        "IUSDT",
        "HUSDT",
        "GUSDT",
        "FUSDT",
        "EUSDT",
        "DUSDT",
        "CUSDT",
        "BUSDT",
        "AUSDT",
        "BTCUSDT",
    ]
    values = {symbol: str(index + 1) for index, symbol in enumerate(symbols)}
    values["AUSDT"] = values["BUSDT"] = "100"
    plan = plan_for(symbols, tmp_path)
    result = panel.project(Path("."), tmp_path, plan, attempts_for(tmp_path, symbols, values))
    assert result["status"] == "SELECTED_FOR_DATA_QUALIFICATION"
    assert result["selected_symbols"][:2] == ["BTCUSDT", "ETHUSDT"]
    assert result["ranked_symbols"][:2] == ["AUSDT", "BUSDT"]
    assert len(result["selected_symbols"]) == 10
    assert (
        next(row for row in result["candidate_records"] if row["symbol"] == "AUSDT")[
            "daily_quote_values"
        ]
        == ["100"] * 30
    )


@pytest.mark.parametrize(
    "symbols,expected",
    [
        (["BTCUSDT", "ETHUSDT", "AUSDT"], "FEWER_THAN_TEN_QUALIFIED"),
        (["BTCUSDT"] + [f"S{n}USDT" for n in range(12)], "ANCHOR_NOT_QUALIFIED"),
    ],
)
def test_blocked_population(tmp_path: Path, symbols: list[str], expected: str) -> None:
    plan = plan_for(symbols, tmp_path)
    result = panel.project(
        Path("."), tmp_path, plan, attempts_for(tmp_path, symbols, dict.fromkeys(symbols, "1"))
    )
    assert result["status"] == "BLOCKED"
    assert expected in result["blockers"]
    assert result["selected_symbols"] == []


def test_missing_is_not_zero_and_incomplete_is_excluded(tmp_path: Path) -> None:
    symbols = ["BTCUSDT", "ETHUSDT", "AUSDT"]
    plan = plan_for(symbols, tmp_path)
    attempts = attempts_for(tmp_path, symbols, dict.fromkeys(symbols, "0"))
    zero = panel.project(Path("."), tmp_path, plan, attempts)
    assert zero["candidate_records"][0]["median_daily_usdt_quote_turnover"] == "0"
    (tmp_path / "003.raw.json").write_bytes(daily("1", count=29))
    attempts[2]["result"].update(
        {
            "sha256": panel.sha((tmp_path / "003.raw.json").read_bytes()),
            "byte_length": (tmp_path / "003.raw.json").stat().st_size,
        }
    )
    incomplete = panel.project(Path("."), tmp_path, plan, attempts)
    assert incomplete["candidate_records"][2]["daily_status"] == "INCOMPLETE_GRID"
    assert incomplete["candidate_records"][2]["median_daily_usdt_quote_turnover"] is None
    missing = panel.project(Path("."), tmp_path, plan, attempts[:2])
    assert missing["status"] == "RANKING_INCOMPLETE"
    assert missing["candidate_records"][2]["daily_status"] == "UNAVAILABLE"


def test_duplicate_or_off_grid_daily_fails_rank(tmp_path: Path) -> None:
    symbols = ["BTCUSDT"]
    plan = plan_for(symbols, tmp_path)
    attempts = attempts_for(tmp_path, symbols, {"BTCUSDT": "1"})
    for raw in (
        daily(duplicate=True),
        panel.canonical(
            [
                [
                    prior.ms(panel.START) + 1,
                    "2",
                    "3",
                    "1",
                    "2",
                    "1",
                    prior.ms(panel.START) + 86_400_001,
                    "1",
                    1,
                    "1",
                    "1",
                ]
            ]
        ),
    ):
        (tmp_path / "001.raw.json").write_bytes(raw)
        attempts[0]["result"].update({"sha256": panel.sha(raw), "byte_length": len(raw)})
        result = panel.project(Path("."), tmp_path, plan, attempts)
        assert result["status"] == "RANKING_INCOMPLETE"
        assert result["candidate_records"][0]["daily_status"] == "MALFORMED"


def test_metadata_scaled_and_duplicate_quarantine(monkeypatch: pytest.MonkeyPatch) -> None:
    names = ["BTC", "1000PEPE", "X", "X"]
    symbols = ["BTCUSDT", "1000PEPEUSDT", "XUSDT", "X2USDT"]
    catalog = [
        {
            "symbol": symbol,
            "baseAsset": base,
            "quoteAsset": "USDT",
            "marginAsset": "USDT",
            "contractType": "PERPETUAL",
            "underlyingType": "COIN",
        }
        for symbol, base in zip(symbols, names, strict=True)
    ]
    considered = {symbol: {"symbol": symbol, "reason": "ELIGIBLE_CURRENT"} for symbol in symbols}
    monkeypatch.setattr(panel, "_catalog", lambda repo: (catalog, considered, {}))
    _, rows = panel.eligibility(Path("."))
    assert [row["metadata_status"] for row in rows] == [
        "SCALED_UNDERLYING_UNRESOLVED",
        "METADATA_ELIGIBLE",
        "IDENTITY_UNRESOLVED",
        "DUPLICATE_EXPOSURE_UNRESOLVED",
    ]  # sorted symbol order


def test_timeout_only_retry_classifier() -> None:
    assert panel.retryable(urllib.error.URLError(TimeoutError()))
    assert panel.retryable(TimeoutError())
    assert panel.retryable(status=500)
    assert not panel.retryable(urllib.error.URLError(OSError("dns")))
    assert not panel.retryable(urllib.error.URLError("tls failure"))
    assert not panel.retryable(OSError("disk"))
    assert not panel.retryable(status=429)


def test_route_and_reuse_plan(tmp_path: Path) -> None:
    panel.validate_query(panel.ROUTE, panel.query_for("BTCUSDT"))
    with pytest.raises(panel.SelectionError):
        panel.validate_query("/capi/v3/order", panel.query_for("BTCUSDT"))
    with pytest.raises(panel.SelectionError):
        panel.validate_query(panel.ROUTE, {**panel.query_for("BTCUSDT"), "interval": "5m"})
    plan = panel.build_plan(Path("."), code_revision="TEST")
    assert len(plan["outer_eligibility"]) == 181
    assert len(plan["reused_recent"]) == 5
    assert len(plan["queries"]) == 171
    assert not set(plan["reused_recent"]).intersection(plan["missing_symbols"])


def test_capture_denial_and_no_retry_on_local_start_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = plan_for(["BTCUSDT", "ETHUSDT"], tmp_path)
    calls: list[str] = []

    class Denied:
        def get(self, path: str, query: dict[str, str], timeout: float) -> tuple[int, bytes]:
            calls.append(query["symbol"])
            return 429, b"denied"

    monkeypatch.setattr(panel, "load_plan", lambda repo, evidence: plan)
    monkeypatch.setattr(panel, "finalize", lambda repo, evidence: {"status": "BLOCKED"})
    panel.capture(Path("."), tmp_path, Denied())
    assert calls == ["BTCUSDT"]
    assert len(list(tmp_path.glob("*.started.json"))) == 1
    other = tmp_path / "other"
    other.mkdir()
    (other / "plan.json").write_bytes(panel.canonical(plan))
    real_write = panel.atomic_new

    def fail_start(path: Path, body: bytes) -> None:
        if path.name.endswith("started.json"):
            raise panel.SelectionError("local evidence persistence failed")
        real_write(path, body)

    monkeypatch.setattr(panel, "atomic_new", fail_start)
    with pytest.raises(panel.SelectionError, match="persistence"):
        panel.capture(Path("."), other, Denied())
    assert calls == ["BTCUSDT"]


@pytest.mark.parametrize("failure,expected_calls", [(500, 2), (429, 1), ("DNS", 1)])
def test_capture_budget_and_retry_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: int | str, expected_calls: int
) -> None:
    plan = plan_for(["BTCUSDT"], tmp_path)
    calls = 0

    class Failing:
        def get(self, path: str, query: dict[str, str], timeout: float) -> tuple[int, bytes]:
            nonlocal calls
            calls += 1
            if failure == "DNS":
                raise urllib.error.URLError(OSError("dns"))
            assert isinstance(failure, int)
            return failure, b"unavailable"

    monkeypatch.setattr(panel, "load_plan", lambda repo, evidence: plan)
    monkeypatch.setattr(panel, "finalize", lambda repo, evidence: {"status": "BLOCKED"})
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    panel.capture(Path("."), tmp_path, Failing())
    assert calls == expected_calls
    assert len(list(tmp_path.glob("*.started.json"))) == expected_calls


def test_offline_verifier_rederives_not_just_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    symbols = ["BTCUSDT", "ETHUSDT"]
    plan = plan_for(symbols, tmp_path)
    attempts = attempts_for(tmp_path, symbols, dict.fromkeys(symbols, "1"))
    for index, attempt in enumerate(attempts, 1):
        (tmp_path / f"{index:03d}.started.json").write_bytes(panel.canonical(attempt["started"]))
        (tmp_path / f"{index:03d}.result.json").write_bytes(panel.canonical(attempt["result"]))
    monkeypatch.setattr(panel, "load_plan", lambda repo, evidence: plan)
    panel.finalize(Path("."), tmp_path)
    monkeypatch.setattr(
        panel, "CAPTURE_SELECTION_SHA256", panel.sha((tmp_path / "selection.json").read_bytes())
    )
    monkeypatch.setattr(
        panel, "CAPTURE_MANIFEST_SHA256", panel.sha((tmp_path / "manifest.json").read_bytes())
    )
    monkeypatch.setattr(panel.PublicDailyTransport, "get", lambda *args: pytest.fail("network"))
    assert panel.verify(Path("."), tmp_path)["status"] == "BLOCKED"
    saved = json.loads((tmp_path / "selection.json").read_bytes())
    saved["candidate_records"][0]["rank"] = 999
    (tmp_path / "selection.json").write_bytes(panel.canonical(saved))
    manifest = json.loads((tmp_path / "manifest.json").read_bytes())
    for item in manifest["files"]:
        if item["file"] == "selection.json":
            item["sha256"] = panel.sha((tmp_path / "selection.json").read_bytes())
            item["byte_length"] = (tmp_path / "selection.json").stat().st_size
    (tmp_path / "manifest.json").write_bytes(panel.canonical(manifest))
    monkeypatch.setattr(
        panel, "CAPTURE_SELECTION_SHA256", panel.sha((tmp_path / "selection.json").read_bytes())
    )
    monkeypatch.setattr(
        panel, "CAPTURE_MANIFEST_SHA256", panel.sha((tmp_path / "manifest.json").read_bytes())
    )
    with pytest.raises(panel.SelectionError, match="raw-derived"):
        panel.verify(Path("."), tmp_path)


def offline_subprocess_env(root: Path) -> dict[str, str]:
    """Keep local exchange credentials out of verifier subprocesses and tracebacks."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("ATB_WEEX_DEMO_")}
    env["PYTHONPATH"] = str(root)
    return env


def test_unmocked_original_verification_from_outside_repo(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    env = offline_subprocess_env(root)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.research.research_v2_snapshot_selection",
            "verify",
            "--repo",
            str(root),
            "--evidence",
            str(root / panel.CAPTURE_BUNDLE),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout) == {
        "missing_queries": 0,
        "selected": [],
        "status": "RANKING_INCOMPLETE",
    }


def test_unmocked_original_verification_after_squash_in_shallow_checkout(
    tmp_path: Path,
) -> None:
    """Reviewed bytes remain verifiable when the capture commit is absent."""
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "synthetic-source"
    source.mkdir()
    for relative in (
        panel.ORIGINAL,
        panel.SUPPLEMENT,
        panel.CAPTURE_BUNDLE,
        panel.KAS_SUPPLEMENT,
    ):
        shutil.copytree(root / relative, source / relative)
    for relative in (
        Path("scripts/__init__.py"),
        Path("scripts/research/__init__.py"),
        Path("scripts/research/research_v2_universe_feasibility.py"),
        Path("scripts/research/research_v2_snapshot_selection.py"),
    ):
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, destination)

    def git(*args: str, cwd: Path = source) -> str:
        return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()

    git("init", "-b", "main")
    git("config", "user.email", "synthetic@example.invalid")
    git("config", "user.name", "Synthetic Verification")
    git("add", ".")
    git("commit", "-m", "synthetic base")
    git("switch", "-c", "reviewed-work")
    (source / "reviewed-marker.txt").write_text("reviewed bytes\n", encoding="utf-8")
    git("add", "reviewed-marker.txt")
    git("commit", "-m", "reviewed branch")
    reviewed_tree = git("rev-parse", "HEAD^{tree}")
    git("switch", "main")
    git("merge", "--squash", "reviewed-work")
    git("commit", "-m", "squash equivalent")
    assert git("rev-parse", "HEAD^{tree}") == reviewed_tree
    shallow = tmp_path / "shallow-main"
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--depth",
            "1",
            "--branch",
            "main",
            source.as_uri(),
            str(shallow),
        ],
        check=True,
        capture_output=True,
    )
    assert git("rev-list", "--count", "HEAD", cwd=shallow) == "1"
    missing_capture_commit = subprocess.run(
        ["git", "cat-file", "-e", panel.CAPTURE_CODE_REVISION],
        cwd=shallow,
        capture_output=True,
    )
    assert missing_capture_commit.returncode != 0
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.research.research_v2_snapshot_selection",
            "verify",
            "--repo",
            str(shallow),
            "--evidence",
            str(shallow / panel.CAPTURE_BUNDLE),
        ],
        cwd=tmp_path,
        env=offline_subprocess_env(shallow),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(completed.stdout)["status"] == "RANKING_INCOMPLETE"
    kas_verified = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.research.research_v2_snapshot_selection",
            "kas-verify",
            "--repo",
            str(shallow),
            "--evidence",
            str(shallow / panel.KAS_SUPPLEMENT),
        ],
        cwd=tmp_path,
        env=offline_subprocess_env(shallow),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(kas_verified.stdout)["status"] == "SELECTED_FOR_DATA_QUALIFICATION"
    helper = shallow / "scripts/research/research_v2_universe_feasibility.py"
    helper.write_bytes(helper.read_bytes() + b"\n# unreviewed verifier mutation\n")
    rejected = subprocess.run(
        completed.args,
        cwd=tmp_path,
        env=offline_subprocess_env(shallow),
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "accepted #173 helper generation mismatch" in rejected.stderr


def bounded_repo(tmp_path: Path) -> tuple[Path, Path]:
    root = Path(__file__).resolve().parents[2]
    repo = tmp_path / "bound-repo"
    repo.mkdir()
    for relative in (panel.ORIGINAL, panel.SUPPLEMENT, panel.CAPTURE_BUNDLE):
        shutil.copytree(root / relative, repo / relative)
    evidence = repo / panel.KAS_SUPPLEMENT
    evidence.mkdir(parents=True)
    plan = panel.build_kas_plan(
        repo, code_revision="synthetic-verification", created_at_utc="2026-10-14T20:00:00Z"
    )
    (evidence / "plan.json").write_bytes(panel.canonical(plan))
    return repo, evidence


def test_kas_only_completion_reuses_old_raw_and_rederives_selection(tmp_path: Path) -> None:
    repo, evidence = bounded_repo(tmp_path)
    originals = {
        name: panel.sha((repo / panel.CAPTURE_BUNDLE / name).read_bytes())
        for name in ("plan.json", "selection.json", "manifest.json")
    }
    calls: list[dict[str, str]] = []

    class Fake:
        def get(self, path: str, query: dict[str, str], timeout: float) -> tuple[int, bytes]:
            calls.append(query)
            return 200, daily("1")

    result = panel.capture_kas(repo, evidence, Fake())
    assert calls == [panel.KAS_QUERY]
    assert result["status"] == "SELECTED_FOR_DATA_QUALIFICATION"
    assert len(result["selected_symbols"]) == 10
    assert result["supplement_attempt_count"] == 1
    assert panel.verify_kas(repo, evidence)["selected_symbols"] == result["selected_symbols"]
    assert originals == {
        name: panel.sha((repo / panel.CAPTURE_BUNDLE / name).read_bytes()) for name in originals
    }

    saved = json.loads((evidence / "combined_selection.json").read_bytes())
    kas = next(row for row in saved["candidate_records"] if row["symbol"] == "KASUSDT")
    kas["median_daily_usdt_quote_turnover"] = "999999999999"
    kas["rank"] = 1
    kas["selected"] = True
    (evidence / "combined_selection.json").write_bytes(panel.canonical(saved))
    manifest = json.loads((evidence / "manifest.json").read_bytes())
    for item in manifest["files"]:
        if item["file"] == "combined_selection.json":
            item["sha256"] = panel.sha((evidence / item["file"]).read_bytes())
            item["byte_length"] = (evidence / item["file"]).stat().st_size
    (evidence / "manifest.json").write_bytes(panel.canonical(manifest))
    with pytest.raises(panel.SelectionError, match="raw-derived"):
        panel.verify_kas(repo, evidence)


@pytest.mark.parametrize(
    "first,expected",
    [
        ("TIMEOUT", 2),
        (500, 2),
        ("DNS", 1),
        (403, 1),
    ],
)
def test_kas_retry_ceiling_and_sanitized_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first: str | int, expected: int
) -> None:
    repo, evidence = bounded_repo(tmp_path)
    calls = 0

    class Fake:
        def get(self, path: str, query: dict[str, str], timeout: float) -> tuple[int, bytes]:
            nonlocal calls
            assert query == panel.KAS_QUERY
            calls += 1
            if calls == 2:
                return 200, daily("1")
            if first == "TIMEOUT":
                raise urllib.error.URLError(TimeoutError())
            if first == "DNS":
                raise urllib.error.URLError(OSError(123, "sensitive endpoint text"))
            assert isinstance(first, int)
            return first, b"unavailable"

    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    result = panel.capture_kas(repo, evidence, Fake())
    assert calls == expected
    assert result["supplement_attempt_count"] == expected
    assert result["status"] == (
        "SELECTED_FOR_DATA_QUALIFICATION" if expected == 2 else "RANKING_INCOMPLETE"
    )
    if first == "DNS":
        failure = (evidence / "001.result.json").read_text(encoding="utf-8")
        assert "sensitive endpoint text" not in failure
        assert json.loads(failure)["errno"] == 123


def test_kas_persistence_failure_blocks_before_get(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, evidence = bounded_repo(tmp_path)
    calls = 0

    class Fake:
        def get(self, path: str, query: dict[str, str], timeout: float) -> tuple[int, bytes]:
            nonlocal calls
            calls += 1
            return 200, daily()

    write = panel.atomic_new

    def broken(path: Path, body: bytes) -> None:
        if path.name.endswith("started.json"):
            raise panel.SelectionError("local persistence failed")
        write(path, body)

    monkeypatch.setattr(panel, "atomic_new", broken)
    with pytest.raises(panel.SelectionError, match="persistence"):
        panel.capture_kas(repo, evidence, Fake())
    assert calls == 0


def test_locked_kas_completion_identity_and_raw_derived_panel() -> None:
    repo = Path(__file__).resolve().parents[2]
    evidence = repo / panel.KAS_SUPPLEMENT
    assert panel.sha((evidence / "plan.json").read_bytes()) == (
        "515454fe2064bd90feccee243a6b06bfea46e449d8ef58c4be3000ef9148d756"
    )
    assert panel.sha((evidence / "001.raw.json").read_bytes()) == (
        "f71383de530cbe2ac308482cbfa2be8beca9eb3f54e0c7001e26856aebee02a7"
    )
    assert panel.sha((evidence / "combined_selection.json").read_bytes()) == (
        "80c2574deb124357f671785d6bcb64368a0441fa921d250f1ac2d6bc6cae41c4"
    )
    assert panel.sha((evidence / "manifest.json").read_bytes()) == (
        "de96989dd4c582001db271a44f6e8174dee504f620e84c4631501574c369ec79"
    )
    result = panel.verify_kas(repo, evidence)
    assert result["status"] == "SELECTED_FOR_DATA_QUALIFICATION"
    assert result["supplement_attempt_count"] == 1
    assert result["selected_symbols"] == [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "XRPUSDT",
        "ZECUSDT",
        "ZAMAUSDT",
        "BCHUSDT",
        "BTWUSDT",
        "AVAXUSDT",
        "DOGEUSDT",
    ]
    kas = next(row for row in result["candidate_records"] if row["symbol"] == "KASUSDT")
    assert kas["rank"] == 96
    assert kas["median_daily_usdt_quote_turnover"] == "624885.73020"
    assert kas["selected"] is False
