"""Read-only Dashboard projection over sealed Experience samples and the concrete estimator."""

from __future__ import annotations

from copy import deepcopy
from typing import Protocol, runtime_checkable

from adaptive_trading_bot.dashboard.experience_models import (
    DashboardExperienceContextPage,
    DashboardExperienceContextQuery,
    DashboardExperienceEstimate,
    DashboardExperienceEstimateQuery,
    DashboardExperienceHierarchyEvidence,
    _project_domain_key,
    project_experience_fingerprint,
)
from adaptive_trading_bot.experience import (
    ExperienceEstimate,
    ExperienceEstimationConfiguration,
    ExperienceEstimator,
    ExperienceTradeSampleSnapshot,
)

_SAMPLE_SNAPSHOT_SCHEMA_VERSION = "1.0.0"


class DashboardExperienceProviderError(RuntimeError):
    """Experience authority could not be projected safely for observation."""


@runtime_checkable
class ExperienceTradeSampleSnapshotSource(Protocol):
    """Narrow source of the current immutable process-local sample snapshot."""

    def snapshot(self) -> ExperienceTradeSampleSnapshot: ...


class DashboardExperienceProvider(Protocol):
    """Read-only Experience query boundary consumed by the API."""

    def contexts(
        self, query: DashboardExperienceContextQuery
    ) -> DashboardExperienceContextPage: ...

    def estimate(self, query: DashboardExperienceEstimateQuery) -> DashboardExperienceEstimate: ...


class SnapshotDashboardExperienceProvider:
    """Project current sealed samples through the concrete project ExperienceEstimator."""

    __slots__ = ("_estimator", "_snapshot_source")

    def __init__(
        self,
        snapshot_source: ExperienceTradeSampleSnapshotSource,
        configuration: ExperienceEstimationConfiguration | None = None,
    ) -> None:
        if not isinstance(snapshot_source, ExperienceTradeSampleSnapshotSource):
            raise TypeError("snapshot_source must provide Experience sample snapshots")
        self._snapshot_source = snapshot_source
        self._estimator = ExperienceEstimator(configuration)

    def contexts(self, query: DashboardExperienceContextQuery) -> DashboardExperienceContextPage:
        if not isinstance(query, DashboardExperienceContextQuery):
            raise TypeError("query must be a DashboardExperienceContextQuery")
        query.validate()
        snapshot = self._current_snapshot()
        contexts = {
            sample.fingerprint for sample in snapshot.samples if sample.exit_fill_at <= query.as_of
        }
        projected = tuple(
            sorted(
                (project_experience_fingerprint(item) for item in contexts),
                key=lambda item: item.sort_key,
            )
        )
        returned = projected[: query.limit]
        page = DashboardExperienceContextPage(
            query.as_of,
            query.limit,
            len(projected),
            len(returned),
            len(projected) > len(returned),
            returned,
        )
        page.validate()
        return deepcopy(page)

    def estimate(self, query: DashboardExperienceEstimateQuery) -> DashboardExperienceEstimate:
        if not isinstance(query, DashboardExperienceEstimateQuery):
            raise TypeError("query must be a DashboardExperienceEstimateQuery")
        query.validate()
        snapshot = self._current_snapshot()
        try:
            estimate = self._estimator.estimate(
                query.target_fingerprint,
                snapshot,
                query.as_of,
            )
            projected = _project_estimate(estimate)
            projected.validate()
        except Exception as error:
            raise DashboardExperienceProviderError(
                "Experience estimate authority could not be projected"
            ) from error
        return deepcopy(projected)

    def _current_snapshot(self) -> ExperienceTradeSampleSnapshot:
        try:
            snapshot = self._snapshot_source.snapshot()
            if not isinstance(snapshot, ExperienceTradeSampleSnapshot):
                raise TypeError("source returned an invalid Experience sample snapshot")
            if snapshot.sample_snapshot_schema_version != _SAMPLE_SNAPSHOT_SCHEMA_VERSION:
                raise ValueError("Experience sample snapshot schema version mismatch")
            detached = deepcopy(snapshot)
            validated = ExperienceTradeSampleSnapshot(detached.samples)
        except Exception as error:
            raise DashboardExperienceProviderError(
                "Experience sample snapshot is unavailable or inconsistent"
            ) from error
        return validated


def _project_estimate(estimate: ExperienceEstimate) -> DashboardExperienceEstimate:
    estimate.validate()
    return DashboardExperienceEstimate(
        target_fingerprint=project_experience_fingerprint(estimate.target_fingerprint),
        as_of=estimate.as_of,
        availability=estimate.availability.value,
        selected_key=(
            _project_domain_key(estimate.selected_key)
            if estimate.selected_key is not None
            else None
        ),
        selected_level=(
            estimate.selected_level.value if estimate.selected_level is not None else None
        ),
        confidence=estimate.confidence.value,
        examined_hierarchy=tuple(
            DashboardExperienceHierarchyEvidence(
                _project_domain_key(item.key),
                item.raw_eligible_sample_count,
                item.effective_sample_size,
                item.sufficient,
                item.hierarchy_evidence_schema_version,
            )
            for item in estimate.examined_hierarchy
        ),
        eligible_selected_raw_sample_count=estimate.eligible_selected_raw_sample_count,
        selected_effective_sample_size=estimate.selected_effective_sample_size,
        excluded_future_sample_count=estimate.excluded_future_sample_count,
        prior_source=estimate.prior_source.value if estimate.prior_source is not None else None,
        prior_source_level=(
            estimate.prior_source_level.value if estimate.prior_source_level is not None else None
        ),
        prior_source_key=(
            _project_domain_key(estimate.prior_source_key)
            if estimate.prior_source_key is not None
            else None
        ),
        prior_external_effective_sample_size=estimate.prior_external_effective_sample_size,
        prior_alpha=estimate.prior_alpha,
        prior_beta=estimate.prior_beta,
        posterior_alpha=estimate.posterior_alpha,
        posterior_beta=estimate.posterior_beta,
        posterior_mean_win_probability=estimate.posterior_mean_win_probability,
        recency_weighted_win_rate=estimate.recency_weighted_win_rate,
        recency_weighted_expectancy_r=estimate.recency_weighted_expectancy_r,
        quote_currency=estimate.quote_currency,
        recency_weighted_expectancy_net_quote=estimate.recency_weighted_expectancy_net_quote,
        estimation_policy_version=estimate.estimation_policy_version,
        recency_policy_version=estimate.recency_policy_version,
        estimate_schema_version=estimate.estimate_schema_version,
    )
