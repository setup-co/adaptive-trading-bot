"""Causal, sealed Range Mean Reversion research decisions; never trading authority."""

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
from adaptive_trading_bot.research.weex_historical_corpus import (
    CorpusInterval,
    LockedWeexResearchCorpusStore,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyConfiguration,
    OperationalSafetyGate,
    RiskManager,
    RiskManagerConfiguration,
    RiskPortfolioSnapshot,
)
from adaptive_trading_bot.storage import HistoricalCandleStore, SourceId
from adaptive_trading_bot.strategies import (
    RangeMeanReversionConfiguration,
    RangeMeanReversionStrategy,
    assemble_strategy_contexts,
)
from adaptive_trading_bot.strategies.range_context import ValidatedRangeConfiguration

_SCHEMA = "range-mean-reversion-research-corpus-v1"
_TIMEFRAMES = (Timeframe.MINUTES_15, Timeframe.HOUR_1, Timeframe.HOURS_4)


class RangeMeanReversionResearchError(ValueError):
    """The declared corpus or one of its authorities is inconsistent."""


@dataclass(frozen=True, slots=True)
class RangeMeanReversionResearchConfiguration:
    source_ids: tuple[SourceId, ...]
    symbols: tuple[TradingSymbol, ...]
    start: datetime
    end: datetime
    research_equity_quote: Decimal
    warmup_exclusions: tuple[tuple[TradingSymbol, CorpusInterval], ...] = ()
    risk_manager_configuration: RiskManagerConfiguration = field(
        default_factory=RiskManagerConfiguration
    )
    safety_configuration: OperationalSafetyConfiguration = field(
        default_factory=OperationalSafetyConfiguration
    )
    safety_assumptions: ResearchSafetyAssumptionProfile = field(
        default_factory=ResearchSafetyAssumptionProfile
    )
    strategy_configuration: RangeMeanReversionConfiguration = field(
        default_factory=RangeMeanReversionConfiguration
    )
    range_configuration: ValidatedRangeConfiguration = field(
        default_factory=ValidatedRangeConfiguration
    )
    schema_version: str = field(init=False, default=_SCHEMA)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_ids", tuple(sorted(deepcopy(self.source_ids), key=lambda x: x.value))
        )
        object.__setattr__(self, "symbols", tuple(sorted(deepcopy(self.symbols), key=str)))
        object.__setattr__(
            self,
            "warmup_exclusions",
            tuple(
                sorted(
                    deepcopy(self.warmup_exclusions), key=lambda item: (str(item[0]), item[1].start)
                )
            ),
        )
        object.__setattr__(self, "start", require_utc(self.start, field="research start"))
        object.__setattr__(self, "end", require_utc(self.end, field="research end"))
        for name in (
            "risk_manager_configuration",
            "safety_configuration",
            "safety_assumptions",
            "strategy_configuration",
            "range_configuration",
        ):
            object.__setattr__(self, name, deepcopy(getattr(self, name)))
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "range-research-configuration", self, excluded_names=frozenset({"integrity_seal"})
            ),
        )

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "range-research-configuration", self, excluded_names=frozenset({"integrity_seal"})
            ),
        ):
            raise RangeMeanReversionResearchError("research configuration integrity mismatch")
        self._validate_facts()

    def _validate_facts(self) -> None:
        if self.schema_version != _SCHEMA or self.start >= self.end:
            raise RangeMeanReversionResearchError("research schema or range is invalid")
        if not self.source_ids or len(set(self.source_ids)) != len(self.source_ids):
            raise RangeMeanReversionResearchError("research sources must be unique and non-empty")
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise RangeMeanReversionResearchError("research symbols must be unique and non-empty")
        if any(s.base not in {"BTC", "ETH"} or s.quote != "USDT" for s in self.symbols):
            raise RangeMeanReversionResearchError("only V1 BTC/ETH USDT research is allowed")
        if any(
            symbol not in self.symbols or not isinstance(interval, CorpusInterval)
            for symbol, interval in self.warmup_exclusions
        ):
            raise RangeMeanReversionResearchError("warm-up exclusion identity is invalid")
        for left, right in zip(self.warmup_exclusions, self.warmup_exclusions[1:], strict=False):
            if left[0] == right[0] and left[1].end > right[1].start:
                raise RangeMeanReversionResearchError("warm-up exclusions overlap")
        if any(
            align_timestamp(t, frame) != t for t in (self.start, self.end) for frame in _TIMEFRAMES
        ):
            raise RangeMeanReversionResearchError("research range must align to V1 timeframes")
        if (
            not isinstance(self.research_equity_quote, Decimal)
            or not self.research_equity_quote.is_finite()
            or self.research_equity_quote <= 0
        ):
            raise RangeMeanReversionResearchError("research equity must be positive and finite")
        if (
            self.strategy_configuration != RangeMeanReversionConfiguration()
            or self.range_configuration != ValidatedRangeConfiguration()
        ):
            raise RangeMeanReversionResearchError(
                "only default Range Mean Reversion V1 is predeclared"
            )
        _revalidate_risk_configuration(self.risk_manager_configuration)
        _revalidate_safety_configuration(self.safety_configuration)
        _validate_safety_assumptions(self.safety_assumptions)


@dataclass(frozen=True, slots=True)
class RangeMeanReversionDecisionObservation:
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
                "range-decision-observation", self, excluded_names=frozenset({"integrity_seal"})
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
                "range-decision-observation", self, excluded_names=frozenset({"integrity_seal"})
            ),
        ):
            raise RangeMeanReversionResearchError("decision observation integrity mismatch")
        self.decision.validate()
        if self.category is not _decision_category(self.decision):
            raise RangeMeanReversionResearchError("decision category contradicts authority")


@dataclass(frozen=True, slots=True)
class RangeMeanReversionHistoricalResearchCorpus:
    configuration: RangeMeanReversionResearchConfiguration
    decisions: tuple[RangeMeanReversionDecisionObservation, ...]
    historical_contexts: tuple[HistoricalStrategyContextObservation, ...]
    research_admissible_entry_identities: tuple[DecisionIdentity, ...] = field(init=False)
    warmup_excluded_entry_identities: tuple[DecisionIdentity, ...] = field(init=False)
    corpus_digest: str = field(init=False)
    corpus_id: str = field(init=False)
    schema_version: str = field(init=False, default=_SCHEMA)
    is_trading_authority: bool = field(init=False, default=False)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration", deepcopy(self.configuration))
        object.__setattr__(self, "decisions", deepcopy(self.decisions))
        object.__setattr__(self, "historical_contexts", deepcopy(self.historical_contexts))
        excluded = tuple(
            item.identity
            for item in self.decisions
            if item.is_research_admissible_entry
            and _is_warmup_excluded(self.configuration, item.identity)
        )
        identities = tuple(
            item.identity
            for item in self.decisions
            if item.is_research_admissible_entry
            and not _is_warmup_excluded(self.configuration, item.identity)
        )
        object.__setattr__(self, "warmup_excluded_entry_identities", excluded)
        object.__setattr__(self, "research_admissible_entry_identities", identities)
        digest = _integrity_seal(
            "range-corpus-digest",
            (
                self.configuration,
                self.decisions,
                self.historical_contexts,
            ),
            excluded_names=frozenset(),
        )
        object.__setattr__(self, "corpus_digest", digest)
        object.__setattr__(self, "corpus_id", f"range-mean-reversion-research-corpus:{digest}")
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "range-research-corpus", self, excluded_names=frozenset({"integrity_seal"})
            ),
        )

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "range-research-corpus", self, excluded_names=frozenset({"integrity_seal"})
            ),
        ):
            raise RangeMeanReversionResearchError("research corpus integrity mismatch")
        self._validate_facts()

    def _validate_facts(self) -> None:
        self.configuration.validate()
        if self.schema_version != _SCHEMA or self.is_trading_authority:
            raise RangeMeanReversionResearchError("corpus schema or authority claim is invalid")
        if len(self.decisions) != len(self.historical_contexts):
            raise RangeMeanReversionResearchError("decision/context population differs")
        expected_per_series = int(
            (self.configuration.end - self.configuration.start) / Timeframe.MINUTES_15.duration
        )
        expected_total = (
            expected_per_series
            * len(self.configuration.source_ids)
            * len(self.configuration.symbols)
        )
        if len(self.decisions) != expected_total:
            raise RangeMeanReversionResearchError("decision population omits a 15m observation")
        # One strategy and detector for the entire consecutive series: replaying each
        # decision independently would erase the actual range lifecycle.
        strategy = RangeMeanReversionStrategy()
        previous: tuple[datetime, str, str] | None = None
        previous_by_series: dict[tuple[str, str], datetime] = {}
        counts_by_series: dict[tuple[str, str], int] = {}
        for item, sealed in zip(self.decisions, self.historical_contexts, strict=True):
            item.validate()
            sealed.validate()
            decision = item.decision
            context = sealed.context
            key = (
                decision.identity.observed_at,
                decision.identity.source_id.value,
                str(decision.identity.symbol),
            )
            if previous is not None and key <= previous:
                raise RangeMeanReversionResearchError("decisions are not canonical and unique")
            previous = key
            series_key = (key[1], key[2])
            prior = previous_by_series.get(series_key)
            if prior is None and key[0] != self.configuration.start + Timeframe.MINUTES_15.duration:
                raise RangeMeanReversionResearchError("range detector chronology omits first bar")
            if prior is not None and key[0] != prior + Timeframe.MINUTES_15.duration:
                raise RangeMeanReversionResearchError("range detector chronology has a gap")
            previous_by_series[series_key] = key[0]
            counts_by_series[series_key] = counts_by_series.get(series_key, 0) + 1
            if (
                context != decision.context
                or decision.identity.source_id not in self.configuration.source_ids
                or decision.identity.symbol not in self.configuration.symbols
                or not self.configuration.start < key[0] <= self.configuration.end
                or decision.identity.strategy_id != strategy.strategy_id
                or decision.identity.strategy_version != strategy.strategy_version
            ):
                raise RangeMeanReversionResearchError("decision context or provenance differs")
            expected = strategy.evaluate(context)
            if expected != decision.strategy_evaluation:
                raise RangeMeanReversionResearchError("stateful strategy evaluation differs")
            equity = self.configuration.research_equity_quote
            portfolio = RiskPortfolioSnapshot(key[0], "USDT", equity, equity, equity, ())
            risk = RiskManager(self.configuration.risk_manager_configuration).evaluate(
                expected, portfolio
            )
            safety = OperationalSafetyGate(self.configuration.safety_configuration).evaluate(
                risk, _research_health(context, self.configuration.safety_assumptions)
            )
            if risk != decision.risk_decision or safety != decision.safety_decision:
                raise RangeMeanReversionResearchError("Risk or Safety authority differs")
        if (
            len(counts_by_series)
            != len(self.configuration.source_ids) * len(self.configuration.symbols)
            or any(count != expected_per_series for count in counts_by_series.values())
            or any(time != self.configuration.end for time in previous_by_series.values())
        ):
            raise RangeMeanReversionResearchError("decision series is incomplete")
        excluded = tuple(
            item.identity
            for item in self.decisions
            if item.is_research_admissible_entry
            and _is_warmup_excluded(self.configuration, item.identity)
        )
        identities = tuple(
            item.identity
            for item in self.decisions
            if item.is_research_admissible_entry
            and not _is_warmup_excluded(self.configuration, item.identity)
        )
        if excluded != self.warmup_excluded_entry_identities:
            raise RangeMeanReversionResearchError("warm-up exclusion identities differ")
        if identities != self.research_admissible_entry_identities:
            raise RangeMeanReversionResearchError("admissible identities differ")
        digest = _integrity_seal(
            "range-corpus-digest",
            (
                self.configuration,
                self.decisions,
                self.historical_contexts,
            ),
            excluded_names=frozenset(),
        )
        if (
            self.corpus_digest != digest
            or self.corpus_id != f"range-mean-reversion-research-corpus:{digest}"
        ):
            raise RangeMeanReversionResearchError("corpus digest differs")

    def to_json(self) -> str:
        self.validate()
        return dumps(
            _canonical_integrity_value(self, excluded_names=frozenset()),
            sort_keys=True,
            separators=(",", ":"),
        )


class RangeMeanReversionHistoricalCorpusGenerator:
    """Replay existing authorities with one stateful default strategy per run."""

    def __init__(self, store: HistoricalCandleStore) -> None:
        self._store = store

    def generate(
        self,
        configuration: RangeMeanReversionResearchConfiguration,
        *,
        replay_speed: ReplaySpeed = ReplaySpeed.X100,
    ) -> RangeMeanReversionHistoricalResearchCorpus:
        configuration.validate()
        if any(is_locked_weex_source(source) for source in configuration.source_ids) and (
            not isinstance(self._store, LockedWeexResearchCorpusStore)
            or configuration.source_ids != (self._store.source_id,)
        ):
            raise RangeMeanReversionResearchError(
                "qualified WEEX history requires its locked store"
            )
        if isinstance(self._store, LockedWeexResearchCorpusStore) and (
            configuration.warmup_exclusions
            != locked_warmup_exclusions(self._store, configuration.symbols)
        ):
            raise RangeMeanReversionResearchError("locked warm-up exclusions differ")
        config = deepcopy(configuration)
        specs = tuple(
            ReplaySeries(source, symbol, frame, config.start, config.end)
            for source in config.source_ids
            for symbol in config.symbols
            for frame in _TIMEFRAMES
        )
        replay = PacedReplayController(
            ReplayEngine(HistoricalReplaySource(self._store)).prepare(
                specs, clock=FrozenClock(config.start)
            ),
            replay_speed,
            timer=_NoWaitReplayTimer(),
        )
        features = FeatureEngine()
        structures = MarketStructureEngine()
        regimes = TechnicalRegimeEngine()
        composer = MultiTimeframeRegimeComposer()
        strategy = RangeMeanReversionStrategy()
        risk = RiskManager(config.risk_manager_configuration)
        safety = OperationalSafetyGate(config.safety_configuration)
        journal: InMemoryDecisionJournal[object, object] = InMemoryDecisionJournal()
        decisions: list[RangeMeanReversionDecisionObservation] = []
        contexts: list[HistoricalStrategyContextObservation] = []
        for batch in replay.run():
            observed = observed_candles_from_replay(batch)
            feature_batch = features.process(observed)
            structure_batch = structures.process(observed)
            technical_batch = regimes.process(feature_batch, structure_batch)
            mtf_batch = composer.process(technical_batch)
            assembled = assemble_strategy_contexts(
                observed, feature_batch, structure_batch, mtf_batch
            )
            if not config.start < batch.observed_at <= config.end:
                continue
            entries: list[DecisionJournalEntry[object, object]] = []
            for context in assembled:
                evaluation = strategy.evaluate(context)
                equity = config.research_equity_quote
                portfolio = RiskPortfolioSnapshot(
                    context.observed_at, "USDT", equity, equity, equity, ()
                )
                risk_decision = risk.evaluate(evaluation, portfolio)
                safety_decision = safety.evaluate(
                    risk_decision, _research_health(context, config.safety_assumptions)
                )
                entry = DecisionJournalEntry(context, evaluation, risk_decision, safety_decision)
                entry.validate()
                entries.append(entry)
                decisions.append(RangeMeanReversionDecisionObservation(entry))
                contexts.append(HistoricalStrategyContextObservation(context))
            if entries:
                journal.append_batch(DecisionJournalBatch(batch.observed_at, tuple(entries)))
        if tuple(item.decision for item in decisions) != journal.snapshot().entries:
            raise RangeMeanReversionResearchError("decision journal and corpus differ")
        return RangeMeanReversionHistoricalResearchCorpus(config, tuple(decisions), tuple(contexts))


def _is_warmup_excluded(
    configuration: RangeMeanReversionResearchConfiguration, identity: DecisionIdentity
) -> bool:
    return any(
        symbol == identity.symbol and interval.start < identity.observed_at <= interval.end
        for symbol, interval in configuration.warmup_exclusions
    )


def locked_warmup_exclusions(
    store: LockedWeexResearchCorpusStore, symbols: tuple[TradingSymbol, ...]
) -> tuple[tuple[TradingSymbol, CorpusInterval], ...]:
    """Declare exactly the immutable manifest's admission-exclusion intervals."""
    return tuple(
        (symbol, interval)
        for symbol in symbols
        for interval in store.manifest.warmup_exclusions[f"{symbol.base}{symbol.quote}"]
    )
