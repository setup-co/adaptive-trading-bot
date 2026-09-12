"""Lossless API transport for read-only Experience observations."""

from datetime import datetime
from typing import Literal, overload

from pydantic import BaseModel, ConfigDict

from adaptive_trading_bot.dashboard.experience_models import (
    DashboardExperienceContextKey,
    DashboardExperienceContextPage,
    DashboardExperienceEstimate,
    DashboardExperienceFingerprint,
    DashboardExperienceHierarchyEvidence,
)


class DashboardExperienceApiModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class DashboardExperienceFingerprintResponse(DashboardExperienceApiModel):
    symbol: str
    strategy_id: str
    strategy_version: str
    entry_timeframe: str
    regime_label: str
    direction: str
    trend_strength_bucket: str
    volatility_state: str
    volume_state: str
    higher_timeframe_bias: str
    multi_timeframe_alignment: str
    fingerprint_schema_version: str


class DashboardExperienceContextKeyResponse(DashboardExperienceApiModel):
    level: str
    strategy_id: str
    strategy_version: str
    symbol: str | None
    entry_timeframe: str | None
    regime_label: str | None
    direction: str | None
    trend_strength_bucket: str | None
    volatility_state: str | None
    volume_state: str | None
    higher_timeframe_bias: str | None
    multi_timeframe_alignment: str | None
    key_schema_version: str


class DashboardExperienceHierarchyEvidenceResponse(DashboardExperienceApiModel):
    key: DashboardExperienceContextKeyResponse
    raw_eligible_sample_count: int
    effective_sample_size: str
    sufficient: bool
    hierarchy_evidence_schema_version: str


class DashboardExperienceContextPageResponse(DashboardExperienceApiModel):
    as_of: datetime
    limit: int
    total_matched: int
    returned_count: int
    truncated: bool
    contexts: tuple[DashboardExperienceFingerprintResponse, ...]
    history_scope: str
    history_limitation: str


class DashboardExperienceEstimateResponse(DashboardExperienceApiModel):
    target_fingerprint: DashboardExperienceFingerprintResponse
    as_of: datetime
    availability: str
    selected_key: DashboardExperienceContextKeyResponse | None
    selected_level: str | None
    confidence: str
    examined_hierarchy: tuple[DashboardExperienceHierarchyEvidenceResponse, ...]
    eligible_selected_raw_sample_count: int
    selected_effective_sample_size: str
    excluded_future_sample_count: int
    prior_source: str | None
    prior_source_level: str | None
    prior_source_key: DashboardExperienceContextKeyResponse | None
    prior_external_effective_sample_size: str | None
    prior_alpha: str | None
    prior_beta: str | None
    posterior_alpha: str | None
    posterior_beta: str | None
    posterior_mean_win_probability: str | None
    recency_weighted_win_rate: str | None
    recency_weighted_expectancy_r: str | None
    quote_currency: str | None
    recency_weighted_expectancy_net_quote: str | None
    estimation_policy_version: str
    recency_policy_version: str
    estimate_schema_version: str
    history_scope: str
    history_limitation: str


class DashboardExperienceInvalidQueryResponse(DashboardExperienceApiModel):
    status: Literal["invalid_request"] = "invalid_request"
    code: Literal["DASHBOARD_EXPERIENCE_INVALID_QUERY"] = "DASHBOARD_EXPERIENCE_INVALID_QUERY"
    message: Literal["dashboard Experience query is invalid"] = (
        "dashboard Experience query is invalid"
    )


def experience_context_page_response(
    page: DashboardExperienceContextPage,
) -> DashboardExperienceContextPageResponse:
    page.validate()
    return DashboardExperienceContextPageResponse(
        as_of=page.as_of,
        limit=page.limit,
        total_matched=page.total_matched,
        returned_count=page.returned_count,
        truncated=page.truncated,
        contexts=tuple(_fingerprint_response(item) for item in page.contexts),
        history_scope=page.history_scope,
        history_limitation=page.history_limitation,
    )


def experience_estimate_response(
    estimate: DashboardExperienceEstimate,
) -> DashboardExperienceEstimateResponse:
    estimate.validate()
    return DashboardExperienceEstimateResponse(
        target_fingerprint=_fingerprint_response(estimate.target_fingerprint),
        as_of=estimate.as_of,
        availability=estimate.availability,
        selected_key=_key_response(estimate.selected_key),
        selected_level=estimate.selected_level,
        confidence=estimate.confidence,
        examined_hierarchy=tuple(_evidence_response(item) for item in estimate.examined_hierarchy),
        eligible_selected_raw_sample_count=estimate.eligible_selected_raw_sample_count,
        selected_effective_sample_size=str(estimate.selected_effective_sample_size),
        excluded_future_sample_count=estimate.excluded_future_sample_count,
        prior_source=estimate.prior_source,
        prior_source_level=estimate.prior_source_level,
        prior_source_key=_key_response(estimate.prior_source_key),
        prior_external_effective_sample_size=_optional_decimal(
            estimate.prior_external_effective_sample_size
        ),
        prior_alpha=_optional_decimal(estimate.prior_alpha),
        prior_beta=_optional_decimal(estimate.prior_beta),
        posterior_alpha=_optional_decimal(estimate.posterior_alpha),
        posterior_beta=_optional_decimal(estimate.posterior_beta),
        posterior_mean_win_probability=_optional_decimal(estimate.posterior_mean_win_probability),
        recency_weighted_win_rate=_optional_decimal(estimate.recency_weighted_win_rate),
        recency_weighted_expectancy_r=_optional_decimal(estimate.recency_weighted_expectancy_r),
        quote_currency=estimate.quote_currency,
        recency_weighted_expectancy_net_quote=_optional_decimal(
            estimate.recency_weighted_expectancy_net_quote
        ),
        estimation_policy_version=estimate.estimation_policy_version,
        recency_policy_version=estimate.recency_policy_version,
        estimate_schema_version=estimate.estimate_schema_version,
        history_scope=estimate.history_scope,
        history_limitation=estimate.history_limitation,
    )


def _fingerprint_response(
    value: DashboardExperienceFingerprint,
) -> DashboardExperienceFingerprintResponse:
    return DashboardExperienceFingerprintResponse(
        **{
            name: getattr(value, name)
            for name in DashboardExperienceFingerprintResponse.model_fields
        }
    )


@overload
def _key_response(
    value: DashboardExperienceContextKey,
) -> DashboardExperienceContextKeyResponse: ...


@overload
def _key_response(value: None) -> None: ...


def _key_response(
    value: DashboardExperienceContextKey | None,
) -> DashboardExperienceContextKeyResponse | None:
    if value is None:
        return None
    return DashboardExperienceContextKeyResponse(
        **{
            name: getattr(value, name)
            for name in DashboardExperienceContextKeyResponse.model_fields
        }
    )


def _evidence_response(
    value: DashboardExperienceHierarchyEvidence,
) -> DashboardExperienceHierarchyEvidenceResponse:
    return DashboardExperienceHierarchyEvidenceResponse(
        key=_key_response(value.key),
        raw_eligible_sample_count=value.raw_eligible_sample_count,
        effective_sample_size=str(value.effective_sample_size),
        sufficient=value.sufficient,
        hierarchy_evidence_schema_version=value.hierarchy_evidence_schema_version,
    )


def _optional_decimal(value: object) -> str | None:
    return None if value is None else str(value)
