"""Research-only R-space projection of sealed paired Trend Pullback outcomes."""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from json import dumps

from adaptive_trading_bot.backtest import ExecutionCostConfiguration
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.research.paired_exit_replay import PairedExitReplayStatus
from adaptive_trading_bot.research.trend_pullback_corpus import (
    ResearchAdmissionConfiguration,
    TrendPullbackHistoricalResearchCorpus,
)
from adaptive_trading_bot.research.trend_pullback_exit_robustness import (
    OverlapAudit,
    PairedDeltaSummary,
    PairedPopulationSummary,
    RobustnessRate,
    TrendPullbackExitRobustnessReport,
    _canonical,
)
from adaptive_trading_bot.research.weex_historical_corpus import (
    QUALIFIED_DATASET_MANIFEST_SHA256,
    LockedWeexResearchCorpusStore,
)
from adaptive_trading_bot.storage import SourceId

_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
PROJECTION_SCHEMA_VERSION = "trend-pullback-signal-edge-v1"
FROZEN_SOURCE_ID = SourceId("weex-v3-last-e5b0c4035a19d5a80ca7")
FROZEN_DATASET_VERSION = "weex-v1-5m-last-a629a29b580b2e103adba918"
FROZEN_MANIFEST_SHA256 = "98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95"
FROZEN_RESEARCH_EQUITY_QUOTE = Decimal("10000")
FROZEN_FOLLOW_UP_DURATION = timedelta(hours=168)
FROZEN_EXECUTION_COSTS = ExecutionCostConfiguration(
    spread_bps=Decimal("2"),
    slippage_bps=Decimal("2"),
    fee_bps=Decimal("10"),
)
DEPENDENCE_WARNINGS = (
    "Raw candidate count is not an independent sample size.",
    "Overlapping positions may share the same market path.",
    "Total realized net R is descriptive, not portfolio PnL or return.",
    "No significance or confidence claim follows from raw count alone.",
)
AUTHORITY_EXCLUSIONS = (
    "No leveraged performance, liquidation realism, or complete historical funding.",
    "No portfolio performance, independent-trade proof, or future profitability proof.",
    "No Paper, Shadow, Live, or trading authority.",
)


class SignalEdgeProjectionError(ValueError):
    """Sealed report, frozen protocol, or economic projection is inconsistent."""


class SignalEdgeRunId(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class ProfitFactorState(StrEnum):
    FINITE = "FINITE"
    INFINITE = "INFINITE"
    UNAVAILABLE = "UNAVAILABLE"


class SignalEdgeUnitStatus(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"
    INSUFFICIENT = "INSUFFICIENT"


class SignalEdgeScreenStatus(StrEnum):
    SIGNAL_EDGE_SCREEN_POSITIVE = "SIGNAL_EDGE_SCREEN_POSITIVE"
    SIGNAL_EDGE_SCREEN_NEGATIVE = "SIGNAL_EDGE_SCREEN_NEGATIVE"
    SIGNAL_EDGE_SCREEN_MIXED = "SIGNAL_EDGE_SCREEN_MIXED"
    SIGNAL_EDGE_SCREEN_INSUFFICIENT = "SIGNAL_EDGE_SCREEN_INSUFFICIENT"


class EconomicStratificationDimension(StrEnum):
    ENTRY_SIDE = "ENTRY_SIDE"
    UTC_ENTRY_MONTH = "UTC_ENTRY_MONTH"


@dataclass(frozen=True, slots=True)
class SignalEdgeRunDefinition:
    run_id: SignalEdgeRunId
    symbol: TradingSymbol
    decision_start: datetime
    decision_end: datetime
    follow_up_boundary: datetime

    def __post_init__(self) -> None:
        start = require_utc(self.decision_start, field="signal-edge decision start")
        end = require_utc(self.decision_end, field="signal-edge decision end")
        boundary = require_utc(self.follow_up_boundary, field="signal-edge follow-up boundary")
        if start >= end or boundary != end + FROZEN_FOLLOW_UP_DURATION:
            raise SignalEdgeProjectionError("signal-edge run window or follow-up is invalid")


FROZEN_RUNS = (
    SignalEdgeRunDefinition(
        SignalEdgeRunId.A,
        TradingSymbol("BTC", "USDT"),
        datetime(2024, 7, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 8, tzinfo=UTC),
    ),
    SignalEdgeRunDefinition(
        SignalEdgeRunId.B,
        TradingSymbol("ETH", "USDT"),
        datetime(2024, 7, 1, tzinfo=UTC),
        datetime(2024, 12, 25, tzinfo=UTC),
        datetime(2025, 1, 1, tzinfo=UTC),
    ),
    SignalEdgeRunDefinition(
        SignalEdgeRunId.C,
        TradingSymbol("ETH", "USDT"),
        datetime(2025, 4, 2, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 8, tzinfo=UTC),
    ),
)


@dataclass(frozen=True, slots=True)
class CandidateEconomicOutcome:
    """One closed candidate's authoritative PnL divided by authorized risk."""

    identity: DecisionIdentity
    entry_side: str
    utc_entry_month: str
    exit_fill_at: datetime
    realized_net_pnl: Decimal
    approved_risk_quote: Decimal
    net_r: Decimal = field(init=False)

    def __post_init__(self) -> None:
        require_utc(self.exit_fill_at, field="candidate exit fill")
        if self.entry_side not in {"LONG", "SHORT"}:
            raise SignalEdgeProjectionError("candidate entry side is invalid")
        if self.utc_entry_month != self.identity.observed_at.strftime("%Y-%m"):
            raise SignalEdgeProjectionError("candidate entry month is inconsistent")
        if (
            not isinstance(self.approved_risk_quote, Decimal)
            or not self.approved_risk_quote.is_finite()
            or self.approved_risk_quote <= 0
            or not isinstance(self.realized_net_pnl, Decimal)
            or not self.realized_net_pnl.is_finite()
        ):
            raise SignalEdgeProjectionError("candidate PnL or authorized risk is invalid")
        with localcontext(_CONTEXT):
            object.__setattr__(self, "net_r", self.realized_net_pnl / self.approved_risk_quote)


@dataclass(frozen=True, slots=True)
class CandidateAbsoluteEconomics:
    outcome_denominator: int
    win_count: int
    breakeven_count: int
    loss_count: int
    win_rate: RobustnessRate
    positive_net_r: Decimal
    negative_net_r: Decimal
    total_realized_net_r: Decimal
    mean_net_r: Decimal | None
    median_net_r: Decimal | None
    minimum_net_r: Decimal | None
    maximum_net_r: Decimal | None
    average_winning_r: Decimal | None
    average_losing_r: Decimal | None
    profit_factor_state: ProfitFactorState
    profit_factor: Decimal | None


@dataclass(frozen=True, slots=True)
class CandidateEconomicStratum:
    dimension: EconomicStratificationDimension
    value: str
    economics: CandidateAbsoluteEconomics


@dataclass(frozen=True, slots=True)
class EconomicPopulation:
    paired_population: PairedPopulationSummary
    entry_unfilled_rate: RobustnessRate
    economic_outcome_denominator: int


@dataclass(frozen=True, slots=True)
class SignalEdgeRunProjection:
    report_digest: str
    population: EconomicPopulation
    outcomes: tuple[CandidateEconomicOutcome, ...]
    candidate_economics: CandidateAbsoluteEconomics
    maximum_chronological_losing_streak: int
    strata: tuple[CandidateEconomicStratum, ...]
    paired_candidate_minus_baseline_deltas: PairedDeltaSummary
    overlap_audit: OverlapAudit
    economically_evaluable: bool
    unit_status: SignalEdgeUnitStatus
    run_definition: SignalEdgeRunDefinition | None = None
    dataset_version: str | None = None
    manifest_sha256: str | None = None
    dependence_warnings: tuple[str, ...] = DEPENDENCE_WARNINGS
    authority_exclusions: tuple[str, ...] = AUTHORITY_EXCLUSIONS
    schema_version: str = PROJECTION_SCHEMA_VERSION
    is_portfolio_performance: bool = False
    is_trading_authority: bool = False

    def to_json(self) -> str:
        return dumps(_canonical(self, set()), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class SignalEdgeScreenProjection:
    runs: tuple[SignalEdgeRunProjection, SignalEdgeRunProjection, SignalEdgeRunProjection]
    status: SignalEdgeScreenStatus
    schema_version: str = PROJECTION_SCHEMA_VERSION
    authority_exclusions: tuple[str, ...] = AUTHORITY_EXCLUSIONS

    def to_json(self) -> str:
        return dumps(_canonical(self, set()), sort_keys=True, separators=(",", ":"))


def project_run(report: TrendPullbackExitRobustnessReport) -> SignalEdgeRunProjection:
    """Project one sealed paired report without re-running fills or accounting."""

    if not isinstance(report, TrendPullbackExitRobustnessReport):
        raise TypeError("signal-edge input must be a sealed robustness report")
    report.validate()
    outcomes: list[CandidateEconomicOutcome] = []
    for observation in report.observations:
        result = observation.paired_result
        if result.status is not PairedExitReplayStatus.REPLAYED:
            continue
        candidate = result.ema50_candidate
        if candidate is None:
            raise SignalEdgeProjectionError("replayed candidate branch is absent")
        if not candidate.position_closed:
            continue
        if candidate.realized_net_pnl is None or candidate.exit_fill is None:
            raise SignalEdgeProjectionError("closed candidate lacks accounting or exit fill")
        outcomes.append(
            CandidateEconomicOutcome(
                identity=deepcopy(observation.identity),
                entry_side=observation.entry_side,
                utc_entry_month=observation.utc_entry_month,
                exit_fill_at=candidate.exit_fill.fill_at,
                realized_net_pnl=candidate.realized_net_pnl,
                approved_risk_quote=result.entry_intent.approved_risk_quote,
            )
        )
    count = len(outcomes)
    population = report.population
    if count != population.candidate_closed_count:
        raise SignalEdgeProjectionError("candidate closure denominator contradicts sealed report")
    absolute = _economics(tuple(outcomes))
    closed_rate = population.candidate_closed_rate.value
    evaluable = _is_evaluable(count, closed_rate)
    unit = _unit_status(absolute, evaluable)
    return SignalEdgeRunProjection(
        report_digest=report.report_digest,
        population=EconomicPopulation(
            paired_population=deepcopy(population),
            entry_unfilled_rate=_rate(population.entry_unfilled_count, population.candidate_count),
            economic_outcome_denominator=count,
        ),
        outcomes=tuple(outcomes),
        candidate_economics=absolute,
        maximum_chronological_losing_streak=_losing_streak(tuple(outcomes)),
        strata=_strata(tuple(outcomes)),
        paired_candidate_minus_baseline_deltas=deepcopy(report.deltas),
        overlap_audit=deepcopy(report.overlap_audit),
        economically_evaluable=evaluable,
        unit_status=unit,
    )


def project_frozen_run(
    store: LockedWeexResearchCorpusStore,
    corpus: TrendPullbackHistoricalResearchCorpus,
    report: TrendPullbackExitRobustnessReport,
    definition: SignalEdgeRunDefinition,
) -> SignalEdgeRunProjection:
    """Enforce the frozen A/B/C protocol before projecting a future study run."""

    if not isinstance(store, LockedWeexResearchCorpusStore):
        raise SignalEdgeProjectionError("frozen run requires the locked WEEX corpus store")
    if (
        store.manifest.dataset_version != FROZEN_DATASET_VERSION
        or store.source_id != FROZEN_SOURCE_ID
        or QUALIFIED_DATASET_MANIFEST_SHA256.get(FROZEN_DATASET_VERSION) != FROZEN_MANIFEST_SHA256
    ):
        raise SignalEdgeProjectionError("frozen run dataset identity is not qualified")
    if definition not in FROZEN_RUNS:
        raise SignalEdgeProjectionError("run definition is not predeclared")
    corpus.validate()
    report.validate()
    expected_admission = ResearchAdmissionConfiguration(
        source_ids=(FROZEN_SOURCE_ID,),
        symbols=(definition.symbol,),
        start=definition.decision_start,
        end=definition.decision_end,
        research_equity_quote=FROZEN_RESEARCH_EQUITY_QUOTE,
    )
    if (
        corpus.configuration != expected_admission
        or report.source_corpus_id != corpus.corpus_id
        or report.source_corpus_digest != corpus.corpus_digest
        or report.configuration.follow_up_duration != FROZEN_FOLLOW_UP_DURATION
        or report.configuration.execution_costs != FROZEN_EXECUTION_COSTS
        or tuple(item.identity for item in report.observations)
        != corpus.research_admissible_entry_identities
    ):
        raise SignalEdgeProjectionError("report does not match the frozen signal-edge run")
    return replace(
        project_run(report),
        run_definition=definition,
        dataset_version=FROZEN_DATASET_VERSION,
        manifest_sha256=FROZEN_MANIFEST_SHA256,
    )


def project_frozen_screen(
    units: tuple[
        tuple[
            LockedWeexResearchCorpusStore,
            TrendPullbackHistoricalResearchCorpus,
            TrendPullbackExitRobustnessReport,
        ],
        tuple[
            LockedWeexResearchCorpusStore,
            TrendPullbackHistoricalResearchCorpus,
            TrendPullbackExitRobustnessReport,
        ],
        tuple[
            LockedWeexResearchCorpusStore,
            TrendPullbackHistoricalResearchCorpus,
            TrendPullbackExitRobustnessReport,
        ],
    ],
) -> SignalEdgeScreenProjection:
    """Classify exactly A, B, C from validated sealed research authorities."""

    runs = tuple(
        project_frozen_run(store, corpus, report, definition)
        for definition, (store, corpus, report) in zip(FROZEN_RUNS, units, strict=True)
    )
    return SignalEdgeScreenProjection(
        runs=(runs[0], runs[1], runs[2]),
        status=_screen_status(tuple(run.unit_status for run in runs)),
    )


def _economics(outcomes: tuple[CandidateEconomicOutcome, ...]) -> CandidateAbsoluteEconomics:
    values = tuple(item.net_r for item in outcomes)
    winning = tuple(value for value in values if value > 0)
    losing = tuple(value for value in values if value < 0)
    with localcontext(_CONTEXT):
        positive = sum(winning, Decimal(0))
        negative = sum(losing, Decimal(0))
        total = positive + negative
        if negative < 0:
            profit_factor_state = ProfitFactorState.FINITE
            profit_factor = positive / abs(negative)
        elif positive > 0:
            profit_factor_state = ProfitFactorState.INFINITE
            profit_factor = None
        else:
            profit_factor_state = ProfitFactorState.UNAVAILABLE
            profit_factor = None
    ordered = tuple(sorted(values))
    return CandidateAbsoluteEconomics(
        outcome_denominator=len(values),
        win_count=len(winning),
        breakeven_count=len(values) - len(winning) - len(losing),
        loss_count=len(losing),
        win_rate=_rate(len(winning), len(values)),
        positive_net_r=positive,
        negative_net_r=negative,
        total_realized_net_r=total,
        mean_net_r=_mean(values),
        median_net_r=_median(ordered),
        minimum_net_r=ordered[0] if ordered else None,
        maximum_net_r=ordered[-1] if ordered else None,
        average_winning_r=_mean(winning),
        average_losing_r=_mean(losing),
        profit_factor_state=profit_factor_state,
        profit_factor=profit_factor,
    )


def _strata(outcomes: tuple[CandidateEconomicOutcome, ...]) -> tuple[CandidateEconomicStratum, ...]:
    result: list[CandidateEconomicStratum] = []
    for dimension in EconomicStratificationDimension:
        groups: dict[str, list[CandidateEconomicOutcome]] = defaultdict(list)
        for item in outcomes:
            value = (
                item.entry_side
                if dimension is EconomicStratificationDimension.ENTRY_SIDE
                else item.utc_entry_month
            )
            groups[value].append(item)
        for value in sorted(groups):
            result.append(
                CandidateEconomicStratum(dimension, value, _economics(tuple(groups[value])))
            )
    return tuple(result)


def _is_evaluable(closed_count: int, closed_rate: Decimal | None) -> bool:
    return closed_count >= 10 and closed_rate is not None and closed_rate >= Decimal("0.80")


def _losing_streak(outcomes: tuple[CandidateEconomicOutcome, ...]) -> int:
    ordered = sorted(outcomes, key=lambda item: (item.exit_fill_at, item.identity.sort_key))
    current = maximum = 0
    for item in ordered:
        current = current + 1 if item.net_r < 0 else 0
        maximum = max(maximum, current)
    return maximum


def _unit_status(economics: CandidateAbsoluteEconomics, evaluable: bool) -> SignalEdgeUnitStatus:
    if not evaluable:
        return SignalEdgeUnitStatus.INSUFFICIENT
    mean = economics.mean_net_r
    median = economics.median_net_r
    if mean is None or median is None:
        raise SignalEdgeProjectionError("evaluable run lacks realized outcomes")
    factor_above_one = economics.profit_factor_state is ProfitFactorState.INFINITE or (
        economics.profit_factor is not None and economics.profit_factor > 1
    )
    factor_at_most_one = (
        economics.profit_factor_state is ProfitFactorState.FINITE
        and economics.profit_factor is not None
        and economics.profit_factor <= 1
    )
    if mean > 0 and factor_above_one and median >= 0:
        return SignalEdgeUnitStatus.POSITIVE
    if mean <= 0 and factor_at_most_one:
        return SignalEdgeUnitStatus.NEGATIVE
    return SignalEdgeUnitStatus.MIXED


def _screen_status(units: tuple[SignalEdgeUnitStatus, ...]) -> SignalEdgeScreenStatus:
    if len(units) != 3:
        raise SignalEdgeProjectionError("screen requires exactly three predeclared runs")
    if SignalEdgeUnitStatus.INSUFFICIENT in units:
        return SignalEdgeScreenStatus.SIGNAL_EDGE_SCREEN_INSUFFICIENT
    positive = units.count(SignalEdgeUnitStatus.POSITIVE)
    negative = units.count(SignalEdgeUnitStatus.NEGATIVE)
    if positive >= 2 and negative == 0:
        return SignalEdgeScreenStatus.SIGNAL_EDGE_SCREEN_POSITIVE
    if negative >= 2:
        return SignalEdgeScreenStatus.SIGNAL_EDGE_SCREEN_NEGATIVE
    return SignalEdgeScreenStatus.SIGNAL_EDGE_SCREEN_MIXED


def _rate(numerator: int, denominator: int) -> RobustnessRate:
    with localcontext(_CONTEXT):
        return RobustnessRate(
            numerator,
            denominator,
            Decimal(numerator) / denominator if denominator else None,
        )


def _mean(values: tuple[Decimal, ...]) -> Decimal | None:
    if not values:
        return None
    with localcontext(_CONTEXT):
        return sum(values, Decimal(0)) / len(values)


def _median(ordered: tuple[Decimal, ...]) -> Decimal | None:
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    with localcontext(_CONTEXT):
        return (ordered[middle - 1] + ordered[middle]) / 2
