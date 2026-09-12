import ast
import inspect
import runpy
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

import adaptive_trading_bot.dashboard.experience_provider as provider_module
from adaptive_trading_bot.api import create_app
from adaptive_trading_bot.config import Settings
from adaptive_trading_bot.dashboard import (
    DashboardExperienceContextQuery,
    DashboardExperienceEstimateQuery,
    DashboardExperienceProviderError,
    SnapshotDashboardExperienceProvider,
    experience_estimate_response,
)
from adaptive_trading_bot.experience import (
    ExperienceContextFingerprint,
    ExperienceEstimate,
    ExperienceEstimationConfiguration,
    ExperienceEstimator,
    ExperienceFallbackLevel,
    ExperienceTradeSample,
    ExperienceTradeSampleSnapshot,
)
from adaptive_trading_bot.regime import Direction, RegimeLabel

_EXPERIENCE_HELPERS = runpy.run_path(
    str(Path(__file__).parents[1] / "experience" / "test_estimation.py")
)
_project_one = cast(
    Callable[..., tuple[object, object, ExperienceTradeSample]],
    _EXPERIENCE_HELPERS["_project_one"],
)
_sample = cast(Callable[..., ExperienceTradeSample], _EXPERIENCE_HELPERS["_sample"])
AS_OF = cast(datetime, _EXPERIENCE_HELPERS["AS_OF"])


class _SnapshotSource:
    def __init__(self, snapshot: ExperienceTradeSampleSnapshot) -> None:
        self.current = snapshot
        self.calls = 0

    def snapshot(self) -> ExperienceTradeSampleSnapshot:
        self.calls += 1
        return self.current


def _base() -> ExperienceTradeSample:
    return _project_one()[2]


def _snapshot(*samples: ExperienceTradeSample) -> ExperienceTradeSampleSnapshot:
    return ExperienceTradeSampleSnapshot(tuple(sorted(samples, key=lambda item: item.sort_key)))


def _configuration() -> ExperienceEstimationConfiguration:
    return ExperienceEstimationConfiguration(
        minimum_raw_sample_count=1,
        minimum_effective_sample_size=Decimal("0.01"),
        medium_confidence_effective_sample_size=Decimal(2),
        high_confidence_effective_sample_size=Decimal(3),
    )


def _estimate_query(
    fingerprint: ExperienceContextFingerprint,
    *,
    as_of: datetime = AS_OF,
) -> DashboardExperienceEstimateQuery:
    return DashboardExperienceEstimateQuery(fingerprint, as_of)


def _params(
    fingerprint: ExperienceContextFingerprint, *, as_of: datetime = AS_OF
) -> dict[str, str]:
    return {
        "as_of": as_of.isoformat(),
        "symbol": str(fingerprint.symbol),
        "strategy_id": fingerprint.strategy_id,
        "strategy_version": fingerprint.strategy_version,
        "entry_timeframe": fingerprint.entry_timeframe.value,
        "regime_label": fingerprint.regime_label.value,
        "direction": fingerprint.direction.value,
        "trend_strength_bucket": fingerprint.trend_strength_bucket.value,
        "volatility_state": fingerprint.volatility_state.value,
        "volume_state": fingerprint.volume_state.value,
        "higher_timeframe_bias": fingerprint.higher_timeframe_bias.value,
        "multi_timeframe_alignment": fingerprint.multi_timeframe_alignment.value,
    }


def test_contexts_are_distinct_causal_authoritative_and_canonically_bounded() -> None:
    base = _base()
    first = _sample(base, 1, source="venue-a")
    duplicate_context = _sample(base, 2, source="venue-b")
    other = _sample(base, 3, regime_label=RegimeLabel.NEUTRAL)
    future = _sample(base, 4, direction=Direction.DOWN, exit_at=AS_OF + timedelta(seconds=1))
    source = _SnapshotSource(_snapshot(first, duplicate_context, other, future))
    provider = SnapshotDashboardExperienceProvider(source)

    page = provider.contexts(DashboardExperienceContextQuery(AS_OF, limit=1))

    assert page.total_matched == 2
    assert page.returned_count == 1
    assert page.truncated is True
    assert page.contexts == tuple(sorted(page.contexts, key=lambda item: item.sort_key))
    assert page.contexts[0].direction != Direction.DOWN.value
    assert page.history_scope == "PROCESS_LOCAL"
    assert "not durable" in page.history_limitation


def test_future_only_context_is_absent_at_earlier_as_of() -> None:
    base = _base()
    future = _sample(base, 1, exit_at=AS_OF + timedelta(microseconds=1))
    provider = SnapshotDashboardExperienceProvider(_SnapshotSource(_snapshot(future)))

    page = provider.contexts(DashboardExperienceContextQuery(AS_OF))

    assert page.contexts == ()
    assert page.total_matched == 0


@pytest.mark.parametrize(
    "as_of",
    (datetime(2026, 9, 12), datetime(2026, 9, 12, tzinfo=timezone(timedelta(hours=1)))),
)
def test_experience_queries_require_strict_utc(as_of: datetime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        DashboardExperienceContextQuery(as_of)
    with pytest.raises(ValueError, match="UTC"):
        _estimate_query(_base().fingerprint, as_of=as_of)


@pytest.mark.parametrize("limit", (0, 201, True))
def test_context_limit_is_bounded(limit: int) -> None:
    with pytest.raises((TypeError, ValueError)):
        DashboardExperienceContextQuery(AS_OF, limit)


def test_tampered_sample_fails_before_context_exposure() -> None:
    sample = _sample(_base(), 1)
    snapshot = _snapshot(sample)
    object.__setattr__(sample, "net_realized_pnl", Decimal(999))
    provider = SnapshotDashboardExperienceProvider(_SnapshotSource(snapshot))

    with pytest.raises(DashboardExperienceProviderError):
        provider.contexts(DashboardExperienceContextQuery(AS_OF))


def test_provider_reads_current_snapshot_and_invokes_concrete_estimator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _base()
    source = _SnapshotSource(_snapshot())
    calls: list[tuple[ExperienceContextFingerprint, datetime]] = []

    class RecordingEstimator(ExperienceEstimator):
        def estimate(
            self,
            target: ExperienceContextFingerprint,
            snapshot: ExperienceTradeSampleSnapshot,
            as_of: datetime,
        ) -> ExperienceEstimate:
            calls.append((target, as_of))
            return super().estimate(target, snapshot, as_of)

    monkeypatch.setattr(provider_module, "ExperienceEstimator", RecordingEstimator)
    provider = SnapshotDashboardExperienceProvider(source, _configuration())
    source.current = _snapshot(_sample(base, 1))

    result = provider.estimate(_estimate_query(base.fingerprint))

    assert calls == [(base.fingerprint, AS_OF)]
    assert source.calls == 1
    assert result.target_fingerprint.symbol == str(base.fingerprint.symbol)
    assert result.availability == "AVAILABLE"
    assert result.selected_level == ExperienceFallbackLevel.FULL_CONTEXT.value


def test_provider_constructor_cannot_accept_prebuilt_estimate_authority() -> None:
    parameters = inspect.signature(SnapshotDashboardExperienceProvider).parameters
    app_parameters = inspect.signature(create_app).parameters

    assert tuple(parameters) == ("snapshot_source", "configuration")
    assert "estimate" not in parameters
    assert "experience_provider" not in app_parameters
    assert "experience_snapshot_source" in app_parameters
    assert "experience_configuration" in app_parameters


def test_no_history_and_insufficient_states_preserve_exact_unavailability() -> None:
    base = _base()
    empty_provider = SnapshotDashboardExperienceProvider(_SnapshotSource(_snapshot()))
    no_history = empty_provider.estimate(_estimate_query(base.fingerprint))
    insufficient_provider = SnapshotDashboardExperienceProvider(
        _SnapshotSource(_snapshot(_sample(base, 1)))
    )
    insufficient = insufficient_provider.estimate(_estimate_query(base.fingerprint))

    assert no_history.availability == "NO_HISTORY"
    assert no_history.posterior_mean_win_probability is None
    assert no_history.recency_weighted_expectancy_r is None
    assert insufficient.availability == "INSUFFICIENT_EVIDENCE"
    assert insufficient.posterior_mean_win_probability is not None


async def test_api_constructs_exact_target_ignores_injected_derived_fields_and_is_lossless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _base()
    precise = _sample(
        base,
        1,
        net=Decimal("12345678901234567890.1234567890123"),
        realized_r=Decimal("1.23456789012345678901234567890123"),
    )
    captured: list[DashboardExperienceEstimateQuery] = []

    class RecordingEstimator(ExperienceEstimator):
        def estimate(
            self,
            target: ExperienceContextFingerprint,
            snapshot: ExperienceTradeSampleSnapshot,
            as_of: datetime,
        ) -> ExperienceEstimate:
            captured.append(DashboardExperienceEstimateQuery(target, as_of))
            return super().estimate(target, snapshot, as_of)

    monkeypatch.setattr(provider_module, "ExperienceEstimator", RecordingEstimator)
    app = create_app(
        Settings(_env_file=None),  # type: ignore[call-arg]
        experience_snapshot_source=_SnapshotSource(_snapshot(precise)),
        experience_configuration=_configuration(),
    )
    params = _params(base.fingerprint) | {
        "confidence": "HIGH",
        "prior_alpha": "999",
        "selected_level": "STRATEGY",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/experience/estimate", params=params)

    assert response.status_code == 200
    assert captured[0].target_fingerprint == base.fingerprint
    body = response.json()
    assert body["confidence"] == "LOW"
    assert body["selected_level"] == "FULL_CONTEXT"
    assert body["prior_alpha"] == "1"
    assert body["recency_weighted_expectancy_net_quote"] == str(precise.net_realized_pnl)
    assert body["recency_weighted_expectancy_r"] == str(precise.realized_r_net)
    assert isinstance(body["posterior_mean_win_probability"], str)
    assert body["history_scope"] == "PROCESS_LOCAL"


async def test_context_and_no_history_api_transport_preserve_causality_and_none() -> None:
    base = _base()
    future = _sample(base, 1, exit_at=AS_OF + timedelta(seconds=1))
    app = create_app(
        Settings(_env_file=None),  # type: ignore[call-arg]
        experience_snapshot_source=_SnapshotSource(_snapshot(future)),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        contexts = await client.get(
            "/api/v1/dashboard/experience/contexts",
            params={"as_of": AS_OF.isoformat(), "limit": "200"},
        )
        estimate = await client.get(
            "/api/v1/dashboard/experience/estimate",
            params=_params(base.fingerprint),
        )

    assert contexts.status_code == 200
    assert contexts.json()["contexts"] == []
    assert estimate.status_code == 200
    body = estimate.json()
    assert body["availability"] == "NO_HISTORY"
    assert body["excluded_future_sample_count"] == 1
    assert body["posterior_alpha"] is None
    assert body["recency_weighted_win_rate"] is None


async def test_invalid_and_missing_experience_snapshot_source_are_structured() -> None:
    base = _base()
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        invalid = await client.get(
            "/api/v1/dashboard/experience/contexts",
            params={"as_of": "not-a-time"},
        )
        non_utc = await client.get(
            "/api/v1/dashboard/experience/estimate",
            params=_params(
                base.fingerprint,
                as_of=AS_OF.astimezone(timezone(timedelta(hours=1))),
            ),
        )
        missing = await client.get(
            "/api/v1/dashboard/experience/estimate",
            params=_params(base.fingerprint),
        )

    assert invalid.status_code == 400
    assert invalid.json()["code"] == "DASHBOARD_EXPERIENCE_INVALID_QUERY"
    assert non_utc.status_code == 400
    assert missing.status_code == 503
    assert missing.json()["code"] == "DASHBOARD_EXPERIENCE_UNAVAILABLE"


async def test_api_ignores_fabricated_estimate_method_on_snapshot_source() -> None:
    base = _base()
    snapshot = _snapshot(_sample(base, 1))
    authoritative = SnapshotDashboardExperienceProvider(
        _SnapshotSource(snapshot), _configuration()
    ).estimate(_estimate_query(base.fingerprint))
    fabricated = deepcopy(authoritative)
    object.__setattr__(
        fabricated,
        "posterior_mean_win_probability",
        Decimal("0.999999999999999999999999999999"),
    )
    fabricated.validate()

    class FaultyEstimateProducingSource(_SnapshotSource):
        def __init__(self) -> None:
            super().__init__(snapshot)
            self.estimate_calls = 0

        def estimate(self, query: DashboardExperienceEstimateQuery) -> object:
            self.estimate_calls += 1
            return fabricated

    source = FaultyEstimateProducingSource()
    with pytest.raises(TypeError, match="experience_provider"):
        create_app(
            Settings(_env_file=None),  # type: ignore[call-arg]
            experience_provider=source,
        )
    app = create_app(
        Settings(_env_file=None),  # type: ignore[call-arg]
        experience_snapshot_source=source,
        experience_configuration=_configuration(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/dashboard/experience/estimate",
            params=_params(base.fingerprint),
        )

    assert response.status_code == 200
    assert source.estimate_calls == 0
    assert response.json()["posterior_mean_win_probability"] == str(
        authoritative.posterior_mean_win_probability
    )
    assert response.json()["posterior_mean_win_probability"] != str(
        fabricated.posterior_mean_win_probability
    )


async def test_snapshot_failure_is_sanitized_and_malformed_snapshot_returns_503() -> None:
    base = _base()

    class FailingSource:
        def snapshot(self) -> ExperienceTradeSampleSnapshot:
            raise RuntimeError("secret-path-and-token")

    class MalformedSource:
        def snapshot(self) -> object:
            return object()

    failing_app = create_app(
        Settings(_env_file=None),  # type: ignore[call-arg]
        experience_snapshot_source=FailingSource(),
    )
    malformed_app = create_app(
        Settings(_env_file=None),  # type: ignore[call-arg]
        experience_snapshot_source=MalformedSource(),  # type: ignore[arg-type]
    )
    async with AsyncClient(
        transport=ASGITransport(app=failing_app), base_url="http://test"
    ) as client:
        failing = await client.get(
            "/api/v1/dashboard/experience/contexts",
            params={"as_of": AS_OF.isoformat()},
        )
    async with AsyncClient(
        transport=ASGITransport(app=malformed_app), base_url="http://test"
    ) as client:
        bad_model = await client.get(
            "/api/v1/dashboard/experience/estimate",
            params=_params(base.fingerprint),
        )

    expected = {
        "status": "unavailable",
        "code": "DASHBOARD_EXPERIENCE_PROVIDER_FAILURE",
        "message": "dashboard Experience observation is temporarily unavailable",
    }
    assert failing.status_code == 503
    assert failing.json() == expected
    assert "secret" not in failing.text
    assert bad_model.status_code == 503
    assert bad_model.json() == expected


def test_transport_and_routes_are_get_only_and_provider_has_no_mutating_surface() -> None:
    base = _base()
    provider = SnapshotDashboardExperienceProvider(_SnapshotSource(_snapshot()))
    app = create_app(
        Settings(_env_file=None),  # type: ignore[call-arg]
        experience_snapshot_source=_SnapshotSource(_snapshot()),
    )
    routes = {
        route.path: route.methods
        for route in app.routes
        if isinstance(route, APIRoute) and "/dashboard/experience/" in route.path
    }
    public = {
        name
        for name, value in inspect.getmembers(SnapshotDashboardExperienceProvider)
        if not name.startswith("_") and inspect.isfunction(value)
    }
    projected = provider.estimate(_estimate_query(base.fingerprint))
    response = experience_estimate_response(projected).model_dump()

    assert routes == {
        "/api/v1/dashboard/experience/contexts": {"GET"},
        "/api/v1/dashboard/experience/estimate": {"GET"},
    }
    assert public == {"contexts", "estimate"}
    assert response["posterior_alpha"] is None


def test_dashboard_experience_provider_has_no_alternate_formula_or_authority_dependency() -> None:
    tree = ast.parse(Path(provider_module.__file__).read_text(encoding="utf-8"))
    imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden = ("accounting", "exchange", "journal", "live", "paper", "risk", "strategies")

    assert not any(token in module for module in imported for token in forbidden)
    source = Path(provider_module.__file__).read_text(encoding="utf-8")
    assert "ExperienceEstimator(configuration)" in source
    assert "prior_alpha +" not in source
    assert "def _recency" not in source
