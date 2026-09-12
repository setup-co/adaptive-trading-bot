import ast
import runpy
from collections.abc import Callable
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Context, Decimal, getcontext, setcontext
from pathlib import Path
from typing import cast

import pytest

import adaptive_trading_bot.experience.estimation as estimation_module
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.experience import (
    ExperienceContextFingerprint,
    ExperienceEstimate,
    ExperienceEstimateAvailability,
    ExperienceEstimationConfiguration,
    ExperienceEstimationError,
    ExperienceEstimator,
    ExperienceEvidenceConfidence,
    ExperienceFallbackLevel,
    ExperiencePriorSource,
    ExperienceProjectionError,
    ExperienceTradeSample,
    ExperienceTradeSampleSnapshot,
    experience_context_hierarchy,
)
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.regime import (
    Direction,
    HigherTimeframeBias,
    MultiTimeframeAlignment,
    RegimeLabel,
)
from adaptive_trading_bot.storage import SourceId

_SAMPLE_HELPERS = runpy.run_path(str(Path(__file__).with_name("test_sample_projection.py")))
_project_one = cast(
    Callable[..., tuple[object, object, ExperienceTradeSample]],
    _SAMPLE_HELPERS["_project_one"],
)
AS_OF = datetime(2026, 9, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def base_sample() -> ExperienceTradeSample:
    return _project_one()[2]


def _configuration(
    *,
    minimum_raw: int = 1,
    minimum_effective: Decimal = Decimal("0.01"),
    medium: Decimal = Decimal(3),
    high: Decimal = Decimal(5),
    cap: Decimal = Decimal(20),
    horizon: timedelta = timedelta(days=100),
    floor: Decimal = Decimal("0.25"),
) -> ExperienceEstimationConfiguration:
    return ExperienceEstimationConfiguration(
        minimum_raw_sample_count=minimum_raw,
        minimum_effective_sample_size=minimum_effective,
        medium_confidence_effective_sample_size=medium,
        high_confidence_effective_sample_size=high,
        hierarchical_prior_strength_cap=cap,
        recency_horizon=horizon,
        minimum_recency_weight=floor,
    )


def _sample(
    base: ExperienceTradeSample,
    index: int,
    *,
    exit_at: datetime = AS_OF,
    outcome: str = "PROFIT",
    net: Decimal = Decimal(1),
    realized_r: Decimal = Decimal(1),
    source: str | None = None,
    symbol: TradingSymbol | None = None,
    strategy_version: str | None = None,
    direction: Direction | None = None,
    regime_label: RegimeLabel | None = None,
    higher_timeframe_bias: HigherTimeframeBias | None = None,
    alignment: MultiTimeframeAlignment | None = None,
) -> ExperienceTradeSample:
    current_symbol = symbol or base.fingerprint.symbol
    current_version = strategy_version or base.fingerprint.strategy_version
    fingerprint = replace(
        base.fingerprint,
        symbol=current_symbol,
        strategy_version=current_version,
        direction=direction or base.fingerprint.direction,
        regime_label=regime_label or base.fingerprint.regime_label,
        higher_timeframe_bias=higher_timeframe_bias or base.fingerprint.higher_timeframe_bias,
        multi_timeframe_alignment=alignment or base.fingerprint.multi_timeframe_alignment,
    )
    source_id = SourceId(source or f"estimate-{index}")
    entry_fill_at = exit_at - timedelta(microseconds=2)
    decision_at = entry_fill_at - timedelta(microseconds=index + 1)
    identity = DecisionIdentity(
        source_id,
        current_symbol,
        decision_at,
        base.decision_identity.strategy_id,
        current_version,
    )
    return replace(
        base,
        decision_identity=identity,
        source_id=source_id,
        fingerprint=fingerprint,
        hierarchical_keys=experience_context_hierarchy(fingerprint),
        entry_decision_at=decision_at,
        entry_fill_at=entry_fill_at,
        exit_fill_at=exit_at,
        gross_realized_pnl=net,
        total_trade_fees_quote=Decimal(0),
        net_realized_pnl=net,
        realized_r_net=realized_r,
        outcome_classification=outcome,
        duration=timedelta(microseconds=2),
        accounting_entry_fill_id=f"estimate-entry-{index}",
        accounting_exit_fill_id=f"estimate-exit-{index}",
    )


def _snapshot(*samples: ExperienceTradeSample) -> ExperienceTradeSampleSnapshot:
    return ExperienceTradeSampleSnapshot(tuple(sorted(samples, key=lambda sample: sample.sort_key)))


def _estimate(
    target: ExperienceContextFingerprint,
    samples: ExperienceTradeSampleSnapshot,
    configuration: ExperienceEstimationConfiguration | None = None,
    as_of: datetime = AS_OF,
) -> ExperienceEstimate:
    return ExperienceEstimator(configuration).estimate(target, samples, as_of)


def _different_direction(base: ExperienceTradeSample, index: int) -> ExperienceTradeSample:
    direction = Direction.DOWN if base.fingerprint.direction is Direction.UP else Direction.UP
    return _sample(base, index, direction=direction)


def _different_htf(base: ExperienceTradeSample, index: int) -> ExperienceTradeSample:
    bias = (
        HigherTimeframeBias.DOWN
        if base.fingerprint.higher_timeframe_bias is HigherTimeframeBias.UP
        else HigherTimeframeBias.UP
    )
    alignment = (
        MultiTimeframeAlignment.ALIGNED_DOWN
        if base.fingerprint.multi_timeframe_alignment is not MultiTimeframeAlignment.ALIGNED_DOWN
        else MultiTimeframeAlignment.ALIGNED_UP
    )
    return _sample(base, index, higher_timeframe_bias=bias, alignment=alignment)


def _different_regime(base: ExperienceTradeSample, index: int) -> ExperienceTradeSample:
    regime = (
        RegimeLabel.NEUTRAL
        if base.fingerprint.regime_label is not RegimeLabel.NEUTRAL
        else RegimeLabel.UPTREND
    )
    return _sample(base, index, regime_label=regime)


def test_configuration_defaults_and_canonical_hierarchy_are_versioned(
    base_sample: ExperienceTradeSample,
) -> None:
    configuration = ExperienceEstimationConfiguration()
    hierarchy = experience_context_hierarchy(base_sample.fingerprint)

    assert configuration.minimum_raw_sample_count == 20
    assert configuration.minimum_effective_sample_size == Decimal(12)
    assert configuration.medium_confidence_effective_sample_size == Decimal(30)
    assert configuration.high_confidence_effective_sample_size == Decimal(60)
    assert configuration.hierarchical_prior_strength_cap == Decimal(20)
    assert configuration.recency_horizon == timedelta(days=180)
    assert configuration.minimum_recency_weight == Decimal("0.25")
    assert configuration.neutral_prior_alpha == configuration.neutral_prior_beta == 1
    assert configuration.configuration_schema_version == "1.0.0"
    assert tuple(key.level for key in hierarchy) == tuple(ExperienceFallbackLevel)
    assert hierarchy == base_sample.hierarchical_keys


def test_no_samples_returns_no_history_without_fabricated_values(
    base_sample: ExperienceTradeSample,
) -> None:
    result = _estimate(base_sample.fingerprint, _snapshot())

    assert result.availability is ExperienceEstimateAvailability.NO_HISTORY
    assert result.confidence is ExperienceEvidenceConfidence.NO_HISTORY
    assert result.selected_key is None
    assert result.posterior_mean_win_probability is None
    assert result.recency_weighted_win_rate is None
    assert result.recency_weighted_expectancy_r is None
    assert result.recency_weighted_expectancy_net_quote is None
    assert tuple(item.raw_eligible_sample_count for item in result.examined_hierarchy) == (0,) * 5


def test_only_future_samples_are_excluded_and_return_no_history(
    base_sample: ExperienceTradeSample,
) -> None:
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(
            _sample(base_sample, 1, exit_at=AS_OF + timedelta(microseconds=1)),
            _sample(base_sample, 2, exit_at=AS_OF + timedelta(days=1)),
        ),
    )

    assert result.availability is ExperienceEstimateAvailability.NO_HISTORY
    assert result.excluded_future_sample_count == 2


@pytest.mark.parametrize(
    "as_of",
    (datetime(2026, 9, 12), datetime(2026, 9, 12, tzinfo=timezone(timedelta(hours=1)))),
)
def test_as_of_must_be_strict_utc(base_sample: ExperienceTradeSample, as_of: datetime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        _estimate(base_sample.fingerprint, _snapshot(), as_of=as_of)


@pytest.mark.parametrize(
    ("age", "expected_weight"),
    (
        (timedelta(), Decimal(1)),
        (timedelta(days=50), Decimal("0.625")),
        (timedelta(days=100), Decimal("0.25")),
        (timedelta(days=1000), Decimal("0.25")),
    ),
)
def test_bounded_linear_recency_weights_are_exact(
    base_sample: ExperienceTradeSample, age: timedelta, expected_weight: Decimal
) -> None:
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(_sample(base_sample, 1, exit_at=AS_OF - age)),
        _configuration(),
    )

    assert result.selected_effective_sample_size == expected_weight


def test_ambient_decimal_context_does_not_change_estimate(
    base_sample: ExperienceTradeSample,
) -> None:
    snapshot = _snapshot(
        _sample(base_sample, 1, exit_at=AS_OF - timedelta(days=17), realized_r=Decimal("1.7")),
        _sample(
            base_sample,
            2,
            exit_at=AS_OF - timedelta(days=73),
            outcome="LOSS",
            net=Decimal(-2),
            realized_r=Decimal("-0.4"),
        ),
    )
    original = getcontext().copy()
    try:
        setcontext(Context(prec=6, rounding=ROUND_DOWN))
        low = _estimate(base_sample.fingerprint, snapshot, _configuration())
        setcontext(Context(prec=60))
        high = _estimate(base_sample.fingerprint, snapshot, _configuration())
    finally:
        setcontext(original)

    assert low == high


def test_most_specific_sufficient_level_is_selected(
    base_sample: ExperienceTradeSample,
) -> None:
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(_sample(base_sample, 1), _sample(base_sample, 2)),
        _configuration(minimum_raw=2, minimum_effective=Decimal(2)),
    )

    assert result.selected_level is ExperienceFallbackLevel.FULL_CONTEXT
    assert all(item.sufficient for item in result.examined_hierarchy)


def test_insufficient_full_context_falls_back_to_regime_htf(
    base_sample: ExperienceTradeSample,
) -> None:
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(_sample(base_sample, 1), _different_direction(base_sample, 2)),
        _configuration(minimum_raw=2, minimum_effective=Decimal(2)),
    )

    assert result.selected_level is ExperienceFallbackLevel.REGIME_HTF
    assert tuple(item.raw_eligible_sample_count for item in result.examined_hierarchy) == (
        1,
        2,
        2,
        2,
        2,
    )


def test_effective_sample_minimum_independently_controls_fallback(
    base_sample: ExperienceTradeSample,
) -> None:
    old = AS_OF - timedelta(days=100)
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(
            _sample(base_sample, 1, exit_at=old),
            _sample(base_sample, 2, exit_at=old),
            _different_direction(base_sample, 3),
            _different_direction(base_sample, 4),
        ),
        _configuration(minimum_raw=2, minimum_effective=Decimal(2)),
    )

    assert result.examined_hierarchy[0].raw_eligible_sample_count == 2
    assert result.examined_hierarchy[0].effective_sample_size == Decimal("0.5")
    assert result.examined_hierarchy[0].sufficient is False
    assert result.selected_level is ExperienceFallbackLevel.REGIME_HTF


def test_multiple_insufficient_levels_fall_back_deterministically(
    base_sample: ExperienceTradeSample,
) -> None:
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(
            _sample(base_sample, 1),
            _different_htf(base_sample, 2),
            _different_regime(base_sample, 3),
        ),
        _configuration(minimum_raw=3, minimum_effective=Decimal(3)),
    )

    assert result.selected_level is ExperienceFallbackLevel.STRATEGY_SYMBOL


def test_no_sufficient_level_uses_broadest_history_as_insufficient(
    base_sample: ExperienceTradeSample,
) -> None:
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(_sample(base_sample, 1)),
        _configuration(minimum_raw=2, minimum_effective=Decimal(2)),
    )

    assert result.selected_level is ExperienceFallbackLevel.STRATEGY
    assert result.availability is ExperienceEstimateAvailability.INSUFFICIENT_EVIDENCE
    assert result.confidence is ExperienceEvidenceConfidence.INSUFFICIENT


@pytest.mark.parametrize(
    ("count", "confidence"),
    (
        (2, ExperienceEvidenceConfidence.LOW),
        (3, ExperienceEvidenceConfidence.MEDIUM),
        (5, ExperienceEvidenceConfidence.HIGH),
    ),
)
@pytest.mark.parametrize("profitable", (True, False))
def test_confidence_tiers_depend_on_effective_evidence_not_outcome_sign(
    base_sample: ExperienceTradeSample,
    count: int,
    confidence: ExperienceEvidenceConfidence,
    profitable: bool,
) -> None:
    outcome = "PROFIT" if profitable else "LOSS"
    net = Decimal(1) if profitable else Decimal(-1)
    realized_r = Decimal(1) if profitable else Decimal(-1)
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(
            *(
                _sample(base_sample, index, outcome=outcome, net=net, realized_r=realized_r)
                for index in range(1, count + 1)
            )
        ),
        _configuration(minimum_raw=1, minimum_effective=Decimal(1)),
    )

    assert result.confidence is confidence


def test_breakeven_is_non_success_for_weighted_rate_and_posterior(
    base_sample: ExperienceTradeSample,
) -> None:
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(
            _sample(base_sample, 1),
            _sample(base_sample, 2, outcome="BREAKEVEN", net=Decimal(0), realized_r=Decimal(0)),
        ),
        _configuration(),
    )

    assert result.recency_weighted_win_rate == Decimal("0.5")
    assert result.posterior_alpha == Decimal(2)
    assert result.posterior_beta == Decimal(2)
    assert result.posterior_mean_win_probability == Decimal("0.5")


def test_neutral_prior_when_no_external_broader_evidence_exists(
    base_sample: ExperienceTradeSample,
) -> None:
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(_sample(base_sample, 1), _sample(base_sample, 2)),
        _configuration(minimum_raw=2, minimum_effective=Decimal(2)),
    )

    assert result.prior_source is ExperiencePriorSource.NEUTRAL_PRIOR
    assert result.prior_source_key is None
    assert result.prior_external_effective_sample_size == 0
    assert result.prior_alpha == 1
    assert result.prior_beta == 1


def test_immediate_parent_external_evidence_excludes_selected_subgroup(
    base_sample: ExperienceTradeSample,
) -> None:
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(
            _sample(base_sample, 1),
            _sample(base_sample, 2),
            _different_direction(base_sample, 3),
        ),
        _configuration(minimum_raw=2, minimum_effective=Decimal(2)),
    )

    assert result.selected_level is ExperienceFallbackLevel.FULL_CONTEXT
    assert result.prior_source is ExperiencePriorSource.HIERARCHICAL
    assert result.prior_source_level is ExperienceFallbackLevel.REGIME_HTF
    assert result.prior_external_effective_sample_size == 1
    assert result.prior_alpha == 2
    assert result.prior_beta == 1
    assert result.posterior_alpha == 4
    assert result.posterior_beta == 1
    assert result.posterior_mean_win_probability == Decimal("0.8")


def test_next_broader_parent_used_when_immediate_parent_has_no_external_evidence(
    base_sample: ExperienceTradeSample,
) -> None:
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(
            _sample(base_sample, 1),
            _different_direction(base_sample, 2),
            _different_regime(base_sample, 3),
        ),
        _configuration(minimum_raw=2, minimum_effective=Decimal(2)),
    )

    assert result.selected_level is ExperienceFallbackLevel.REGIME_HTF
    assert result.prior_source_level is ExperienceFallbackLevel.STRATEGY_SYMBOL
    assert result.prior_external_effective_sample_size == 1


def test_hierarchical_prior_strength_is_capped(base_sample: ExperienceTradeSample) -> None:
    local = (_sample(base_sample, 1), _sample(base_sample, 2))
    external = tuple(_different_direction(base_sample, index) for index in range(3, 13))
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(*local, *external),
        _configuration(minimum_raw=2, minimum_effective=Decimal(2), cap=Decimal(3)),
    )

    assert result.prior_external_effective_sample_size == 10
    assert result.prior_alpha == 4
    assert result.prior_beta == 1


def test_posterior_arithmetic_is_exact_and_sparse_evidence_shrinks(
    base_sample: ExperienceTradeSample,
) -> None:
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(_sample(base_sample, 1)),
        _configuration(minimum_raw=1, minimum_effective=Decimal(1)),
    )

    assert result.recency_weighted_win_rate == 1
    assert result.posterior_alpha == 2
    assert result.posterior_beta == 1
    assert result.posterior_mean_win_probability == Decimal("0.6666666666666666666666666666666667")
    assert result.posterior_mean_win_probability < result.recency_weighted_win_rate


def test_large_local_sample_has_less_neutral_prior_influence(
    base_sample: ExperienceTradeSample,
) -> None:
    small = _estimate(
        base_sample.fingerprint,
        _snapshot(_sample(base_sample, 1)),
        _configuration(),
    )
    large = _estimate(
        base_sample.fingerprint,
        _snapshot(*(_sample(base_sample, index) for index in range(1, 21))),
        _configuration(),
    )

    assert small.posterior_mean_win_probability is not None
    assert large.posterior_mean_win_probability is not None
    assert Decimal(1) - large.posterior_mean_win_probability < (
        Decimal(1) - small.posterior_mean_win_probability
    )


def test_weighted_expectancies_use_authoritative_sample_values(
    base_sample: ExperienceTradeSample,
) -> None:
    result = _estimate(
        base_sample.fingerprint,
        _snapshot(
            _sample(base_sample, 1, realized_r=Decimal(3), net=Decimal(7)),
            _sample(
                base_sample,
                2,
                exit_at=AS_OF - timedelta(days=100),
                outcome="LOSS",
                realized_r=Decimal(-1),
                net=Decimal(-1),
            ),
        ),
        _configuration(),
    )

    assert result.selected_effective_sample_size == Decimal("1.25")
    assert result.recency_weighted_expectancy_r == Decimal("2.2")
    assert result.recency_weighted_expectancy_net_quote == Decimal("5.4")


def test_source_does_not_split_keys_but_strategy_version_does(
    base_sample: ExperienceTradeSample,
) -> None:
    source_result = _estimate(
        base_sample.fingerprint,
        _snapshot(
            _sample(base_sample, 1, source="venue-a"),
            _sample(base_sample, 2, source="venue-b"),
        ),
        _configuration(minimum_raw=2, minimum_effective=Decimal(2)),
    )
    version_result = _estimate(
        base_sample.fingerprint,
        _snapshot(_sample(base_sample, 3, strategy_version="different")),
    )

    assert source_result.selected_level is ExperienceFallbackLevel.FULL_CONTEXT
    assert source_result.eligible_selected_raw_sample_count == 2
    assert version_result.availability is ExperienceEstimateAvailability.NO_HISTORY


def test_mixed_quote_selected_population_fails_closed(
    base_sample: ExperienceTradeSample,
) -> None:
    with pytest.raises(ExperienceEstimationError, match="incompatible descriptive authority"):
        _estimate(
            base_sample.fingerprint,
            _snapshot(
                _sample(base_sample, 1, symbol=TradingSymbol("BTC", "USDT")),
                _sample(base_sample, 2, symbol=TradingSymbol("ETH", "USD")),
            ),
            _configuration(minimum_raw=2, minimum_effective=Decimal(2)),
        )


def test_tampered_sample_seal_fails_before_estimation(
    base_sample: ExperienceTradeSample,
) -> None:
    sample = _sample(base_sample, 1)
    snapshot = _snapshot(sample)
    object.__setattr__(sample, "realized_r_net", Decimal(999))

    with pytest.raises(ExperienceProjectionError, match="sample integrity seal mismatch"):
        _estimate(base_sample.fingerprint, snapshot)


def test_malformed_canonical_hierarchy_fails_closed(
    base_sample: ExperienceTradeSample,
) -> None:
    sample = _sample(base_sample, 1)
    snapshot = _snapshot(sample)
    object.__setattr__(sample, "hierarchical_keys", sample.hierarchical_keys[:-1])

    with pytest.raises(ExperienceProjectionError, match="sample integrity seal mismatch"):
        _estimate(base_sample.fingerprint, snapshot)


def test_input_order_invariance_and_output_detachment(
    base_sample: ExperienceTradeSample,
) -> None:
    samples = tuple(_sample(base_sample, index) for index in range(1, 4))
    canonical = _snapshot(*samples)
    reordered = deepcopy(canonical)
    object.__setattr__(reordered, "samples", tuple(reversed(reordered.samples)))

    first = _estimate(base_sample.fingerprint, canonical, _configuration())
    second = _estimate(base_sample.fingerprint, reordered, _configuration())
    before = deepcopy(first)
    object.__setattr__(samples[0], "net_realized_pnl", Decimal(999))

    assert first == second
    assert first == before
    with pytest.raises(FrozenInstanceError):
        first.confidence = ExperienceEvidenceConfidence.HIGH  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        first.examined_hierarchy[0].sufficient = False  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes",
    (
        {"minimum_raw_sample_count": 0},
        {"minimum_effective_sample_size": Decimal(0)},
        {"medium_confidence_effective_sample_size": Decimal("NaN")},
        {"high_confidence_effective_sample_size": Decimal(1)},
        {"hierarchical_prior_strength_cap": Decimal(-1)},
        {"recency_horizon": timedelta()},
        {"minimum_recency_weight": Decimal(0)},
        {"minimum_recency_weight": Decimal("1.1")},
        {"neutral_prior_alpha": Decimal(0)},
        {"neutral_prior_beta": Decimal("Infinity")},
    ),
)
def test_configuration_rejects_invalid_research_policy(changes: dict[str, object]) -> None:
    with pytest.raises(ExperienceEstimationError):
        ExperienceEstimationConfiguration(**changes)  # type: ignore[arg-type]


def test_estimation_dependency_and_scope_guards() -> None:
    tree = ast.parse(Path(estimation_module.__file__).read_text(encoding="utf-8"))
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    forbidden_dependencies = (
        "accounting",
        "dashboard",
        "decision",
        "exchange",
        "execution",
        "journal",
        "live",
        "paper",
        "pricing",
        "risk",
        "router",
        "strategies",
    )
    assert not any(token in module for module in imported for token in forbidden_dependencies)

    estimate_fields = {item.name for item in fields(ExperienceEstimate)}
    forbidden_scope = (
        "edge_score",
        "rank",
        "authorization",
        "permission",
        "mae",
        "mfe",
        "process_score",
    )
    assert not any(token in name for token in forbidden_scope for name in estimate_fields)
