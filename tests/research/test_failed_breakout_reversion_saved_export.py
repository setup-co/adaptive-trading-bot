import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from adaptive_trading_bot.research import failed_breakout_reversion_screen as screen
from adaptive_trading_bot.research.failed_breakout_reversion_saved_export import (
    RECOVERY_STATUS,
    SavedMemberAuthority,
    SavedRowsAuthority,
    export_saved_projection,
)
from adaptive_trading_bot.research.failed_breakout_reversion_screen import _canonical, _text


def _write(path: Path, value: object) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(_text(value)))
    return path.stat().st_size, sha256(path.read_bytes()).hexdigest()


def _row(wire: str, scenario: str, decision_at: datetime, net_r: str) -> dict[str, object]:
    net = Decimal(net_r)
    return {
        "symbol": wire,
        "scenario": scenario,
        "decision_at": decision_at,
        "status": "ORDINARY_CLOSED",
        "trigger": {"kind": "TIME_EXIT"},
        "entry": {"fee_quote": "0.01"},
        "exit": {"fee_quote": "0.01"},
        "quantity": "1",
        "approved_risk_quote": "1",
        "gross_pnl_quote": str(net + Decimal("0.02")),
        "total_fees_quote": "0.02",
        "net_pnl_quote": str(net),
        "closed_net_r": str(net),
        "unresolved_quantity": "0",
        "entry_grid_wait_seconds": "0",
        "holding_lower_seconds": "1800",
        "holding_upper_seconds": "1800",
    }


def _saved_fixture(repo: Path) -> SavedRowsAuthority:
    root = repo / "saved"
    decisions = (
        datetime(2026, 4, 10, 0, 15, tzinfo=UTC),
        datetime(2026, 4, 10, 0, 50, tzinfo=UTC),
    )
    members: list[SavedMemberAuthority] = []
    rows: list[dict[str, object]] = []
    indexed: list[dict[str, object]] = []
    for wire in ("BTCUSDT", "ETHUSDT"):
        member_path = root / "output" / f"{wire.lower()}-member.json"
        member = {
            "symbol": wire,
            "counts": {"admitted": 2},
            "spacing_mask": [item.isoformat() for item in decisions],
            "spacing_mask_count": 2,
        }
        size, digest = _write(member_path, member)
        relative = f"output/{wire.lower()}-member.json"
        indexed.append({"path": relative, "size": size, "sha256": digest})
        members.append(SavedMemberAuthority(wire, relative, size, digest, 2, 4, 2))
        for scenario in ("BASE", "STRESS"):
            rows.extend(
                (
                    _row(wire, scenario, decisions[0], "0.35"),
                    _row(wire, scenario, decisions[1], "-1.00"),
                )
            )
    trades_path = root / "output" / "trades.jsonl"
    trades_path.write_bytes(b"".join(_canonical(_text(row)) + b"\n" for row in rows))
    trades_size = trades_path.stat().st_size
    trades_digest = sha256(trades_path.read_bytes()).hexdigest()
    indexed.append({"path": "output/trades.jsonl", "size": trades_size, "sha256": trades_digest})
    index_path = root / "artifact-index.json"
    _write(index_path, {"files": indexed})
    return SavedRowsAuthority(
        Path("saved"),
        "synthetic-source-head",
        sha256(index_path.read_bytes()).hexdigest(),
        trades_size,
        trades_digest,
        len(rows),
        tuple(members),
    )


def test_text_recurses_nested_decimal_lists_without_losing_exact_strings() -> None:
    assert _text(
        {"top_three_positive_r": [Decimal("0.3500"), {"nested": (Decimal("-1.20"),)}]}
    ) == {"top_three_positive_r": ["0.3500", {"nested": ["-1.20"]}]}


def test_saved_export_writes_projection_with_winners_but_negative_total(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    authority = _saved_fixture(repo)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("historical input/simulation boundary was invoked")

    monkeypatch.setattr(screen, "run", forbidden)
    monkeypatch.setattr(screen, "run_member", forbidden)
    monkeypatch.setattr(screen, "AcceptedInput", forbidden)
    monkeypatch.setattr(
        "adaptive_trading_bot.research.failed_breakout_reversion_saved_export._clean_sha",
        lambda *_args, **_kwargs: None,
    )
    output = tmp_path / "output"
    attestation = export_saved_projection(
        repo,
        output,
        exporter_sha="a" * 40,
        authority=authority,
    )

    projection = json.loads((output / "projection.json").read_text())
    report = json.loads((output / "report.json").read_text())
    saved_attestation = json.loads((output / "attestation.json").read_text())
    btc_base = projection["economic_table"]["BTCUSDT:BASE:FULL"]
    assert btc_base["wins"] == 1
    assert btc_base["losses"] == 1
    assert btc_base["top_three_positive_r"] == ["0.35"]
    assert btc_base["modeled_net_pnl_quote"] == "-0.65"
    assert projection["classification"] == "INSUFFICIENT_EVIDENCE"
    assert report["status"] == RECOVERY_STATUS
    assert saved_attestation == _text(attestation)
    assert saved_attestation["validated_row_count"] == 8
