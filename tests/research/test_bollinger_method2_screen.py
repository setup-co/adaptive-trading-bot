import json
import runpy
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, getcontext, localcontext
from pathlib import Path
from typing import Any

from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.research.bollinger_method2_screen import (
    _classify_projection,
    _decimal_difference,
)
from adaptive_trading_bot.research.failed_breakout_reversion_screen import Scenario
from adaptive_trading_bot.strategies.bollinger_method2 import CONFIRMED_ID, CONTROL_ID, _mfi10

CLI = runpy.run_path(
    str(Path(__file__).parents[2] / "scripts" / "research" / "bollinger_method2_screen.py")
)
cli_main = CLI["main"]
CANONICAL_FIXTURE = runpy.run_path(
    str(Path(__file__).with_name("test_trend_impulse_pullback_screen.py"))
)
write_authority = CANONICAL_FIXTURE["_write_authority"]


def _flow_candles(
    prices: tuple[str, ...], volumes: tuple[str, ...] | None = None
) -> tuple[Candle, ...]:
    amounts = volumes or tuple("1" for _ in prices)
    return tuple(
        Candle(
            TradingSymbol("BTC", "USDT"),
            Timeframe.MINUTES_15,
            datetime(2026, 1, 1, tzinfo=UTC) + index * timedelta(minutes=15),
            datetime(2026, 1, 1, tzinfo=UTC) + (index + 1) * timedelta(minutes=15),
            Decimal(price),
            high=Decimal(price),
            low=Decimal(price),
            close=Decimal(price),
            volume=Decimal(volume),
        )
        for index, (price, volume) in enumerate(zip(prices, amounts, strict=True))
    )


def test_mfi10_uses_base_volume_strict_direction_and_nullable_zero_flow() -> None:
    rising = tuple(str(value) for value in range(1, 12))
    assert _mfi10(_flow_candles(rising))[0] == Decimal(100)
    assert _mfi10(_flow_candles(tuple(reversed(rising))))[0] == Decimal(0)
    assert _mfi10(_flow_candles(tuple("10" for _ in range(11))))[0] is None
    mfi, positive, negative = _mfi10(
        _flow_candles(
            ("10", "11", "10", "11", "10", "11", "10", "11", "10", "11", "10"),
            ("1", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"),
        )
    )
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        assert mfi == Decimal(100) * positive / (positive + negative)
    assert positive > 0 and negative > 0


def test_actual_cli_reads_canonical_parquet_and_writes_final_report(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    authority = write_authority(repo)
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(
        ("git", "config", "user.email", "synthetic@example.invalid"), cwd=repo, check=True
    )
    subprocess.run(("git", "config", "user.name", "Synthetic Fixture"), cwd=repo, check=True)
    subprocess.run(("git", "add", "."), cwd=repo, check=True)
    subprocess.run(("git", "commit", "-qm", "synthetic corpus"), cwd=repo, check=True)
    executable = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    output = tmp_path / "output"

    assert (
        cli_main(
            [
                "--repo",
                str(repo),
                "--output",
                str(output),
                "--run-id",
                "synthetic-bb-method2",
                "--executable-sha",
                executable,
            ],
            authority=authority,
        )
        == 0
    )

    projection = json.loads((output / "projection.json").read_text())
    complete = json.loads((output / "complete.json").read_text())
    assert complete["status"] == "COMPLETE"
    assert complete["trade_rows"] > 0
    assert projection["classification"] == "INSUFFICIENT_EVIDENCE"
    for member in projection["members"]:
        control = member["arms"][CONTROL_ID]["counts"]
        confirmed = member["arms"][CONFIRMED_ID]["counts"]
        assert confirmed["signals"] <= control["signals"]
        assert confirmed["admitted"] <= control["admitted"]
    assert all(
        "retained_control" in value and "excluded_control" in value
        for value in projection["selection_comparison"].values()
    )


def test_derived_difference_ignores_ambient_decimal_precision() -> None:
    original = getcontext().prec
    try:
        getcontext().prec = 3
        assert _decimal_difference(
            "123456789.123456789123456789", "123456789.123456789123456788"
        ) == Decimal("0.000000000000000001")
    finally:
        getcontext().prec = original


def _classification_inputs(
    *,
    mean: Decimal = Decimal("0.25"),
    profit_factor_status: str = "NUMERIC",
    profit_factor: Decimal | None = Decimal("2"),
) -> tuple[dict[str, Any], dict[str, Any]]:
    table: dict[str, Any] = {}
    blocks: dict[str, Any] = {}
    for wire in ("BTCUSDT", "ETHUSDT"):
        for scenario in Scenario:
            for view in ("FULL", "COMMON_SPACED"):
                table[f"{wire}:{scenario.value}:{CONFIRMED_ID}:{view}"] = {
                    "closed": 30,
                    "mean_net_r": mean,
                    "profit_factor_status": profit_factor_status,
                    "profit_factor": profit_factor,
                    "unresolved": 0,
                }
        for index, block in enumerate(("BLOCK_1", "BLOCK_2", "BLOCK_3")):
            blocks[f"{wire}:BASE:{CONFIRMED_ID}:{block}"] = {
                "mean_net_r": Decimal("0.1") if index < 2 else Decimal("-0.1")
            }
    return table, blocks


def test_classifier_accepts_numeric_pf_with_exactly_two_positive_base_blocks() -> None:
    table, blocks = _classification_inputs()
    assert _classify_projection(table, blocks, ("BTCUSDT", "ETHUSDT")) == "REVIEW_ONLY_PRE_FUNDING"


def test_classifier_accepts_explicit_zero_loss_positive_profit_factor() -> None:
    table, blocks = _classification_inputs(
        profit_factor_status="UNBOUNDED_ZERO_LOSS", profit_factor=None
    )
    assert _classify_projection(table, blocks, ("BTCUSDT", "ETHUSDT")) == "REVIEW_ONLY_PRE_FUNDING"


def test_classifier_preserves_negative_mixed_sparse_and_unresolved_contracts() -> None:
    negative, blocks = _classification_inputs(mean=Decimal("-0.1"))
    assert _classify_projection(negative, blocks, ("BTCUSDT", "ETHUSDT")) == "NEGATIVE_ADAPTATION"

    mixed, blocks = _classification_inputs(mean=Decimal("-0.1"))
    mixed[f"BTCUSDT:BASE:{CONFIRMED_ID}:FULL"]["mean_net_r"] = Decimal("0.1")
    assert _classify_projection(mixed, blocks, ("BTCUSDT", "ETHUSDT")) == "MIXED_FRAGILE"

    sparse, blocks = _classification_inputs()
    sparse[f"ETHUSDT:STRESS:{CONFIRMED_ID}:COMMON_SPACED"]["closed"] = 29
    assert _classify_projection(sparse, blocks, ("BTCUSDT", "ETHUSDT")) == "INSUFFICIENT_EVIDENCE"

    unresolved, blocks = _classification_inputs()
    unresolved[f"BTCUSDT:BASE:{CONFIRMED_ID}:FULL"]["unresolved"] = 1
    assert (
        _classify_projection(unresolved, blocks, ("BTCUSDT", "ETHUSDT"))
        == "INCOMPLETE_ECONOMIC_EVIDENCE"
    )
