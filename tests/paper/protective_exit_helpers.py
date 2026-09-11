import runpy
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from adaptive_trading_bot.admission import PortfolioAdmissionCandidate
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.execution import SimulatedExecutionCostConfiguration
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, EntrySide
from adaptive_trading_bot.paper import (
    PaperExecutionObservation,
    PaperOpenPositionSnapshot,
    PaperPortfolioCoordinator,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyGate,
    RiskManager,
    RiskManagerConfiguration,
    RiskPortfolioSnapshot,
    RiskPositionExposure,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import CandidateAction

_JOURNAL_HELPERS = runpy.run_path(
    str(Path(__file__).parents[1] / "journal" / "test_decision_journal.py")
)
NOW: datetime = _JOURNAL_HELPERS["NOW"]
BTC: TradingSymbol = _JOURNAL_HELPERS["BTC"]
ETH: TradingSymbol = _JOURNAL_HELPERS["ETH"]
SOURCE = SourceId("archive")
OTHER_SOURCE = SourceId("other-archive")
_context = _JOURNAL_HELPERS["_context"]
_manual_evaluation = _JOURNAL_HELPERS["_manual_evaluation"]
_health = _JOURNAL_HELPERS["_health"]


def candidate(
    *,
    observed_at: datetime = NOW,
    symbol: TradingSymbol = BTC,
    source_id: SourceId = SOURCE,
    action: CandidateAction = CandidateAction.LONG_CANDIDATE,
    positions: tuple[RiskPositionExposure, ...] = (),
) -> PortfolioAdmissionCandidate[object, object]:
    context = _context(source_id=source_id, symbol=symbol, observed_at=observed_at)
    evaluation = _manual_evaluation(
        context,
        strategy_id=f"manual-{source_id.value}-{symbol.base}",
        action=action,
        invalidation=(Decimal(135) if action is CandidateAction.SHORT_CANDIDATE else Decimal(75)),
    )
    risk = RiskManager(RiskManagerConfiguration()).evaluate(
        evaluation,
        RiskPortfolioSnapshot(
            observed_at,
            "USDT",
            Decimal(10000),
            Decimal(10000),
            Decimal(10000),
            positions,
        ),
    )
    entry = DecisionJournalEntry(
        context,
        evaluation,
        risk,
        OperationalSafetyGate().evaluate(risk, _health(risk)),
    )
    intent = EntryOrderIntentAuthorizer().authorize(entry).intent
    assert intent is not None
    return PortfolioAdmissionCandidate(entry, intent)


def coordinator(*, latency: timedelta = timedelta(0)) -> PaperPortfolioCoordinator:
    return PaperPortfolioCoordinator(
        SimulatedExecutionCostConfiguration(
            spread_bps=Decimal(2),
            slippage_bps=Decimal(3),
            fee_bps=Decimal(4),
            latency=latency,
        )
    )


def observation(
    symbol: TradingSymbol,
    observed_at: datetime,
    price: Decimal,
    *,
    source_id: SourceId = SOURCE,
) -> PaperExecutionObservation:
    return PaperExecutionObservation(source_id, symbol, observed_at, price)


def open_positions(
    paper: PaperPortfolioCoordinator,
    *items: PortfolioAdmissionCandidate[object, object],
) -> None:
    paper.start_batch(tuple(items))
    paper.observe_batch(tuple(observation(item.intent.symbol, NOW, Decimal(100)) for item in items))


def risk_position(item: PaperOpenPositionSnapshot) -> RiskPositionExposure:
    return RiskPositionExposure(
        item.symbol,
        item.quantity if item.side is EntrySide.BUY else -item.quantity,
        item.locked_gross_exposure_quote,
        item.locked_open_risk_quote,
    )
