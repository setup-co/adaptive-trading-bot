"""Sealed, non-economic attribution of Range Mean Reversion research admissions."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hmac import compare_digest
from json import dumps
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from adaptive_trading_bot.research.range_scarcity_ledger import ScarcityLedgerStore

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.research.range_mean_reversion_corpus import (
    RangeMeanReversionHistoricalCorpusGenerator,
    RangeMeanReversionHistoricalResearchCorpus,
    RangeMeanReversionResearchConfiguration,
    _is_warmup_excluded,
    locked_warmup_exclusions,
)
from adaptive_trading_bot.research.trend_pullback_corpus import (
    _canonical_integrity_value,
    _integrity_seal,
)
from adaptive_trading_bot.research.weex_historical_corpus import (
    QUALIFIED_DATASET_MANIFEST_SHA256,
    LockedWeexResearchCorpusStore,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyReasonCode,
    OperationalSafetyStatus,
    RiskDecisionStatus,
    RiskReasonCode,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import (
    CandidateAction,
    RangeMeanReversionReasonCode,
    RangeMeanReversionRuleAudit,
)
from adaptive_trading_bot.strategies.range_context import (
    RANGE_PARTIAL_EVIDENCE_RETENTION_V1,
    RangeReasonCode,
    RangeStatus,
)

SCHEMA_VERSION = "range-mean-reversion-scarcity-v2"
FROZEN_SCHEMA_VERSION = "range-mean-reversion-scarcity-v1"
PROTOCOL_VERSION = "RANGE_MEAN_REVERSION_CANDIDATE_SCARCITY_V1"
SOURCE_ID = SourceId("weex-v3-last-e5b0c4035a19d5a80ca7")
DATASET_VERSION = "weex-v1-5m-last-a629a29b580b2e103adba918"
MANIFEST_SHA256 = "98705bd49ec05f912ef6c0257ea9a9886b706f3c80ca84c82faace4e20415e95"
RESEARCH_EQUITY = Decimal("10000")


class RangeScarcityError(ValueError):
    """A sealed source, attribution, or frozen run failed closed."""


class TerminalStage(StrEnum):
    CONTEXT_NOT_READY = "CONTEXT_NOT_READY"
    RANGE_NOT_VALID = "RANGE_NOT_VALID"
    VALID_RANGE_STRATEGY_NO_TRADE = "VALID_RANGE_STRATEGY_NO_TRADE"
    CANDIDATE_RISK_BLOCKED = "CANDIDATE_RISK_BLOCKED"
    CANDIDATE_SAFETY_BLOCKED = "CANDIDATE_SAFETY_BLOCKED"
    CANDIDATE_WARMUP_EXCLUDED = "CANDIDATE_WARMUP_EXCLUDED"
    RESEARCH_ADMISSIBLE = "RESEARCH_ADMISSIBLE"


class ScarcitySummary(StrEnum):
    ZERO_STRATEGY_CANDIDATES = "ZERO_STRATEGY_CANDIDATES"
    CANDIDATES_EXIST_BUT_NONE_ADMITTED = "CANDIDATES_EXIST_BUT_NONE_ADMITTED"
    RESEARCH_ADMISSIBLE_CANDIDATES_EXIST = "RESEARCH_ADMISSIBLE_CANDIDATES_EXIST"


def terminal_stage(
    ready: bool,
    range_status: RangeStatus,
    action: CandidateAction,
    risk_status: RiskDecisionStatus,
    safety_status: OperationalSafetyStatus,
    warmup_excluded: bool,
) -> TerminalStage:
    """Apply the predeclared mutually-exclusive attribution order, not causal inference."""
    if not isinstance(ready, bool) or not isinstance(warmup_excluded, bool):
        raise RangeScarcityError("readiness and warm-up flags must be booleans")
    if not isinstance(range_status, RangeStatus) or not isinstance(action, CandidateAction):
        raise RangeScarcityError("unknown range status or strategy action")
    if not isinstance(risk_status, RiskDecisionStatus) or not isinstance(
        safety_status, OperationalSafetyStatus
    ):
        raise RangeScarcityError("unknown Risk or Safety status")
    if action is CandidateAction.NO_TRADE:
        if risk_status is not RiskDecisionStatus.NO_ACTION or safety_status not in {
            OperationalSafetyStatus.NO_ACTION,
            OperationalSafetyStatus.SAFE_MODE,
        }:
            raise RangeScarcityError("NO_TRADE admission chain is inconsistent")
    elif risk_status is RiskDecisionStatus.REJECTED:
        if safety_status is not OperationalSafetyStatus.BLOCKED_BY_RISK:
            raise RangeScarcityError("Risk rejection must block Safety")
    elif risk_status is not RiskDecisionStatus.APPROVED or safety_status not in {
        OperationalSafetyStatus.CLEARED,
        OperationalSafetyStatus.SAFE_MODE,
    }:
        raise RangeScarcityError("candidate admission chain is inconsistent")
    if not ready:
        return TerminalStage.CONTEXT_NOT_READY
    if range_status is not RangeStatus.VALID_RANGE:
        return TerminalStage.RANGE_NOT_VALID
    if action is CandidateAction.NO_TRADE:
        return TerminalStage.VALID_RANGE_STRATEGY_NO_TRADE
    if risk_status is RiskDecisionStatus.REJECTED:
        return TerminalStage.CANDIDATE_RISK_BLOCKED
    if safety_status is OperationalSafetyStatus.SAFE_MODE:
        return TerminalStage.CANDIDATE_SAFETY_BLOCKED
    if warmup_excluded:
        return TerminalStage.CANDIDATE_WARMUP_EXCLUDED
    return TerminalStage.RESEARCH_ADMISSIBLE


@dataclass(frozen=True, slots=True)
class ObservationAttribution:
    identity: DecisionIdentity
    context_ready: bool
    range_status: RangeStatus
    range_reasons: tuple[RangeReasonCode, ...]
    action: CandidateAction
    strategy_reasons_against: tuple[RangeMeanReversionReasonCode, ...]
    risk_status: RiskDecisionStatus
    risk_reasons: tuple[RiskReasonCode, ...]
    safety_status: OperationalSafetyStatus
    safety_reasons: tuple[OperationalSafetyReasonCode, ...]
    warmup_excluded: bool
    terminal: TerminalStage = field(init=False)

    def __post_init__(self) -> None:
        self.validate_reasons()
        object.__setattr__(
            self,
            "terminal",
            terminal_stage(
                self.context_ready,
                self.range_status,
                self.action,
                self.risk_status,
                self.safety_status,
                self.warmup_excluded,
            ),
        )

    def validate_reasons(self) -> None:
        for values, kind in (
            (self.range_reasons, RangeReasonCode),
            (self.strategy_reasons_against, RangeMeanReversionReasonCode),
            (self.risk_reasons, RiskReasonCode),
            (self.safety_reasons, OperationalSafetyReasonCode),
        ):
            if not isinstance(values, tuple) or any(
                not isinstance(value, kind) for value in values
            ):
                raise RangeScarcityError("unknown or malformed reason code")

    def validate(self) -> None:
        self.validate_reasons()
        if self.terminal is not terminal_stage(
            self.context_ready,
            self.range_status,
            self.action,
            self.risk_status,
            self.safety_status,
            self.warmup_excluded,
        ):
            raise RangeScarcityError("terminal attribution contradicts sealed facts")


@dataclass(frozen=True, slots=True)
class ScarcityCounts:
    total_observations: int
    context_ready: int
    context_not_ready: int
    range_statuses: tuple[tuple[RangeStatus, int], ...]
    range_reasons: tuple[tuple[RangeReasonCode, int], ...]
    actions: tuple[tuple[CandidateAction, int], ...]
    no_trade_reasons: tuple[tuple[RangeMeanReversionReasonCode, int], ...]
    valid_range_no_trade_reasons: tuple[tuple[RangeMeanReversionReasonCode, int], ...]
    candidate_utc_months: tuple[tuple[str, int], ...]
    candidates_inside_warmup: int
    candidates_outside_warmup: int
    risk_statuses: tuple[tuple[RiskDecisionStatus, int], ...]
    safety_statuses: tuple[tuple[OperationalSafetyStatus, int], ...]
    risk_reasons: tuple[tuple[RiskReasonCode, int], ...]
    safety_reasons: tuple[tuple[OperationalSafetyReasonCode, int], ...]
    otherwise_admissible_before_warmup: int
    otherwise_admissible_warmup_excluded: int
    final_research_admissible: int
    terminals: tuple[tuple[TerminalStage, int], ...]

    @property
    def strategy_candidate_count(self) -> int:
        return sum(
            count for action, count in self.actions if action is not CandidateAction.NO_TRADE
        )


def _counts(rows: tuple[ObservationAttribution, ...]) -> ScarcityCounts:
    candidates = tuple(row for row in rows if row.action is not CandidateAction.NO_TRADE)
    no_trades = tuple(row for row in rows if row.action is CandidateAction.NO_TRADE)
    months = sorted({row.identity.observed_at.strftime("%Y-%m") for row in candidates})
    result = ScarcityCounts(
        len(rows),
        sum(row.context_ready for row in rows),
        sum(not row.context_ready for row in rows),
        tuple((status, sum(row.range_status is status for row in rows)) for status in RangeStatus),
        tuple(
            (reason, sum(row.range_reasons.count(reason) for row in rows))
            for reason in RangeReasonCode
        ),
        tuple((action, sum(row.action is action for row in rows)) for action in CandidateAction),
        tuple(
            (reason, sum(row.strategy_reasons_against.count(reason) for row in no_trades))
            for reason in RangeMeanReversionReasonCode
        ),
        tuple(
            (
                reason,
                sum(
                    row.strategy_reasons_against.count(reason)
                    if row.range_status is RangeStatus.VALID_RANGE
                    else 0
                    for row in no_trades
                ),
            )
            for reason in RangeMeanReversionReasonCode
        ),
        tuple(
            (month, sum(row.identity.observed_at.strftime("%Y-%m") == month for row in candidates))
            for month in months
        ),
        sum(row.warmup_excluded for row in candidates),
        sum(not row.warmup_excluded for row in candidates),
        tuple(
            (status, sum(row.risk_status is status for row in candidates))
            for status in (RiskDecisionStatus.APPROVED, RiskDecisionStatus.REJECTED)
        ),
        tuple(
            (status, sum(row.safety_status is status for row in candidates))
            for status in (
                OperationalSafetyStatus.CLEARED,
                OperationalSafetyStatus.SAFE_MODE,
                OperationalSafetyStatus.BLOCKED_BY_RISK,
            )
        ),
        tuple(
            (reason, sum(row.risk_reasons.count(reason) for row in candidates))
            for reason in RiskReasonCode
        ),
        tuple(
            (reason, sum(row.safety_reasons.count(reason) for row in candidates))
            for reason in OperationalSafetyReasonCode
        ),
        sum(
            row.risk_status is RiskDecisionStatus.APPROVED
            and row.safety_status is OperationalSafetyStatus.CLEARED
            for row in candidates
        ),
        sum(row.terminal is TerminalStage.CANDIDATE_WARMUP_EXCLUDED for row in rows),
        sum(row.terminal is TerminalStage.RESEARCH_ADMISSIBLE for row in rows),
        tuple((stage, sum(row.terminal is stage for row in rows)) for stage in TerminalStage),
    )
    if (
        result.context_ready + result.context_not_ready != result.total_observations
        or sum(value for _, value in result.range_statuses) != result.total_observations
        or sum(value for _, value in result.actions) != result.total_observations
        or sum(value for _, value in result.terminals) != result.total_observations
        or result.candidates_inside_warmup + result.candidates_outside_warmup
        != result.strategy_candidate_count
        or sum(value for _, value in result.risk_statuses) != result.strategy_candidate_count
        or sum(value for _, value in result.safety_statuses) != result.strategy_candidate_count
        or result.otherwise_admissible_warmup_excluded + result.final_research_admissible
        != result.otherwise_admissible_before_warmup
    ):
        raise RangeScarcityError("attribution counts do not reconcile")
    return result


@dataclass(frozen=True, slots=True)
class RangeScarcityReport:
    run_id: str
    corpus_id: str
    corpus_digest: str
    observations: tuple[ObservationAttribution, ...]
    counts: ScarcityCounts = field(init=False)
    report_digest: str = field(init=False)
    schema_version: str = field(init=False, default=SCHEMA_VERSION)
    partial_evidence_policy_version: str = field(
        init=False, default=RANGE_PARTIAL_EVIDENCE_RETENTION_V1
    )
    is_trading_authority: bool = field(init=False, default=False)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "observations", deepcopy(self.observations))
        object.__setattr__(self, "counts", _counts(self.observations))
        self._validate_facts()
        object.__setattr__(
            self,
            "report_digest",
            _integrity_seal(
                "range-scarcity-digest",
                self,
                excluded_names=frozenset({"report_digest", "integrity_seal"}),
            ),
        )
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "range-scarcity-report", self, excluded_names=frozenset({"integrity_seal"})
            ),
        )

    def _validate_facts(self) -> None:
        if (
            self.schema_version != SCHEMA_VERSION
            or self.partial_evidence_policy_version != RANGE_PARTIAL_EVIDENCE_RETENTION_V1
            or self.is_trading_authority
            or not self.run_id
            or self.corpus_id != f"range-mean-reversion-research-corpus:{self.corpus_digest}"
        ):
            raise RangeScarcityError("report identity or authority differs")
        keys = tuple(row.identity.sort_key for row in self.observations)
        if keys != tuple(sorted(set(keys))):
            raise RangeScarcityError("observations are not canonical and unique")
        for row in self.observations:
            row.validate()
        if self.counts != _counts(self.observations):
            raise RangeScarcityError("report counts differ from observations")

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "range-scarcity-report", self, excluded_names=frozenset({"integrity_seal"})
            ),
        ):
            raise RangeScarcityError("report seal mismatch")
        self._validate_facts()
        if self.report_digest != _integrity_seal(
            "range-scarcity-digest",
            self,
            excluded_names=frozenset({"report_digest", "integrity_seal"}),
        ):
            raise RangeScarcityError("report digest mismatch")

    def to_json(self) -> str:
        self.validate()
        return dumps(
            _canonical_integrity_value(self, excluded_names=frozenset()),
            sort_keys=True,
            separators=(",", ":"),
        )


def project_corpus(
    corpus: RangeMeanReversionHistoricalResearchCorpus, run_id: str
) -> RangeScarcityReport:
    """Use only the complete, sealed decision population; never replay trades."""
    if not isinstance(corpus, RangeMeanReversionHistoricalResearchCorpus):
        raise RangeScarcityError("sealed Range Mean Reversion corpus required")
    corpus.validate()
    rows: list[ObservationAttribution] = []
    for observation in corpus.decisions:
        decision = observation.decision
        context = decision.context
        evaluation = decision.strategy_evaluation
        audit = evaluation.rule_audit
        if not isinstance(audit, RangeMeanReversionRuleAudit):
            raise RangeScarcityError("Range Mean Reversion rule audit is unavailable")
        if audit.partial_evidence_policy_version != corpus.partial_evidence_policy_version:
            raise RangeScarcityError("Range policy provenance differs")
        if not isinstance(evaluation.reasons_against, tuple):
            raise RangeScarcityError("strategy counter-evidence is malformed")
        rows.append(
            ObservationAttribution(
                observation.identity,
                context.features_15m.is_fully_warmed
                and context.multi_timeframe_regime.is_fully_ready,
                audit.range_status,
                audit.range_reasons,
                evaluation.action,
                cast(tuple[RangeMeanReversionReasonCode, ...], evaluation.reasons_against),
                decision.risk_decision.status,
                decision.risk_decision.reasons,
                decision.safety_decision.status,
                decision.safety_decision.reasons,
                _is_warmup_excluded(corpus.configuration, observation.identity),
            )
        )
    report = RangeScarcityReport(run_id, corpus.corpus_id, corpus.corpus_digest, tuple(rows))
    if (
        tuple(row.identity for row in report.observations)
        != tuple(item.identity for item in corpus.decisions)
        or report.counts.final_research_admissible
        != len(corpus.research_admissible_entry_identities)
        or report.counts.otherwise_admissible_warmup_excluded
        != len(corpus.warmup_excluded_entry_identities)
    ):
        raise RangeScarcityError("candidate or warm-up identities contradict the corpus")
    return report


@dataclass(frozen=True, slots=True)
class DiagnosticRunDefinition:
    run_id: str
    symbol: TradingSymbol
    start: datetime
    end: datetime


FROZEN_DIAGNOSTIC_RUNS = (
    DiagnosticRunDefinition(
        "A",
        TradingSymbol("BTC", "USDT"),
        datetime(2024, 7, 1, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
    ),
    DiagnosticRunDefinition(
        "B",
        TradingSymbol("ETH", "USDT"),
        datetime(2024, 7, 1, tzinfo=UTC),
        datetime(2024, 12, 25, tzinfo=UTC),
    ),
    DiagnosticRunDefinition(
        "C",
        TradingSymbol("ETH", "USDT"),
        datetime(2025, 4, 2, tzinfo=UTC),
        datetime(2026, 1, 1, tzinfo=UTC),
    ),
)


def validate_frozen_unit(
    store: LockedWeexResearchCorpusStore,
    corpus: RangeMeanReversionHistoricalResearchCorpus,
    report: RangeScarcityReport,
    definition: DiagnosticRunDefinition,
) -> None:
    """Require exact locked WEEX identity and immutable-manifest warm-up policy."""
    _require_frozen_range_policy()
    if (
        not isinstance(store, LockedWeexResearchCorpusStore)
        or definition not in FROZEN_DIAGNOSTIC_RUNS
    ):
        raise RangeScarcityError("only a predeclared locked WEEX unit is authorized")
    if (
        store.manifest.dataset_version != DATASET_VERSION
        or store.source_id != SOURCE_ID
        or QUALIFIED_DATASET_MANIFEST_SHA256.get(DATASET_VERSION) != MANIFEST_SHA256
    ):
        raise RangeScarcityError("qualified dataset identity differs")
    expected = _frozen_configuration(store, definition)
    corpus.validate()
    report.validate()
    if corpus.configuration != expected or report != project_corpus(corpus, definition.run_id):
        raise RangeScarcityError("frozen run configuration or report differs")


def _frozen_configuration(
    store: LockedWeexResearchCorpusStore, definition: DiagnosticRunDefinition
) -> RangeMeanReversionResearchConfiguration:
    _require_frozen_range_policy()
    return RangeMeanReversionResearchConfiguration(
        (SOURCE_ID,),
        (definition.symbol,),
        definition.start,
        definition.end,
        RESEARCH_EQUITY,
        warmup_exclusions=locked_warmup_exclusions(store, (definition.symbol,)),
    )


def _require_frozen_range_policy() -> None:
    """Never reinterpret #157/#161 through a newly versioned detector default."""
    from adaptive_trading_bot.strategies import RangeMeanReversionStrategy
    from adaptive_trading_bot.strategies.range_context import ValidatedRangeConfiguration

    if RangeMeanReversionStrategy().strategy_version != "1.0.0" or hasattr(
        ValidatedRangeConfiguration(), "partial_evidence_policy_version"
    ):
        raise RangeScarcityError("frozen scarcity protocol rejects current Range policy")


def run_predeclared_diagnostic_unit(
    store: LockedWeexResearchCorpusStore,
    definition: DiagnosticRunDefinition,
    ledger: ScarcityLedgerStore,
    *,
    current_code_sha: str,
    process_session_id: str,
    now: Callable[[], datetime],
) -> tuple[RangeMeanReversionHistoricalResearchCorpus, RangeScarcityReport]:
    """Future-only gate: durably mark STARTED before any real corpus generation."""
    if (
        not isinstance(store, LockedWeexResearchCorpusStore)
        or definition not in FROZEN_DIAGNOSTIC_RUNS
    ):
        raise RangeScarcityError("only a predeclared locked WEEX unit is authorized")
    if (
        store.manifest.dataset_version != DATASET_VERSION
        or store.source_id != SOURCE_ID
        or QUALIFIED_DATASET_MANIFEST_SHA256.get(DATASET_VERSION) != MANIFEST_SHA256
    ):
        raise RangeScarcityError("qualified dataset identity differs")
    configuration = _frozen_configuration(store, definition)
    ledger_state = ledger.load()
    if (
        ledger_state.dataset_version != DATASET_VERSION
        or ledger_state.source_id != SOURCE_ID.value
        or ledger_state.manifest_sha256 != MANIFEST_SHA256
    ):
        raise RangeScarcityError("ledger dataset/source/manifest identity differs")
    ledger.start_unit(
        definition.run_id,
        current_code_sha=current_code_sha,
        process_session_id=process_session_id,
        at=now(),
    )
    try:
        corpus = RangeMeanReversionHistoricalCorpusGenerator(store).generate(configuration)
        report = project_corpus(corpus, definition.run_id)
        validate_frozen_unit(store, corpus, report, definition)
        ledger.complete_unit(
            definition.run_id,
            at=now(),
            corpus_digest=corpus.corpus_digest,
            report_digest=report.report_digest,
        )
        return corpus, report
    except Exception:
        # If even the failure write fails, STARTED remains durable and ambiguous.
        ledger.fail_unit(definition.run_id, at=now())
        raise


def classify_summary(candidate_count: int, admitted_count: int) -> ScarcitySummary:
    if candidate_count < 0 or admitted_count < 0 or admitted_count > candidate_count:
        raise RangeScarcityError("summary counts are inconsistent")
    if candidate_count == 0:
        return ScarcitySummary.ZERO_STRATEGY_CANDIDATES
    if admitted_count == 0:
        return ScarcitySummary.CANDIDATES_EXIST_BUT_NONE_ADMITTED
    return ScarcitySummary.RESEARCH_ADMISSIBLE_CANDIDATES_EXIST


@dataclass(frozen=True, slots=True)
class RangeScarcityProjection:
    run_ids: tuple[str, str, str]
    report_digests: tuple[str, str, str]
    total_observations: tuple[int, int, int]
    terminal_counts: tuple[tuple[tuple[TerminalStage, int], ...], ...]
    strategy_candidate_counts: tuple[int, int, int]
    final_research_admissible_counts: tuple[int, int, int]
    summary: ScarcitySummary
    schema_version: str = field(init=False, default=SCHEMA_VERSION)
    integrity_seal: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._validate_facts()
        object.__setattr__(
            self,
            "integrity_seal",
            _integrity_seal(
                "range-scarcity-projection", self, excluded_names=frozenset({"integrity_seal"})
            ),
        )

    def _validate_facts(self) -> None:
        if (
            self.schema_version != SCHEMA_VERSION
            or self.run_ids != ("A", "B", "C")
            or any(not isinstance(value, str) or len(value) != 64 for value in self.report_digests)
            or any(type(value) is not int or value < 0 for value in self.total_observations)
            or any(type(value) is not int or value < 0 for value in self.strategy_candidate_counts)
            or any(
                type(value) is not int or value < 0
                for value in self.final_research_admissible_counts
            )
            or any(len(row) != len(TerminalStage) for row in self.terminal_counts)
            or any(
                tuple(stage for stage, _ in row) != tuple(TerminalStage)
                or any(type(value) is not int or value < 0 for _, value in row)
                for row in self.terminal_counts
            )
            or any(
                sum(value for _, value in row) != total
                for row, total in zip(self.terminal_counts, self.total_observations, strict=True)
            )
            or any(
                dict(row)[TerminalStage.RESEARCH_ADMISSIBLE] != admitted
                for row, admitted in zip(
                    self.terminal_counts, self.final_research_admissible_counts, strict=True
                )
            )
            or any(
                admitted > candidates
                for admitted, candidates in zip(
                    self.final_research_admissible_counts,
                    self.strategy_candidate_counts,
                    strict=True,
                )
            )
            or self.summary
            is not classify_summary(
                sum(self.strategy_candidate_counts), sum(self.final_research_admissible_counts)
            )
        ):
            raise RangeScarcityError("cross-run projection is inconsistent")

    def validate(self) -> None:
        if not compare_digest(
            self.integrity_seal,
            _integrity_seal(
                "range-scarcity-projection", self, excluded_names=frozenset({"integrity_seal"})
            ),
        ):
            raise RangeScarcityError("cross-run projection seal mismatch")
        self._validate_facts()

    def to_json(self) -> str:
        self.validate()
        return dumps(
            _canonical_integrity_value(self, excluded_names=frozenset()),
            sort_keys=True,
            separators=(",", ":"),
        )


def project_frozen_summary(
    units: tuple[
        tuple[
            LockedWeexResearchCorpusStore,
            RangeMeanReversionHistoricalResearchCorpus,
            RangeScarcityReport,
        ],
        tuple[
            LockedWeexResearchCorpusStore,
            RangeMeanReversionHistoricalResearchCorpus,
            RangeScarcityReport,
        ],
        tuple[
            LockedWeexResearchCorpusStore,
            RangeMeanReversionHistoricalResearchCorpus,
            RangeScarcityReport,
        ],
    ],
) -> RangeScarcityProjection:
    for definition, (store, corpus, report) in zip(FROZEN_DIAGNOSTIC_RUNS, units, strict=True):
        validate_frozen_unit(store, corpus, report, definition)
    reports = tuple(unit[2] for unit in units)
    candidates = tuple(report.counts.strategy_candidate_count for report in reports)
    admitted = tuple(report.counts.final_research_admissible for report in reports)
    return RangeScarcityProjection(
        ("A", "B", "C"),
        (reports[0].report_digest, reports[1].report_digest, reports[2].report_digest),
        (
            reports[0].counts.total_observations,
            reports[1].counts.total_observations,
            reports[2].counts.total_observations,
        ),
        tuple(report.counts.terminals for report in reports),
        (candidates[0], candidates[1], candidates[2]),
        (admitted[0], admitted[1], admitted[2]),
        classify_summary(sum(candidates), sum(admitted)),
    )


def finalize_predeclared_diagnostic(
    ledger: ScarcityLedgerStore,
    units: tuple[
        tuple[
            LockedWeexResearchCorpusStore,
            RangeMeanReversionHistoricalResearchCorpus,
            RangeScarcityReport,
        ],
        tuple[
            LockedWeexResearchCorpusStore,
            RangeMeanReversionHistoricalResearchCorpus,
            RangeScarcityReport,
        ],
        tuple[
            LockedWeexResearchCorpusStore,
            RangeMeanReversionHistoricalResearchCorpus,
            RangeScarcityReport,
        ],
    ],
) -> RangeScarcityProjection:
    """Bind the final projection to all three durably completed ledger units."""
    projection = project_frozen_summary(units)
    state = ledger.load()
    for logged, (_, corpus, report) in zip(state.units, units, strict=True):
        if (
            logged.corpus_digest != corpus.corpus_digest
            or logged.report_digest != report.report_digest
        ):
            raise RangeScarcityError("ledger completion digests differ from sealed reports")
    from adaptive_trading_bot.research.range_scarcity_ledger import UnitExecutionState

    if any(unit.state is not UnitExecutionState.COMPLETED for unit in state.units):
        raise RangeScarcityError("cross-run ledger has unfinished units")
    # The projection seal is its deterministic digest; the ledger writes it atomically.
    ledger.finalize(projection.integrity_seal)
    return projection
