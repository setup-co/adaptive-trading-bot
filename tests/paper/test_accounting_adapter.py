from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from adaptive_trading_bot.accounting import (
    PositionAccountingCore,
    PositionAccountingFillBatch,
)
from adaptive_trading_bot.backtest import (
    MarketFill,
    OrderSide,
    market_fill_to_position_accounting_fill,
)
from adaptive_trading_bot.domain import Timeframe, TradingSymbol
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.orders import EntryOrderIntentIdentity, EntrySide
from adaptive_trading_bot.paper import (
    PaperFillAccountingAdapterError,
    PaperMarketFill,
    paper_fill_to_position_accounting_fill,
)
from adaptive_trading_bot.storage import SourceId

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
BTC = TradingSymbol("BTC", "USDT")
ETH = TradingSymbol("ETH", "USDT")
SOURCE = SourceId("archive")


def paper_fill(
    *,
    strategy_id: str = "trend-pullback",
    side: EntrySide = EntrySide.BUY,
    quantity: Decimal = Decimal(1),
    fill_at: datetime = NOW,
) -> PaperMarketFill:
    decision = DecisionIdentity(SOURCE, BTC, fill_at, strategy_id, "1.0.0")
    reference = Decimal(100)
    fill_price = Decimal("100.25") if side is EntrySide.BUY else Decimal("99.75")
    fee = quantity * fill_price * Decimal(20) / Decimal(10000)
    return PaperMarketFill(
        intent_identity=EntryOrderIntentIdentity(decision),
        decision_identity=decision,
        source_id=SOURCE,
        symbol=BTC,
        side=side,
        quantity=quantity,
        authorized_at=fill_at,
        eligible_at=fill_at,
        fill_at=fill_at,
        reference_price=reference,
        spread_bps=Decimal(10),
        slippage_bps=Decimal(20),
        fill_price=fill_price,
        fee_bps=Decimal(20),
        fee_quote=fee,
        risk_policy_version="risk-v1",
        safety_policy_version="safety-v1",
    )


def backtest_fill(item: PaperMarketFill, order_id: str) -> MarketFill:
    return MarketFill(
        order_id=order_id,
        symbol=item.symbol,
        side=OrderSide(item.side.value),
        quantity=item.quantity,
        created_at=item.authorized_at,
        eligible_at=item.eligible_at,
        execution_candle_open_time=item.fill_at,
        fill_at=item.fill_at,
        reference_open_price=item.reference_price,
        spread_bps=item.spread_bps,
        slippage_bps=item.slippage_bps,
        fill_price=item.fill_price,
        fee_bps=item.fee_bps,
        fee_quote=item.fee_quote,
        source_id=SOURCE,
        execution_timeframe=Timeframe.MINUTES_15,
    )


def accounting_values(item: object) -> tuple[object, ...]:
    return tuple(
        getattr(item, name)
        for name in ("symbol", "side", "quantity", "fill_at", "fill_price", "fee_quote")
    )


def test_equivalent_backtest_and_paper_fills_adapt_without_value_changes() -> None:
    paper = paper_fill()
    backtest = backtest_fill(paper, "backtest-one")

    adapted_paper = paper_fill_to_position_accounting_fill(paper)
    adapted_backtest = market_fill_to_position_accounting_fill(backtest)

    assert accounting_values(adapted_paper) == accounting_values(adapted_backtest)


def test_equivalent_fill_sequences_produce_identical_shared_position_arithmetic() -> None:
    paper_stream = (
        paper_fill(quantity=Decimal(2)),
        paper_fill(
            strategy_id="mean-reversion",
            side=EntrySide.SELL,
            quantity=Decimal(2),
            fill_at=NOW + timedelta(minutes=1),
        ),
    )
    backtest_stream = tuple(
        backtest_fill(item, f"backtest-{index}") for index, item in enumerate(paper_stream)
    )
    paper_core = PositionAccountingCore()
    backtest_core = PositionAccountingCore()

    for paper_item in paper_stream:
        adapted = paper_fill_to_position_accounting_fill(paper_item)
        paper_core.process(PositionAccountingFillBatch(adapted.fill_at, (adapted,)))
    for backtest_item in backtest_stream:
        adapted = market_fill_to_position_accounting_fill(backtest_item)
        backtest_core.process(PositionAccountingFillBatch(adapted.fill_at, (adapted,)))

    assert paper_core.position(BTC) == backtest_core.position(BTC)


def test_paper_fill_id_is_stable_and_distinct_for_distinct_intent_identities() -> None:
    first = paper_fill()
    repeated = paper_fill()
    distinct = paper_fill(strategy_id="mean-reversion")

    first_id = paper_fill_to_position_accounting_fill(first).fill_id
    assert first_id == paper_fill_to_position_accounting_fill(repeated).fill_id
    assert first_id != paper_fill_to_position_accounting_fill(distinct).fill_id


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("quantity", Decimal(2)),
        ("side", EntrySide.SELL),
        ("fill_price", Decimal(999)),
        ("fee_quote", Decimal(999)),
        ("source_id", SourceId("other")),
        ("symbol", ETH),
        ("risk_policy_version", " forged "),
    ],
)
def test_raw_mutated_paper_fill_fields_fail_closed(field_name: str, value: object) -> None:
    item = paper_fill()
    object.__setattr__(item, field_name, value)

    with pytest.raises(PaperFillAccountingAdapterError, match="inconsistent"):
        paper_fill_to_position_accounting_fill(item)


def test_raw_mutated_paper_fill_identity_inconsistency_fails_closed() -> None:
    item = paper_fill()
    forged = replace(item.decision_identity, strategy_id="forged")
    object.__setattr__(item, "decision_identity", forged)

    with pytest.raises(PaperFillAccountingAdapterError, match="inconsistent"):
        paper_fill_to_position_accounting_fill(item)
