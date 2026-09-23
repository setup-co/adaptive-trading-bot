"""Sealed causal Breakout + Retest decision corpus for research only."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from hmac import compare_digest
from json import dumps

from adaptive_trading_bot.domain import FrozenClock, Timeframe, TradingSymbol, align_timestamp
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.features import (
    FeatureEngine,
    MarketStructureEngine,
    observed_candles_from_replay,
)
from adaptive_trading_bot.historical_sources import is_locked_weex_source
from adaptive_trading_bot.journal import (
    DecisionIdentity,
    DecisionJournalBatch,
    DecisionJournalEntry,
    InMemoryDecisionJournal,
)
from adaptive_trading_bot.regime import MultiTimeframeRegimeComposer, TechnicalRegimeEngine
from adaptive_trading_bot.replay import (
    HistoricalReplaySource,
    PacedReplayController,
    ReplayEngine,
    ReplaySeries,
    ReplaySpeed,
)
from adaptive_trading_bot.research.paired_exit_replay import HistoricalStrategyContextObservation
from adaptive_trading_bot.research.trend_pullback_corpus import (
    ResearchDecisionCategory,
    ResearchSafetyAssumptionProfile,
    _canonical_integrity_value,
    _decision_category,
    _integrity_seal,
    _NoWaitReplayTimer,
    _research_health,
    _revalidate_risk_configuration,
    _revalidate_safety_configuration,
    _validate_safety_assumptions,
)
from adaptive_trading_bot.research.weex_historical_corpus import LockedWeexResearchCorpusStore
from adaptive_trading_bot.risk import (
    OperationalSafetyConfiguration,
    OperationalSafetyGate,
    RiskManager,
    RiskManagerConfiguration,
    RiskPortfolioSnapshot,
)
from adaptive_trading_bot.storage import HistoricalCandleStore, SourceId
from adaptive_trading_bot.strategies import (
    BreakoutRetestConfiguration,
    BreakoutRetestStrategy,
    assemble_strategy_contexts,
)

_SCHEMA = "breakout-retest-research-corpus-v1"
_TIMEFRAMES = (Timeframe.MINUTES_15, Timeframe.HOUR_1, Timeframe.HOURS_4)


class BreakoutRetestResearchError(ValueError):
    """A corpus, declared admission, or causal authority is inconsistent."""


@dataclass(frozen=True, slots=True)
class BreakoutRetestResearchConfiguration:
    source_ids: tuple[SourceId, ...]
    symbols: tuple[TradingSymbol, ...]
    start: datetime
    end: datetime
    research_equity_quote: Decimal
    risk_manager_configuration: RiskManagerConfiguration = field(
        default_factory=RiskManagerConfiguration
    )
    safety_configuration: OperationalSafetyConfiguration = field(
        default_factory=OperationalSafetyConfiguration
    )
    safety_assumptions: ResearchSafetyAssumptionProfile = field(
        default_factory=ResearchSafetyAssumptionProfile
    )
    strategy_configuration: BreakoutRetestConfiguration = field(
        default_factory=BreakoutRetestConfiguration
    )
    schema_version: str = field(init=False, default=_SCHEMA)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_ids", tuple(sorted(deepcopy(self.source_ids), key=lambda x: x.value))
        )
        object.__setattr__(self, "symbols", tuple(sorted(deepcopy(self.symbols), key=str)))
        object.__setattr__(self, "start", require_utc(self.start, field="research start"))
        object.__setattr__(self, "end", require_utc(self.end, field="research end"))
        object.__setattr__(
            self, "risk_manager_configuration", deepcopy(self.risk_manager_configuration)
        )
        object.__setattr__(self, "safety_configuration", deepcopy(self.safety_configuration))
        object.__setattr__(self, "safety_assumptions", deepcopy(self.safety_assumptions))
        object.__setattr__(self, "strategy_configuration", deepcopy(self.strategy_configuration))
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "breakout-retest-research-configuration",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        )

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "breakout-retest-research-configuration",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        ):
            raise BreakoutRetestResearchError("research configuration integrity mismatch")
        self._validate_facts()

    def _validate_facts(self) -> None:
        if self.schema_version != _SCHEMA or self.start >= self.end:
            raise BreakoutRetestResearchError("research configuration schema or range is invalid")
        if not self.source_ids or len(set(self.source_ids)) != len(self.source_ids):
            raise BreakoutRetestResearchError("research sources must be unique and non-empty")
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise BreakoutRetestResearchError("research symbols must be unique and non-empty")
        if any(
            symbol.base not in {"BTC", "ETH"} or symbol.quote != "USDT" for symbol in self.symbols
        ):
            raise BreakoutRetestResearchError("only V1 BTC/ETH USDT research is allowed")
        if any(
            align_timestamp(boundary, timeframe) != boundary
            for boundary in (self.start, self.end)
            for timeframe in _TIMEFRAMES
        ):
            raise BreakoutRetestResearchError("research range must align to V1 timeframes")
        if (
            not isinstance(self.research_equity_quote, Decimal)
            or not self.research_equity_quote.is_finite()
            or self.research_equity_quote <= 0
        ):
            raise BreakoutRetestResearchError("research equity must be positive and finite")
        if self.strategy_configuration != BreakoutRetestConfiguration():
            raise BreakoutRetestResearchError("only default Breakout + Retest V1 is predeclared")
        _revalidate_risk_configuration(self.risk_manager_configuration)
        _revalidate_safety_configuration(self.safety_configuration)
        _validate_safety_assumptions(self.safety_assumptions)


@dataclass(frozen=True, slots=True)
class BreakoutRetestDecisionObservation:
    decision: DecisionJournalEntry[object, object]
    category: ResearchDecisionCategory = field(init=False)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        decision = deepcopy(self.decision)
        decision.validate()
        object.__setattr__(self, "decision", decision)
        object.__setattr__(self, "category", _decision_category(decision))
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "breakout-retest-decision-observation",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        )

    @property
    def identity(self) -> DecisionIdentity:
        return self.decision.identity

    @property
    def is_research_admissible_entry(self) -> bool:
        return self.category is ResearchDecisionCategory.RESEARCH_ADMISSIBLE

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "breakout-retest-decision-observation",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        ):
            raise BreakoutRetestResearchError("decision observation integrity mismatch")
        self.decision.validate()
        if self.category is not _decision_category(self.decision):
            raise BreakoutRetestResearchError("decision category contradicts authority")


@dataclass(frozen=True, slots=True)
class BreakoutRetestHistoricalResearchCorpus:
    configuration: BreakoutRetestResearchConfiguration
    decisions: tuple[BreakoutRetestDecisionObservation, ...]
    historical_contexts: tuple[HistoricalStrategyContextObservation, ...]
    research_admissible_entry_identities: tuple[DecisionIdentity, ...] = field(init=False)
    corpus_digest: str = field(init=False)
    corpus_id: str = field(init=False)
    schema_version: str = field(init=False, default=_SCHEMA)
    is_trading_authority: bool = field(init=False, default=False)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration", deepcopy(self.configuration))
        object.__setattr__(self, "decisions", deepcopy(self.decisions))
        object.__setattr__(self, "historical_contexts", deepcopy(self.historical_contexts))
        identities = tuple(
            item.identity for item in self.decisions if item.is_research_admissible_entry
        )
        object.__setattr__(self, "research_admissible_entry_identities", identities)
        digest = _integrity_seal(
            "breakout-retest-corpus-digest",
            (
                self.configuration,
                self.decisions,
                self.historical_contexts,
            ),
            excluded_names=frozenset(),
        )
        object.__setattr__(self, "corpus_digest", digest)
        object.__setattr__(self, "corpus_id", f"breakout-retest-research-corpus:{digest}")
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "breakout-retest-research-corpus",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        )

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "breakout-retest-research-corpus",
                self,
                excluded_names=frozenset({"integrity_seal"}),
            ),
        ):
            raise BreakoutRetestResearchError("research corpus integrity mismatch")
        self._validate_facts()

    def _validate_facts(self) -> None:
        self.configuration.validate()
        if self.schema_version != _SCHEMA or self.is_trading_authority:
            raise BreakoutRetestResearchError("corpus schema or authority claim is invalid")
        if len(self.decisions) != len(self.historical_contexts):
            raise BreakoutRetestResearchError("decision/context population differs")
        strategy = BreakoutRetestStrategy()
        previous: tuple[datetime, str, str] | None = None
        for item, sealed_context in zip(self.decisions, self.historical_contexts, strict=True):
            item.validate()
            sealed_context.validate()
            decision = item.decision
            context = sealed_context.context
            key = (
                decision.identity.observed_at,
                decision.identity.source_id.value,
                str(decision.identity.symbol),
            )
            if previous is not None and key <= previous:
                raise BreakoutRetestResearchError("decisions are not canonical and unique")
            previous = key
            if (
                context != decision.context
                or decision.identity.source_id not in self.configuration.source_ids
                or decision.identity.symbol not in self.configuration.symbols
                or not self.configuration.start < key[0] <= self.configuration.end
                or decision.identity.strategy_id != strategy.strategy_id
                or decision.identity.strategy_version != strategy.strategy_version
            ):
                raise BreakoutRetestResearchError("decision context or provenance differs")
            expected = strategy.evaluate(context)
            if expected != decision.strategy_evaluation:
                raise BreakoutRetestResearchError("stateful strategy evaluation differs")
            equity = self.configuration.research_equity_quote
            portfolio = RiskPortfolioSnapshot(
                observed_at=key[0],
                quote_currency="USDT",
                account_equity_quote=equity,
                day_start_equity_quote=equity,
                peak_equity_quote=equity,
                positions=(),
            )
            risk = RiskManager(self.configuration.risk_manager_configuration).evaluate(
                expected,
                portfolio,
            )
            safety = OperationalSafetyGate(self.configuration.safety_configuration).evaluate(
                risk,
                _research_health(context, self.configuration.safety_assumptions),
            )
            if risk != decision.risk_decision or safety != decision.safety_decision:
                raise BreakoutRetestResearchError("Risk or Safety authority differs")
        identities = tuple(
            item.identity for item in self.decisions if item.is_research_admissible_entry
        )
        if identities != self.research_admissible_entry_identities:
            raise BreakoutRetestResearchError("admissible identities differ")
        digest = _integrity_seal(
            "breakout-retest-corpus-digest",
            (
                self.configuration,
                self.decisions,
                self.historical_contexts,
            ),
            excluded_names=frozenset(),
        )
        if (
            self.corpus_digest != digest
            or self.corpus_id != f"breakout-retest-research-corpus:{digest}"
        ):
            raise BreakoutRetestResearchError("corpus digest differs")

    def contexts_after_decision(
        self,
        identity: DecisionIdentity,
    ) -> tuple[HistoricalStrategyContextObservation, ...]:
        self.validate()
        if identity not in self.research_admissible_entry_identities:
            raise BreakoutRetestResearchError("decision is not research-admissible")
        return tuple(
            deepcopy(item)
            for item in self.historical_contexts
            if item.context.source_id == identity.source_id
            and item.context.symbol == identity.symbol
            and item.context.observed_at > identity.observed_at
        )

    def to_json(self) -> str:
        self.validate()
        return dumps(
            _canonical_integrity_value(self, excluded_names=frozenset()),
            sort_keys=True,
            separators=(",", ":"),
        )


class BreakoutRetestHistoricalCorpusGenerator:
    """Run existing Replay through journal authority with one stateful strategy."""

    def __init__(self, store: HistoricalCandleStore) -> None:
        self._store = store

    def generate(
        self,
        configuration: BreakoutRetestResearchConfiguration,
        *,
        replay_speed: ReplaySpeed = ReplaySpeed.X100,
    ) -> BreakoutRetestHistoricalResearchCorpus:
        configuration.validate()
        if any(is_locked_weex_source(source) for source in configuration.source_ids) and (
            not isinstance(self._store, LockedWeexResearchCorpusStore)
            or configuration.source_ids != (self._store.source_id,)
        ):
            raise BreakoutRetestResearchError("qualified WEEX history requires its locked store")
        config = deepcopy(configuration)
        specs = tuple(
            ReplaySeries(source, symbol, timeframe, config.start, config.end)
            for source in config.source_ids
            for symbol in config.symbols
            for timeframe in _TIMEFRAMES
        )
        replay = PacedReplayController(
            ReplayEngine(HistoricalReplaySource(self._store)).prepare(
                specs,
                clock=FrozenClock(config.start),
            ),
            replay_speed,
            timer=_NoWaitReplayTimer(),
        )
        features = FeatureEngine()
        structures = MarketStructureEngine()
        regimes = TechnicalRegimeEngine()
        composer = MultiTimeframeRegimeComposer()
        strategy = BreakoutRetestStrategy()
        risk = RiskManager(config.risk_manager_configuration)
        safety = OperationalSafetyGate(config.safety_configuration)
        journal: InMemoryDecisionJournal[object, object] = InMemoryDecisionJournal()
        decisions: list[BreakoutRetestDecisionObservation] = []
        contexts: list[HistoricalStrategyContextObservation] = []
        for batch in replay.run():
            observed = observed_candles_from_replay(batch)
            feature_batch = features.process(observed)
            structure_batch = structures.process(observed)
            technical_batch = regimes.process(feature_batch, structure_batch)
            mtf_batch = composer.process(technical_batch)
            assembled = assemble_strategy_contexts(
                observed,
                feature_batch,
                structure_batch,
                mtf_batch,
            )
            if not config.start < batch.observed_at <= config.end:
                continue
            entries: list[DecisionJournalEntry[object, object]] = []
            for context in assembled:
                evaluation = strategy.evaluate(context)
                equity = config.research_equity_quote
                portfolio = RiskPortfolioSnapshot(
                    observed_at=context.observed_at,
                    quote_currency="USDT",
                    account_equity_quote=equity,
                    day_start_equity_quote=equity,
                    peak_equity_quote=equity,
                    positions=(),
                )
                risk_decision = risk.evaluate(evaluation, portfolio)
                safety_decision = safety.evaluate(
                    risk_decision,
                    _research_health(context, config.safety_assumptions),
                )
                entry = DecisionJournalEntry(context, evaluation, risk_decision, safety_decision)
                entry.validate()
                entries.append(entry)
                decisions.append(BreakoutRetestDecisionObservation(entry))
                contexts.append(HistoricalStrategyContextObservation(context))
            if entries:
                journal.append_batch(DecisionJournalBatch(batch.observed_at, tuple(entries)))
        if tuple(item.decision for item in decisions) != journal.snapshot().entries:
            raise BreakoutRetestResearchError("decision journal and corpus differ")
        return BreakoutRetestHistoricalResearchCorpus(config, tuple(decisions), tuple(contexts))
