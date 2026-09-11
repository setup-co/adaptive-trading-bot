import ast
import runpy
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, getcontext, setcontext
from pathlib import Path

import pytest

import adaptive_trading_bot.features.live_adapter as feature_adapter_module
import adaptive_trading_bot.paper.live_adapter as paper_adapter_module
import adaptive_trading_bot.risk.live_adapter as safety_adapter_module
from adaptive_trading_bot.admission import PortfolioAdmissionCandidate
from adaptive_trading_bot.domain import Candle, Timeframe, TradingSymbol
from adaptive_trading_bot.execution import SimulatedExecutionCostConfiguration
from adaptive_trading_bot.features import (
    FeatureBatchError,
    FeatureEngine,
    observed_candles_from_live,
    observed_candles_from_replay,
)
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.market_data import (
    LiveClosedCandleObservation,
    LiveMarketDataValidationError,
    LiveMarketFeedSnapshot,
    LiveMarketQuote,
    LiveMarketRequestError,
    LiveMarketRequestKind,
)
from adaptive_trading_bot.orders import (
    EntryOrderIntentAuthorizer,
    OrderIntentAuthorizationStatus,
)
from adaptive_trading_bot.paper import (
    LivePaperObservationAssemblyError,
    PaperPortfolioCoordinator,
    PaperPortfolioStartBatchStatus,
    paper_observation_from_live_quote,
)
from adaptive_trading_bot.replay import ReplayBatch, ReplayEvent
from adaptive_trading_bot.risk import (
    LiveOperationalHealthAssemblyError,
    LiveOperationalHealthInputs,
    OperationalSafetyDecision,
    OperationalSafetyGate,
    OperationalSafetyReasonCode,
    OperationalSafetyStatus,
    RiskManager,
    RiskPortfolioSnapshot,
    VolatilitySafetyState,
    operational_health_from_live_quote,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import CandidateAction

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
SOURCE = SourceId("ccxt-fake-spot")
BTC = TradingSymbol("BTC", "USDT")
_HELPERS = runpy.run_path(str(Path(__file__).parents[1] / "journal" / "test_decision_journal.py"))
_context = _HELPERS["_context"]
_manual_evaluation = _HELPERS["_manual_evaluation"]


class FakePublicSource:
    """Finite fake implementing only the public source operations used by this proof."""

    def __init__(self) -> None:
        self.fail_ticker = False

    @property
    def source_id(self) -> SourceId:
        return SourceId(SOURCE.value)

    def fetch_quote(
        self,
        symbol: TradingSymbol,
        *,
        observed_at: datetime,
        bid: Decimal = Decimal("104.95"),
        ask: Decimal = Decimal("105.05"),
        source_timestamp: datetime | None = None,
    ) -> LiveMarketQuote:
        if self.fail_ticker:
            raise LiveMarketRequestError(
                source_id=self.source_id,
                symbol=symbol,
                observed_at=observed_at,
                request_kind=LiveMarketRequestKind.TICKER,
            )
        return LiveMarketQuote(
            self.source_id,
            symbol,
            observed_at,
            observed_at - timedelta(seconds=1) if source_timestamp is None else source_timestamp,
            bid,
            ask,
            Decimal("105.01"),
        )


def candle(index: int = 0) -> Candle:
    open_time = NOW - timedelta(minutes=15) + index * Timeframe.MINUTES_15.duration
    value = Decimal(100 + index)
    return Candle(
        BTC,
        Timeframe.MINUTES_15,
        open_time,
        open_time + Timeframe.MINUTES_15.duration,
        value,
        value + Decimal(2),
        value - Decimal(2),
        value + Decimal(1),
        Decimal(10),
    )


def live_snapshot(item: Candle) -> LiveMarketFeedSnapshot:
    return LiveMarketFeedSnapshot(
        item.close_time,
        closed_candles=(LiveClosedCandleObservation(SOURCE, item, item.close_time),),
    )


def health_inputs() -> LiveOperationalHealthInputs:
    return LiveOperationalHealthInputs(
        time_sync_offset_ms=Decimal(0),
        volatility_safety_state=VolatilitySafetyState.CLASSIFIED_NORMAL,
        suspicious_price_anomaly=False,
        consecutive_order_rejections=0,
    )


def risk_and_entry(
    quote: LiveMarketQuote,
) -> tuple[OperationalSafetyDecision, DecisionJournalEntry[object, object]]:
    context = _context(
        source_id=quote.source_id, symbol=quote.symbol, observed_at=quote.observed_at
    )
    evaluation = _manual_evaluation(
        context,
        action=CandidateAction.LONG_CANDIDATE,
        invalidation=Decimal(75),
    )
    risk = RiskManager().evaluate(
        evaluation,
        RiskPortfolioSnapshot(
            quote.observed_at,
            "USDT",
            Decimal(10000),
            Decimal(10000),
            Decimal(10000),
        ),
    )
    health = operational_health_from_live_quote(quote, health_inputs())
    safety = OperationalSafetyGate().evaluate(risk, health)
    return safety, DecisionJournalEntry(context, evaluation, risk, safety)


def paper() -> PaperPortfolioCoordinator:
    return PaperPortfolioCoordinator(
        SimulatedExecutionCostConfiguration(
            spread_bps=Decimal(2),
            slippage_bps=Decimal(3),
            fee_bps=Decimal(4),
            latency=timedelta(0),
        )
    )


def test_live_and_replay_use_equal_feature_input_and_engine_results() -> None:
    item = candle()
    live_batch = observed_candles_from_live(live_snapshot(item))
    replay_batch = observed_candles_from_replay(
        ReplayBatch(item.close_time, (ReplayEvent(SOURCE, item),))
    )

    assert live_batch == replay_batch
    assert FeatureEngine().process(live_batch) == FeatureEngine().process(replay_batch)


def test_empty_live_candle_snapshot_fails_without_fabricating_feature_event() -> None:
    with pytest.raises(FeatureBatchError, match="at least one"):
        observed_candles_from_live(LiveMarketFeedSnapshot(NOW))


def test_malformed_live_candle_fails_before_feature_state_advances() -> None:
    engine = FeatureEngine()
    assert (
        engine.process(observed_candles_from_live(live_snapshot(candle()))).snapshots[0].bars_seen
        == 1
    )
    malformed = live_snapshot(candle(1))
    object.__setattr__(malformed.closed_candles[0].candle, "close", Decimal(999))

    with pytest.raises(LiveMarketDataValidationError, match="changed"):
        observed_candles_from_live(malformed)

    valid_result = engine.process(observed_candles_from_live(live_snapshot(candle(1))))
    assert valid_result.snapshots[0].bars_seen == 2


def test_feature_translation_is_detached_from_later_caller_mutation() -> None:
    snapshot = live_snapshot(candle())
    translated = observed_candles_from_live(snapshot)
    object.__setattr__(snapshot.closed_candles[0].source_id, "value", "forged")
    object.__setattr__(snapshot.closed_candles[0].candle, "close", Decimal(999))

    assert translated.events[0].series.source_id == SOURCE
    assert translated.events[0].candle.close == Decimal(101)


def test_quote_maps_exact_midpoint_to_detached_paper_observation() -> None:
    quote = FakePublicSource().fetch_quote(BTC, observed_at=NOW)
    original = getcontext().copy()
    try:
        setcontext(Context(prec=4))
        low_precision = paper_observation_from_live_quote(quote)
        setcontext(Context(prec=50))
        high_precision = paper_observation_from_live_quote(quote)
    finally:
        setcontext(original)

    assert low_precision == high_precision
    assert low_precision.reference_price == quote.midpoint == Decimal("105.00")
    assert low_precision.reference_price not in {quote.bid, quote.ask, quote.last}
    object.__setattr__(quote.source_id, "value", "forged")
    assert low_precision.source_id == SOURCE


def test_health_preserves_exact_feed_facts_and_explicit_non_feed_facts() -> None:
    source_timestamp = NOW - timedelta(seconds=37)
    quote = FakePublicSource().fetch_quote(
        BTC,
        observed_at=NOW,
        bid=Decimal("104.99"),
        ask=Decimal("105.01"),
        source_timestamp=source_timestamp,
    )
    inputs = LiveOperationalHealthInputs(
        Decimal("-12.5"), VolatilitySafetyState.CLASSIFIED_HIGH, True, 2
    )

    health = operational_health_from_live_quote(quote, inputs)

    assert health.source_id == quote.source_id
    assert health.symbol == quote.symbol
    assert health.observed_at == quote.observed_at
    assert health.market_data_observed_at == source_timestamp
    assert health.spread_bps == quote.spread_bps
    assert health.time_sync_offset_ms == Decimal("-12.5")
    assert health.volatility_safety_state is VolatilitySafetyState.CLASSIFIED_HIGH
    assert health.suspicious_price_anomaly
    assert health.consecutive_order_rejections == 2


def test_missing_source_timestamp_fails_before_health_can_be_created() -> None:
    quote = LiveMarketQuote(SOURCE, BTC, NOW, None, Decimal("104.99"), Decimal("105.01"), None)

    with pytest.raises(LiveOperationalHealthAssemblyError, match="source timestamp"):
        operational_health_from_live_quote(quote, health_inputs())
    with pytest.raises(LivePaperObservationAssemblyError, match="source timestamp"):
        paper_observation_from_live_quote(quote)


@pytest.mark.parametrize(
    ("source_timestamp", "bid", "ask", "reason"),
    (
        (
            NOW - timedelta(seconds=121),
            Decimal("104.99"),
            Decimal("105.01"),
            OperationalSafetyReasonCode.STALE_MARKET_DATA,
        ),
        (
            NOW + timedelta(microseconds=1),
            Decimal("104.99"),
            Decimal("105.01"),
            OperationalSafetyReasonCode.FUTURE_MARKET_DATA_TIMESTAMP,
        ),
        (
            NOW - timedelta(seconds=1),
            Decimal(100),
            Decimal(101),
            OperationalSafetyReasonCode.SPREAD_TOO_WIDE,
        ),
    ),
)
def test_existing_safety_gate_blocks_live_stale_future_and_wide_quotes(
    source_timestamp: datetime,
    bid: Decimal,
    ask: Decimal,
    reason: OperationalSafetyReasonCode,
) -> None:
    quote = FakePublicSource().fetch_quote(
        BTC,
        observed_at=NOW,
        bid=bid,
        ask=ask,
        source_timestamp=source_timestamp,
    )
    safety, entry = risk_and_entry(quote)
    authorization = EntryOrderIntentAuthorizer().authorize(entry)
    coordinator = paper()

    assert safety.status is OperationalSafetyStatus.SAFE_MODE
    assert reason in safety.reasons
    assert authorization.status is OrderIntentAuthorizationStatus.NO_INTENT
    assert authorization.intent is None
    assert coordinator.snapshot().started == ()


def test_live_health_identity_mismatch_cannot_create_entry_authority() -> None:
    quote = FakePublicSource().fetch_quote(BTC, observed_at=NOW)
    context = _context(
        source_id=SourceId("different-public-source"),
        symbol=BTC,
        observed_at=NOW,
    )
    evaluation = _manual_evaluation(context, invalidation=Decimal(75))
    risk = RiskManager().evaluate(
        evaluation,
        RiskPortfolioSnapshot(
            NOW,
            "USDT",
            Decimal(10000),
            Decimal(10000),
            Decimal(10000),
        ),
    )
    health = operational_health_from_live_quote(quote, health_inputs())
    safety = OperationalSafetyGate().evaluate(risk, health)
    entry = DecisionJournalEntry(context, evaluation, risk, safety)

    authorization = EntryOrderIntentAuthorizer().authorize(entry)

    assert safety.status is OperationalSafetyStatus.SAFE_MODE
    assert OperationalSafetyReasonCode.HEALTH_IDENTITY_MISMATCH in safety.reasons
    assert authorization.status is OrderIntentAuthorizationStatus.NO_INTENT
    assert authorization.intent is None


def test_healthy_live_quote_uses_existing_authority_and_paper_pricing_chain() -> None:
    source = FakePublicSource()
    entry_quote = source.fetch_quote(BTC, observed_at=NOW)
    safety, entry = risk_and_entry(entry_quote)
    authorization = EntryOrderIntentAuthorizer().authorize(entry)
    assert authorization.intent is not None
    candidate = PortfolioAdmissionCandidate(entry, authorization.intent)
    coordinator = paper()

    started = coordinator.start_batch((candidate,))
    later_quote = source.fetch_quote(
        BTC,
        observed_at=NOW + timedelta(seconds=1),
        bid=Decimal("105.95"),
        ask=Decimal("106.05"),
    )
    observation = paper_observation_from_live_quote(later_quote)
    filled = coordinator.observe_batch((observation,))

    assert safety.status is OperationalSafetyStatus.CLEARED
    assert started.status is PaperPortfolioStartBatchStatus.STARTED
    assert observation.reference_price == Decimal("106.00")
    assert filled.newly_filled[0].reference_price == observation.reference_price
    assert filled.newly_filled[0].fill_price == Decimal("106.042400")
    assert filled.newly_filled[0].fill_price not in {
        later_quote.bid,
        later_quote.ask,
        later_quote.last,
    }
    assert filled.newly_filled[0].quantity == authorization.intent.quantity


def test_public_request_failure_creates_no_bridge_value_or_paper_mutation() -> None:
    source = FakePublicSource()
    source.fail_ticker = True
    coordinator = paper()
    before = coordinator.snapshot()

    with pytest.raises(LiveMarketRequestError):
        quote = source.fetch_quote(BTC, observed_at=NOW)
        operational_health_from_live_quote(quote, health_inputs())
        coordinator.observe_batch((paper_observation_from_live_quote(quote),))

    assert coordinator.snapshot() == before


@pytest.mark.parametrize("field_name", ("source", "symbol", "observed_at", "midpoint"))
def test_raw_quote_identity_time_and_arithmetic_drift_fail_before_translation(
    field_name: str,
) -> None:
    quote = FakePublicSource().fetch_quote(BTC, observed_at=NOW)
    if field_name == "source":
        object.__setattr__(quote.source_id, "value", "forged")
    elif field_name == "symbol":
        object.__setattr__(quote, "symbol", TradingSymbol("ETH", "USDT"))
    elif field_name == "observed_at":
        object.__setattr__(quote, "observed_at", NOW + timedelta(seconds=1))
    else:
        object.__setattr__(quote, "midpoint", Decimal(999))

    with pytest.raises((LiveMarketDataValidationError, LiveOperationalHealthAssemblyError)):
        operational_health_from_live_quote(quote, health_inputs())
    with pytest.raises(LivePaperObservationAssemblyError):
        paper_observation_from_live_quote(quote)


def test_bridge_modules_contain_no_strategy_risk_or_pricing_reimplementation() -> None:
    forbidden_symbols = {
        "RiskManager",
        "OperationalSafetyGate",
        "EntryOrderIntentAuthorizer",
        "PortfolioAdmissionGate",
        "PaperOrderSimulator",
        "price_simulated_market_fill",
    }
    for module in (feature_adapter_module, paper_adapter_module, safety_adapter_module):
        tree = ast.parse(Path(module.__file__ or "").read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert names.isdisjoint(forbidden_symbols)
