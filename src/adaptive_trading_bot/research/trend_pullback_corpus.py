"""Deterministic historical Trend Pullback decision-corpus generation.

The corpus is research-only and has no trading authority.  Every market-derived fact flows
through Replay, Feature, Structure, Regime, Strategy, Risk, Safety, and DecisionJournal
authorities.  Historical operational facts unavailable from OHLCV remain explicit assumptions.
"""

from __future__ import annotations

from argparse import ArgumentParser
from copy import deepcopy
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum, StrEnum
from hashlib import sha256
from hmac import compare_digest
from json import dumps
from pathlib import Path

from adaptive_trading_bot.domain import (
    FrozenClock,
    Timeframe,
    TradingSymbol,
    align_timestamp,
)
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.features import (
    FeatureEngine,
    MarketStructureEngine,
    observed_candles_from_replay,
)
from adaptive_trading_bot.journal import (
    DecisionIdentity,
    DecisionJournalBatch,
    DecisionJournalEntry,
    InMemoryDecisionJournal,
)
from adaptive_trading_bot.regime import (
    HigherTimeframeBias,
    MultiTimeframeAlignment,
    MultiTimeframeRegimeComposer,
    RegimeLabel,
    TechnicalRegimeEngine,
)
from adaptive_trading_bot.replay import (
    HistoricalReplaySource,
    PacedReplayController,
    ReplayEngine,
    ReplaySeries,
    ReplaySpeed,
)
from adaptive_trading_bot.research.paired_exit_replay import (
    HistoricalStrategyContextObservation,
)
from adaptive_trading_bot.risk import (
    ExchangeApiHealthState,
    FeedIntegrityState,
    OperationalHealthSnapshot,
    OperationalSafetyConfiguration,
    OperationalSafetyGate,
    OperationalSafetyStatus,
    RiskDecisionStatus,
    RiskManager,
    RiskManagerConfiguration,
    RiskPortfolioSnapshot,
    VolatilitySafetyState,
)
from adaptive_trading_bot.storage import (
    HistoricalCandleStore,
    ParquetHistoricalCandleStore,
    SourceId,
)
from adaptive_trading_bot.strategies import (
    CandidateAction,
    SourceIdentity,
    StrategyEvaluationContext,
    TrendPullbackStrategy,
    assemble_strategy_contexts,
)

_CONFIG_SCHEMA_VERSION = "1.0.0"
_SAFETY_ASSUMPTION_VERSION = "1.0.0"
_DECISION_OBSERVATION_SCHEMA_VERSION = "1.0.0"
_CORPUS_SCHEMA_VERSION = "1.0.0"
_ARTIFACT_SCHEMA_VERSION = "1.0.0"
_INTEGRITY_SEAL_VERSION = "1.0.0"
_ADMISSION_MEANING = (
    "cleared under declared research assumptions; not a claim of historical live approval"
)
_V1_TIMEFRAMES = (
    Timeframe.MINUTES_15,
    Timeframe.HOUR_1,
    Timeframe.HOURS_4,
)


class TrendPullbackResearchCorpusError(ValueError):
    """Corpus configuration, authority, or integrity failed closed."""


class ResearchDecisionCategory(StrEnum):
    """Population-preserving result of Strategy -> Risk -> Safety."""

    NO_TRADE = "NO_TRADE"
    RISK_BLOCKED = "RISK_BLOCKED"
    SAFETY_BLOCKED = "SAFETY_BLOCKED"
    RESEARCH_ADMISSIBLE = "RESEARCH_ADMISSIBLE"


@dataclass(frozen=True, slots=True)
class ResearchSafetyAssumptionProfile:
    """Explicit non-historical health assumptions used only to exercise Safety."""

    feed_integrity_state: FeedIntegrityState = FeedIntegrityState.OK
    exchange_api_health_state: ExchangeApiHealthState = ExchangeApiHealthState.HEALTHY
    assumed_spread_bps: Decimal = Decimal(0)
    assumed_time_sync_offset_ms: Decimal = Decimal(0)
    volatility_safety_state: VolatilitySafetyState = VolatilitySafetyState.CLASSIFIED_NORMAL
    assumed_suspicious_price_anomaly: bool = False
    assumed_consecutive_order_rejections: int = 0
    historical_operational_facts_observed: bool = field(init=False, default=False)
    assumption_profile_version: str = field(init=False, default=_SAFETY_ASSUMPTION_VERSION)

    def __post_init__(self) -> None:
        _validate_safety_assumptions(self)


@dataclass(frozen=True, slots=True)
class ResearchAdmissionConfiguration:
    """Versioned fixed-equity, zero-carried-exposure research admission assumptions."""

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
    quote_currency: str = field(init=False, default="USDT")
    timeframes: tuple[Timeframe, ...] = field(init=False, default=_V1_TIMEFRAMES)
    strategy_id: str = field(init=False, default="trend_pullback")
    strategy_version: str = field(init=False, default="1.0.0")
    configuration_schema_version: str = field(init=False, default=_CONFIG_SCHEMA_VERSION)
    corpus_schema_version: str = field(init=False, default=_CORPUS_SCHEMA_VERSION)
    configuration_integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_ids",
            tuple(sorted(deepcopy(self.source_ids), key=lambda item: item.value)),
        )
        object.__setattr__(
            self,
            "symbols",
            tuple(sorted(deepcopy(self.symbols), key=str)),
        )
        object.__setattr__(self, "start", require_utc(self.start, field="research start"))
        object.__setattr__(self, "end", require_utc(self.end, field="research end"))
        object.__setattr__(
            self,
            "risk_manager_configuration",
            deepcopy(self.risk_manager_configuration),
        )
        object.__setattr__(self, "safety_configuration", deepcopy(self.safety_configuration))
        object.__setattr__(self, "safety_assumptions", deepcopy(self.safety_assumptions))
        _validate_configuration_shape(self)
        object.__setattr__(
            self,
            "configuration_integrity_seal",
            _integrity_seal(
                "research-admission-configuration",
                self,
                excluded_names=frozenset({"configuration_integrity_seal"}),
            ),
        )

    def validate(self) -> None:
        """Verify the originally accepted configuration and its complete seal."""

        seal = self.configuration_integrity_seal
        expected = _integrity_seal(
            "research-admission-configuration",
            self,
            excluded_names=frozenset({"configuration_integrity_seal"}),
        )
        if not isinstance(seal, str) or not compare_digest(seal, expected):
            raise TrendPullbackResearchCorpusError("research configuration integrity mismatch")
        _validate_configuration_shape(self)


@dataclass(frozen=True, slots=True)
class HistoricalTrendPullbackDecisionObservation:
    """One sealed, outcome-free historical Trend Pullback decision observation."""

    decision: DecisionJournalEntry[object, object]
    action: CandidateAction = field(init=False)
    category: ResearchDecisionCategory = field(init=False)
    is_research_admissible_entry: bool = field(init=False)
    is_context_ready: bool = field(init=False)
    entry_regime_label: RegimeLabel = field(init=False)
    higher_timeframe_bias: HigherTimeframeBias = field(init=False)
    multi_timeframe_alignment: MultiTimeframeAlignment = field(init=False)
    observation_schema_version: str = field(
        init=False, default=_DECISION_OBSERVATION_SCHEMA_VERSION
    )
    observation_integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        owned = deepcopy(self.decision)
        owned.validate()
        category = _decision_category(owned)
        object.__setattr__(self, "decision", owned)
        object.__setattr__(self, "action", owned.strategy_evaluation.action)
        object.__setattr__(self, "category", category)
        object.__setattr__(
            self,
            "is_research_admissible_entry",
            category is ResearchDecisionCategory.RESEARCH_ADMISSIBLE,
        )
        object.__setattr__(
            self,
            "is_context_ready",
            owned.context.features_15m.is_fully_warmed
            and owned.context.multi_timeframe_regime.is_fully_ready,
        )
        object.__setattr__(
            self,
            "entry_regime_label",
            owned.context.multi_timeframe_regime.regime_15m.regime_label,
        )
        object.__setattr__(
            self,
            "higher_timeframe_bias",
            owned.context.multi_timeframe_regime.higher_timeframe_bias,
        )
        object.__setattr__(
            self,
            "multi_timeframe_alignment",
            owned.context.multi_timeframe_regime.alignment,
        )
        object.__setattr__(
            self,
            "observation_integrity_seal",
            _integrity_seal(
                "historical-trend-pullback-decision-observation",
                self,
                excluded_names=frozenset({"observation_integrity_seal"}),
            ),
        )

    @property
    def identity(self) -> DecisionIdentity:
        """Return the sealed journal identity without inventing another join key."""

        return self.decision.identity

    @property
    def observed_at(self) -> datetime:
        return self.identity.observed_at

    @property
    def source_id(self) -> SourceIdentity:
        return self.identity.source_id

    @property
    def symbol(self) -> TradingSymbol:
        return self.identity.symbol

    def validate(self) -> None:
        """Revalidate the journal authority, projections, and creation-time seal."""

        seal = self.observation_integrity_seal
        expected = _integrity_seal(
            "historical-trend-pullback-decision-observation",
            self,
            excluded_names=frozenset({"observation_integrity_seal"}),
        )
        if not isinstance(seal, str) or not compare_digest(seal, expected):
            raise TrendPullbackResearchCorpusError("decision observation integrity mismatch")
        self.decision.validate()
        category = _decision_category(self.decision)
        context = self.decision.context
        if (
            self.observation_schema_version != _DECISION_OBSERVATION_SCHEMA_VERSION
            or self.action is not self.decision.strategy_evaluation.action
            or self.category is not category
            or self.is_research_admissible_entry
            is not (category is ResearchDecisionCategory.RESEARCH_ADMISSIBLE)
            or self.is_context_ready
            is not (
                context.features_15m.is_fully_warmed
                and context.multi_timeframe_regime.is_fully_ready
            )
            or self.entry_regime_label is not context.multi_timeframe_regime.regime_15m.regime_label
            or self.higher_timeframe_bias
            is not context.multi_timeframe_regime.higher_timeframe_bias
            or self.multi_timeframe_alignment is not context.multi_timeframe_regime.alignment
        ):
            raise TrendPullbackResearchCorpusError("decision observation projection mismatch")


@dataclass(frozen=True, slots=True)
class TrendPullbackHistoricalResearchCorpus:
    """Detached complete decision population and same-source sealed context sequence."""

    configuration: ResearchAdmissionConfiguration
    decisions: tuple[HistoricalTrendPullbackDecisionObservation, ...]
    historical_contexts: tuple[HistoricalStrategyContextObservation, ...]
    total_evaluation_count: int = field(init=False)
    not_ready_count: int = field(init=False)
    no_trade_count: int = field(init=False)
    long_candidate_count: int = field(init=False)
    short_candidate_count: int = field(init=False)
    risk_blocked_count: int = field(init=False)
    safety_blocked_count: int = field(init=False)
    research_admissible_entry_count: int = field(init=False)
    research_admissible_entry_identities: tuple[DecisionIdentity, ...] = field(init=False)
    corpus_digest: str = field(init=False)
    corpus_id: str = field(init=False)
    corpus_schema_version: str = field(init=False, default=_CORPUS_SCHEMA_VERSION)
    is_trading_authority: bool = field(init=False, default=False)
    research_admission_meaning: str = field(
        init=False,
        default=_ADMISSION_MEANING,
    )
    corpus_integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration", deepcopy(self.configuration))
        object.__setattr__(self, "decisions", tuple(deepcopy(self.decisions)))
        object.__setattr__(self, "historical_contexts", tuple(deepcopy(self.historical_contexts)))
        counts = _corpus_counts(self.decisions)
        for name, value in counts.items():
            object.__setattr__(self, name, value)
        identities = tuple(
            item.identity for item in self.decisions if item.is_research_admissible_entry
        )
        object.__setattr__(self, "research_admissible_entry_identities", identities)
        digest = _corpus_digest(self.configuration, self.decisions, self.historical_contexts)
        object.__setattr__(self, "corpus_digest", digest)
        object.__setattr__(self, "corpus_id", f"trend-pullback-research-corpus:{digest}")
        _validate_corpus(self)
        object.__setattr__(
            self,
            "corpus_integrity_seal",
            _integrity_seal(
                "trend-pullback-historical-research-corpus",
                self,
                excluded_names=frozenset({"corpus_integrity_seal"}),
            ),
        )

    def validate(self) -> None:
        """Fail closed on any nested mutation or forged aggregate."""

        seal = self.corpus_integrity_seal
        expected = _integrity_seal(
            "trend-pullback-historical-research-corpus",
            self,
            excluded_names=frozenset({"corpus_integrity_seal"}),
        )
        if not isinstance(seal, str) or not compare_digest(seal, expected):
            raise TrendPullbackResearchCorpusError("research corpus integrity mismatch")
        _validate_corpus(self)

    def contexts_after_decision(
        self, identity: DecisionIdentity
    ) -> tuple[HistoricalStrategyContextObservation, ...]:
        """Return the broader same-source sequence; paired replay applies fill chronology."""

        self.validate()
        eligible = set(self.research_admissible_entry_identities)
        if identity not in eligible:
            raise TrendPullbackResearchCorpusError(
                "context retrieval requires a research-admissible decision identity"
            )
        return tuple(
            deepcopy(item)
            for item in self.historical_contexts
            if item.context.source_id == identity.source_id
            and item.context.symbol == identity.symbol
            and item.context.observed_at > identity.observed_at
        )

    def to_json(self) -> str:
        """Serialize a deterministic lossless research-only machine artifact."""

        self.validate()
        return dumps(
            {
                "artifact_schema_version": _ARTIFACT_SCHEMA_VERSION,
                "artifact_kind": "trend_pullback_historical_research_corpus",
                "research_only": True,
                "is_trading_authority": False,
                "research_admission_meaning": self.research_admission_meaning,
                "corpus": _canonical_integrity_value(self, excluded_names=frozenset()),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


class _NoWaitReplayTimer:
    """Exercise Replay pacing without changing or delaying historical semantics."""

    def monotonic(self) -> float:
        return 0.0

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("research replay delay cannot be negative")


class TrendPullbackHistoricalCorpusGenerator:
    """Compose existing causal authorities into one deterministic research corpus."""

    __slots__ = ("_store",)

    def __init__(self, store: HistoricalCandleStore) -> None:
        if not hasattr(store, "read") or not hasattr(store, "scan_integrity"):
            raise TypeError("store must satisfy HistoricalCandleStore")
        self._store = store

    def generate(
        self,
        configuration: ResearchAdmissionConfiguration,
        *,
        replay_speed: ReplaySpeed = ReplaySpeed.X100,
    ) -> TrendPullbackHistoricalResearchCorpus:
        """Generate all in-range 15m decisions under independent admission assumptions."""

        if not isinstance(configuration, ResearchAdmissionConfiguration):
            raise TypeError("configuration must be a ResearchAdmissionConfiguration")
        configuration.validate()
        if not isinstance(replay_speed, ReplaySpeed):
            raise TypeError("replay_speed must be a ReplaySpeed")
        config = deepcopy(configuration)
        specifications = tuple(
            ReplaySeries(source_id, symbol, timeframe, config.start, config.end)
            for source_id in config.source_ids
            for symbol in config.symbols
            for timeframe in config.timeframes
        )
        session = ReplayEngine(HistoricalReplaySource(self._store)).prepare(
            specifications,
            clock=FrozenClock(config.start),
        )
        replay = PacedReplayController(session, replay_speed, timer=_NoWaitReplayTimer())
        features = FeatureEngine()
        structures = MarketStructureEngine()
        regimes = TechnicalRegimeEngine()
        composer = MultiTimeframeRegimeComposer()
        strategy = TrendPullbackStrategy()
        risk = RiskManager(config.risk_manager_configuration)
        safety = OperationalSafetyGate(config.safety_configuration)
        journal: InMemoryDecisionJournal[object, object] = InMemoryDecisionJournal()
        records: list[HistoricalTrendPullbackDecisionObservation] = []
        context_observations: list[HistoricalStrategyContextObservation] = []

        for replay_batch in replay.run():
            observed = observed_candles_from_replay(replay_batch)
            feature_batch = features.process(observed)
            structure_batch = structures.process(observed)
            technical_batch = regimes.process(feature_batch, structure_batch)
            mtf_batch = composer.process(technical_batch)
            contexts = assemble_strategy_contexts(
                observed,
                feature_batch,
                structure_batch,
                mtf_batch,
            )
            # ReplaySeries bounds candle opens by [start, end); their closed-candle decision
            # observations therefore occupy (start, end].
            if not config.start < replay_batch.observed_at <= config.end:
                continue
            entries: list[DecisionJournalEntry[object, object]] = []
            for context in contexts:
                evaluation = strategy.evaluate(context)
                portfolio = RiskPortfolioSnapshot(
                    observed_at=context.observed_at,
                    quote_currency=config.quote_currency,
                    account_equity_quote=config.research_equity_quote,
                    day_start_equity_quote=config.research_equity_quote,
                    peak_equity_quote=config.research_equity_quote,
                    positions=(),
                )
                risk_decision = risk.evaluate(evaluation, portfolio)
                health = _research_health(context, config.safety_assumptions)
                safety_decision = safety.evaluate(risk_decision, health)
                entry = DecisionJournalEntry(context, evaluation, risk_decision, safety_decision)
                entry.validate()
                entries.append(entry)
                records.append(HistoricalTrendPullbackDecisionObservation(entry))
                context_observations.append(HistoricalStrategyContextObservation(context))
            if entries:
                journal.append_batch(DecisionJournalBatch(replay_batch.observed_at, tuple(entries)))

        snapshot = journal.snapshot()
        if tuple(item.decision for item in records) != snapshot.entries:
            raise TrendPullbackResearchCorpusError("decision journal snapshot differs from corpus")
        return TrendPullbackHistoricalResearchCorpus(
            configuration=config,
            decisions=tuple(records),
            historical_contexts=tuple(context_observations),
        )


def _research_health(
    context: StrategyEvaluationContext,
    assumptions: ResearchSafetyAssumptionProfile,
) -> OperationalHealthSnapshot:
    return OperationalHealthSnapshot(
        source_id=context.source_id,
        symbol=context.symbol,
        observed_at=context.observed_at,
        market_data_observed_at=context.observed_at,
        feed_integrity_state=assumptions.feed_integrity_state,
        exchange_api_health_state=assumptions.exchange_api_health_state,
        spread_bps=assumptions.assumed_spread_bps,
        time_sync_offset_ms=assumptions.assumed_time_sync_offset_ms,
        volatility_safety_state=assumptions.volatility_safety_state,
        suspicious_price_anomaly=assumptions.assumed_suspicious_price_anomaly,
        consecutive_order_rejections=assumptions.assumed_consecutive_order_rejections,
    )


def _decision_category(
    decision: DecisionJournalEntry[object, object],
) -> ResearchDecisionCategory:
    action = decision.strategy_evaluation.action
    risk = decision.risk_decision.status
    safety = decision.safety_decision.status
    if action is CandidateAction.NO_TRADE:
        if risk is not RiskDecisionStatus.NO_ACTION or safety not in {
            OperationalSafetyStatus.NO_ACTION,
            OperationalSafetyStatus.SAFE_MODE,
        }:
            raise TrendPullbackResearchCorpusError("NO_TRADE admission chain is inconsistent")
        return ResearchDecisionCategory.NO_TRADE
    if risk is RiskDecisionStatus.REJECTED:
        if safety is not OperationalSafetyStatus.BLOCKED_BY_RISK:
            raise TrendPullbackResearchCorpusError("Risk-blocked admission chain is inconsistent")
        return ResearchDecisionCategory.RISK_BLOCKED
    if risk is not RiskDecisionStatus.APPROVED:
        raise TrendPullbackResearchCorpusError("candidate has an invalid Risk status")
    if safety is OperationalSafetyStatus.CLEARED:
        return ResearchDecisionCategory.RESEARCH_ADMISSIBLE
    if safety is OperationalSafetyStatus.SAFE_MODE:
        return ResearchDecisionCategory.SAFETY_BLOCKED
    raise TrendPullbackResearchCorpusError("candidate has an invalid Safety status")


def _corpus_counts(
    decisions: tuple[HistoricalTrendPullbackDecisionObservation, ...],
) -> dict[str, int]:
    return {
        "total_evaluation_count": len(decisions),
        "not_ready_count": sum(not item.is_context_ready for item in decisions),
        "no_trade_count": sum(item.action is CandidateAction.NO_TRADE for item in decisions),
        "long_candidate_count": sum(
            item.action is CandidateAction.LONG_CANDIDATE for item in decisions
        ),
        "short_candidate_count": sum(
            item.action is CandidateAction.SHORT_CANDIDATE for item in decisions
        ),
        "risk_blocked_count": sum(
            item.category is ResearchDecisionCategory.RISK_BLOCKED for item in decisions
        ),
        "safety_blocked_count": sum(
            item.category is ResearchDecisionCategory.SAFETY_BLOCKED for item in decisions
        ),
        "research_admissible_entry_count": sum(
            item.is_research_admissible_entry for item in decisions
        ),
    }


def _validate_corpus(corpus: TrendPullbackHistoricalResearchCorpus) -> None:
    corpus.configuration.validate()
    if (
        corpus.corpus_schema_version != _CORPUS_SCHEMA_VERSION
        or corpus.is_trading_authority
        or corpus.research_admission_meaning != _ADMISSION_MEANING
    ):
        raise TrendPullbackResearchCorpusError("corpus schema or authority claim is invalid")
    if not isinstance(corpus.decisions, tuple) or not isinstance(corpus.historical_contexts, tuple):
        raise TypeError("corpus decisions and contexts must be immutable tuples")
    for decision_observation in corpus.decisions:
        decision_observation.validate()
    for context_observation in corpus.historical_contexts:
        context_observation.validate()
    decision_keys = tuple(item.identity.sort_key for item in corpus.decisions)
    if decision_keys != tuple(sorted(decision_keys)) or len(set(decision_keys)) != len(
        decision_keys
    ):
        raise TrendPullbackResearchCorpusError("decision observations are not canonical and unique")
    context_keys = tuple(
        (
            item.context.observed_at,
            item.context.source_id.value,
            str(item.context.symbol),
        )
        for item in corpus.historical_contexts
    )
    if context_keys != tuple(sorted(context_keys)) or len(set(context_keys)) != len(context_keys):
        raise TrendPullbackResearchCorpusError("historical contexts are not canonical and unique")
    contexts_by_key = {
        key: context for key, context in zip(context_keys, corpus.historical_contexts, strict=True)
    }
    if len(corpus.decisions) != len(corpus.historical_contexts):
        raise TrendPullbackResearchCorpusError("decision and historical context populations differ")
    for item in corpus.decisions:
        key = (item.observed_at, item.identity.source_id.value, str(item.symbol))
        context = contexts_by_key.get(key)
        if context is None or context.context != item.decision.context:
            raise TrendPullbackResearchCorpusError("decision is not bound to its sealed context")
        if (
            item.identity.source_id not in corpus.configuration.source_ids
            or item.symbol not in corpus.configuration.symbols
            or not corpus.configuration.start < item.observed_at <= corpus.configuration.end
            or item.identity.strategy_id != corpus.configuration.strategy_id
            or item.identity.strategy_version != corpus.configuration.strategy_version
        ):
            raise TrendPullbackResearchCorpusError("decision lies outside corpus provenance")
        risk = item.decision.risk_decision
        if (
            risk.account_equity_quote != corpus.configuration.research_equity_quote
            or risk.day_start_equity_quote != corpus.configuration.research_equity_quote
            or risk.peak_equity_quote != corpus.configuration.research_equity_quote
            or risk.current_open_risk_quote != 0
            or risk.current_gross_exposure_quote != 0
        ):
            raise TrendPullbackResearchCorpusError("decision violates independent research capital")
        _validate_admission_audit(item.decision, corpus.configuration)
    counts = _corpus_counts(corpus.decisions)
    if any(getattr(corpus, name) != value for name, value in counts.items()):
        raise TrendPullbackResearchCorpusError("corpus aggregate count is inconsistent")
    identities = tuple(
        item.identity for item in corpus.decisions if item.is_research_admissible_entry
    )
    if corpus.research_admissible_entry_identities != identities:
        raise TrendPullbackResearchCorpusError("admissible identity projection is inconsistent")
    digest = _corpus_digest(
        corpus.configuration,
        corpus.decisions,
        corpus.historical_contexts,
    )
    if corpus.corpus_digest != digest or corpus.corpus_id != (
        f"trend-pullback-research-corpus:{digest}"
    ):
        raise TrendPullbackResearchCorpusError("corpus digest is inconsistent")


def _validate_admission_audit(
    decision: DecisionJournalEntry[object, object],
    configuration: ResearchAdmissionConfiguration,
) -> None:
    risk = decision.risk_decision
    safety = decision.safety_decision
    risk_configuration = configuration.risk_manager_configuration
    safety_configuration = configuration.safety_configuration
    assumptions = configuration.safety_assumptions
    if (
        risk.risk_policy_version != risk_configuration.risk_policy_version
        or risk.max_daily_loss_fraction != risk_configuration.max_daily_loss_fraction
        or risk.max_drawdown_fraction != risk_configuration.max_drawdown_fraction
        or safety.safety_policy_version != safety_configuration.safety_policy_version
        or safety.max_market_data_age_seconds != safety_configuration.max_market_data_age_seconds
        or safety.max_spread_bps != safety_configuration.max_spread_bps
        or safety.max_abs_time_sync_offset_ms != safety_configuration.max_abs_time_sync_offset_ms
        or safety.max_consecutive_order_rejections
        != safety_configuration.max_consecutive_order_rejections
        or safety.market_data_observed_at != decision.identity.observed_at
        or safety.feed_integrity_state is not assumptions.feed_integrity_state
        or safety.exchange_api_health_state is not assumptions.exchange_api_health_state
        or safety.spread_bps != assumptions.assumed_spread_bps
        or safety.time_sync_offset_ms != assumptions.assumed_time_sync_offset_ms
        or safety.volatility_safety_state is not assumptions.volatility_safety_state
        or safety.suspicious_price_anomaly is not assumptions.assumed_suspicious_price_anomaly
        or safety.consecutive_order_rejections != assumptions.assumed_consecutive_order_rejections
    ):
        raise TrendPullbackResearchCorpusError(
            "decision admission audit differs from declared research assumptions"
        )


def _validate_configuration_shape(configuration: ResearchAdmissionConfiguration) -> None:
    if configuration.configuration_schema_version != _CONFIG_SCHEMA_VERSION:
        raise TrendPullbackResearchCorpusError("configuration schema version mismatch")
    if configuration.corpus_schema_version != _CORPUS_SCHEMA_VERSION:
        raise TrendPullbackResearchCorpusError("configured corpus schema version mismatch")
    if configuration.strategy_id != "trend_pullback" or configuration.strategy_version != "1.0.0":
        raise TrendPullbackResearchCorpusError("configuration strategy provenance is invalid")
    if configuration.quote_currency != "USDT" or configuration.timeframes != _V1_TIMEFRAMES:
        raise TrendPullbackResearchCorpusError("configuration must use V1 USDT/15m/1h/4h")
    if not configuration.source_ids or len(set(configuration.source_ids)) != len(
        configuration.source_ids
    ):
        raise TrendPullbackResearchCorpusError("source_ids must be non-empty and unique")
    if not configuration.symbols or len(set(configuration.symbols)) != len(configuration.symbols):
        raise TrendPullbackResearchCorpusError("symbols must be non-empty and unique")
    if any(
        symbol.base not in {"BTC", "ETH"} or symbol.quote != "USDT"
        for symbol in configuration.symbols
    ):
        raise TrendPullbackResearchCorpusError("symbols must be BTC/USDT or ETH/USDT")
    if configuration.start >= configuration.end:
        raise TrendPullbackResearchCorpusError("research range must be non-empty")
    if any(
        align_timestamp(configuration.start, timeframe) != configuration.start
        or align_timestamp(configuration.end, timeframe) != configuration.end
        for timeframe in configuration.timeframes
    ):
        raise TrendPullbackResearchCorpusError("research range must align to every V1 timeframe")
    equity = configuration.research_equity_quote
    if not isinstance(equity, Decimal) or not equity.is_finite() or equity <= 0:
        raise TrendPullbackResearchCorpusError("research equity must be a positive finite Decimal")
    _revalidate_risk_configuration(configuration.risk_manager_configuration)
    _revalidate_safety_configuration(configuration.safety_configuration)
    _validate_safety_assumptions(configuration.safety_assumptions)


def _revalidate_risk_configuration(configuration: RiskManagerConfiguration) -> None:
    if not isinstance(configuration, RiskManagerConfiguration):
        raise TypeError("risk_manager_configuration must be a RiskManagerConfiguration")
    reconstructed = RiskManagerConfiguration(
        risk_per_trade_fraction=configuration.risk_per_trade_fraction,
        max_simultaneous_risk_fraction=configuration.max_simultaneous_risk_fraction,
        max_daily_loss_fraction=configuration.max_daily_loss_fraction,
        max_drawdown_fraction=configuration.max_drawdown_fraction,
        max_gross_exposure_fraction=configuration.max_gross_exposure_fraction,
    )
    if reconstructed != configuration:
        raise TrendPullbackResearchCorpusError("Risk configuration reconstruction changed facts")


def _revalidate_safety_configuration(configuration: OperationalSafetyConfiguration) -> None:
    if not isinstance(configuration, OperationalSafetyConfiguration):
        raise TypeError("safety_configuration must be an OperationalSafetyConfiguration")
    reconstructed = OperationalSafetyConfiguration(
        max_market_data_age_seconds=configuration.max_market_data_age_seconds,
        max_spread_bps=configuration.max_spread_bps,
        max_abs_time_sync_offset_ms=configuration.max_abs_time_sync_offset_ms,
        max_consecutive_order_rejections=configuration.max_consecutive_order_rejections,
    )
    if reconstructed != configuration:
        raise TrendPullbackResearchCorpusError("Safety configuration reconstruction changed facts")


def _validate_safety_assumptions(assumptions: ResearchSafetyAssumptionProfile) -> None:
    if not isinstance(assumptions, ResearchSafetyAssumptionProfile):
        raise TypeError("safety_assumptions must be a ResearchSafetyAssumptionProfile")
    if assumptions.assumption_profile_version != _SAFETY_ASSUMPTION_VERSION:
        raise TrendPullbackResearchCorpusError("Safety assumption profile version mismatch")
    if assumptions.historical_operational_facts_observed:
        raise TrendPullbackResearchCorpusError("research assumptions cannot claim observed history")
    if not isinstance(assumptions.feed_integrity_state, FeedIntegrityState):
        raise TypeError("assumed feed integrity state is invalid")
    if not isinstance(assumptions.exchange_api_health_state, ExchangeApiHealthState):
        raise TypeError("assumed exchange API health state is invalid")
    if not isinstance(assumptions.volatility_safety_state, VolatilitySafetyState):
        raise TypeError("assumed volatility safety state is invalid")
    for name, value in (
        ("assumed_spread_bps", assumptions.assumed_spread_bps),
        ("assumed_time_sync_offset_ms", assumptions.assumed_time_sync_offset_ms),
    ):
        if not isinstance(value, Decimal) or not value.is_finite():
            raise TrendPullbackResearchCorpusError(f"{name} must be a finite Decimal")
    if assumptions.assumed_spread_bps < 0:
        raise TrendPullbackResearchCorpusError("assumed spread cannot be negative")
    if type(assumptions.assumed_suspicious_price_anomaly) is not bool:
        raise TypeError("assumed price anomaly must be a bool")
    if (
        type(assumptions.assumed_consecutive_order_rejections) is not int
        or assumptions.assumed_consecutive_order_rejections < 0
    ):
        raise TrendPullbackResearchCorpusError("assumed rejection count must be non-negative")


def _corpus_digest(
    configuration: ResearchAdmissionConfiguration,
    decisions: tuple[HistoricalTrendPullbackDecisionObservation, ...],
    contexts: tuple[HistoricalStrategyContextObservation, ...],
) -> str:
    return _integrity_seal(
        "trend-pullback-historical-research-corpus-digest",
        (configuration, decisions, contexts),
        excluded_names=frozenset(),
    )


def _integrity_seal(
    namespace: str,
    value: object,
    *,
    excluded_names: frozenset[str],
) -> str:
    payload = dumps(
        (
            f"adaptive-trading-bot/{namespace}",
            _INTEGRITY_SEAL_VERSION,
            _canonical_integrity_value(value, excluded_names=excluded_names),
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _canonical_integrity_value(
    value: object,
    *,
    excluded_names: frozenset[str],
) -> object:
    if value is None:
        return ("none",)
    if isinstance(value, Enum):
        return (
            "enum",
            f"{type(value).__module__}.{type(value).__qualname__}",
            _canonical_integrity_value(value.value, excluded_names=excluded_names),
        )
    if isinstance(value, datetime):
        return ("datetime", value.isoformat(timespec="microseconds"), value.fold)
    if isinstance(value, timedelta):
        return ("timedelta", value.days, value.seconds, value.microseconds)
    if isinstance(value, Decimal):
        decimal_tuple = value.as_tuple()
        return (
            "decimal",
            decimal_tuple.sign,
            tuple(decimal_tuple.digits),
            str(decimal_tuple.exponent),
        )
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, tuple):
        return (
            "tuple",
            tuple(
                _canonical_integrity_value(item, excluded_names=excluded_names) for item in value
            ),
        )
    if is_dataclass(value) and not isinstance(value, type):
        return (
            "dataclass",
            f"{type(value).__module__}.{type(value).__qualname__}",
            tuple(
                (
                    item.name,
                    _canonical_integrity_value(
                        getattr(value, item.name),
                        excluded_names=excluded_names,
                    ),
                )
                for item in fields(value)
                if item.name not in excluded_names
            ),
        )
    raise TrendPullbackResearchCorpusError(
        f"unsupported corpus integrity type {type(value).__qualname__}"
    )


def _parse_utc(value: str) -> datetime:
    return require_utc(
        datetime.fromisoformat(value.strip().replace("Z", "+00:00")),
        field="CLI timestamp",
    )


def _parser() -> ArgumentParser:
    parser = ArgumentParser(description="Generate a research-only Trend Pullback corpus")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument(
        "--symbol", action="append", choices=("BTC/USDT", "ETH/USDT"), required=True
    )
    parser.add_argument("--start", type=_parse_utc, required=True)
    parser.add_argument("--end", type=_parse_utc, required=True)
    parser.add_argument("--research-equity", type=Decimal, required=True)
    parser.add_argument(
        "--speed", choices=tuple(speed.label for speed in ReplaySpeed), default="100x"
    )
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Generate a deterministic JSON research artifact without market-data mutation."""

    arguments = _parser().parse_args(argv)
    symbols = tuple(TradingSymbol(*value.split("/", maxsplit=1)) for value in arguments.symbol)
    configuration = ResearchAdmissionConfiguration(
        source_ids=tuple(SourceId(value) for value in arguments.source),
        symbols=symbols,
        start=arguments.start,
        end=arguments.end,
        research_equity_quote=arguments.research_equity,
    )
    corpus = TrendPullbackHistoricalCorpusGenerator(
        ParquetHistoricalCandleStore(arguments.data_root)
    ).generate(configuration, replay_speed=ReplaySpeed.parse(arguments.speed))
    artifact = corpus.to_json()
    if arguments.output is None:
        print(artifact)
    else:
        arguments.output.write_text(artifact + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
