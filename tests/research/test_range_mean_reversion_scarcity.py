"""Synthetic-only scarcity attribution and immutable prior-evidence checks."""

from dataclasses import fields
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.research.range_mean_reversion_scarcity import (
    ObservationAttribution,
    RangeScarcityError,
    RangeScarcityProjection,
    RangeScarcityReport,
    ScarcitySummary,
    TerminalStage,
    classify_summary,
    terminal_stage,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyReasonCode,
    OperationalSafetyStatus,
    RiskDecisionStatus,
    RiskReasonCode,
)
from adaptive_trading_bot.storage import SourceId
from adaptive_trading_bot.strategies import CandidateAction, RangeMeanReversionReasonCode
from adaptive_trading_bot.strategies.range_context import RangeReasonCode, RangeStatus

SYMBOL = TradingSymbol("BTC", "USDT")
SOURCE = SourceId("synthetic-range-scarcity")


def _row(
    index: int,
    *,
    ready: bool = True,
    range_status: RangeStatus = RangeStatus.VALID_RANGE,
    range_reasons: tuple[RangeReasonCode, ...] = (),
    action: CandidateAction = CandidateAction.NO_TRADE,
    strategy_reasons: tuple[RangeMeanReversionReasonCode, ...] = (),
    risk_status: RiskDecisionStatus = RiskDecisionStatus.NO_ACTION,
    safety_status: OperationalSafetyStatus = OperationalSafetyStatus.NO_ACTION,
    warmup: bool = False,
    month: int = 9,
) -> ObservationAttribution:
    identity = DecisionIdentity(
        SOURCE,
        SYMBOL,
        datetime(2026, month, index + 1, tzinfo=UTC),
        "range_mean_reversion",
        "1.0.0",
    )
    return ObservationAttribution(
        identity,
        ready,
        range_status,
        range_reasons,
        action,
        strategy_reasons,
        risk_status,
        (RiskReasonCode.STRATEGY_CANDIDATE_ACCEPTED,)
        if action is not CandidateAction.NO_TRADE
        else (),
        safety_status,
        (OperationalSafetyReasonCode.RISK_APPROVAL_VALID,)
        if risk_status is RiskDecisionStatus.APPROVED
        else (),
        warmup,
    )


def _all_terminal_rows() -> tuple[ObservationAttribution, ...]:
    return (
        _row(
            0,
            ready=False,
            range_status=RangeStatus.INSUFFICIENT_DATA,
            range_reasons=(RangeReasonCode.CONTEXT_NOT_READY, RangeReasonCode.FEATURES_NOT_WARMED),
            strategy_reasons=(RangeMeanReversionReasonCode.RANGE_INSUFFICIENT_DATA,),
        ),
        _row(
            1,
            range_status=RangeStatus.NOT_RANGE,
            range_reasons=(
                RangeReasonCode.NEED_FOUR_CONFIRMED_PIVOTS,
                RangeReasonCode.NEED_FOUR_CONFIRMED_PIVOTS,
            ),
            strategy_reasons=(RangeMeanReversionReasonCode.RANGE_NOT_VALID,),
        ),
        _row(
            2,
            range_status=RangeStatus.RANGE_BROKEN,
            range_reasons=(RangeReasonCode.RANGE_BROKEN_UP,),
            strategy_reasons=(RangeMeanReversionReasonCode.RANGE_BROKEN,),
        ),
        _row(
            3,
            range_status=RangeStatus.RANGE_STALE,
            range_reasons=(RangeReasonCode.RANGE_STALE,),
            strategy_reasons=(RangeMeanReversionReasonCode.RANGE_STALE,),
        ),
        _row(
            4,
            range_reasons=(RangeReasonCode.RANGE_GEOMETRY_VALID,),
            strategy_reasons=(
                RangeMeanReversionReasonCode.PRICE_NOT_AT_RANGE_EDGE,
                RangeMeanReversionReasonCode.RSI_EXHAUSTION_FAILED,
            ),
        ),
        _row(
            5,
            action=CandidateAction.LONG_CANDIDATE,
            risk_status=RiskDecisionStatus.REJECTED,
            safety_status=OperationalSafetyStatus.BLOCKED_BY_RISK,
        ),
        _row(
            6,
            action=CandidateAction.SHORT_CANDIDATE,
            risk_status=RiskDecisionStatus.APPROVED,
            safety_status=OperationalSafetyStatus.SAFE_MODE,
        ),
        _row(
            7,
            action=CandidateAction.LONG_CANDIDATE,
            risk_status=RiskDecisionStatus.APPROVED,
            safety_status=OperationalSafetyStatus.CLEARED,
            warmup=True,
            month=10,
        ),
        _row(
            8,
            action=CandidateAction.SHORT_CANDIDATE,
            risk_status=RiskDecisionStatus.APPROVED,
            safety_status=OperationalSafetyStatus.CLEARED,
            month=10,
        ),
    )


def test_all_terminal_stages_and_exact_multilabel_funnels() -> None:
    digest = "a" * 64
    report = RangeScarcityReport(
        "A", f"range-mean-reversion-research-corpus:{digest}", digest, _all_terminal_rows()
    )
    report.validate()
    counts = report.counts
    assert counts.total_observations == 9
    assert (counts.context_ready, counts.context_not_ready) == (8, 1)
    assert dict(counts.range_statuses) == {
        RangeStatus.INSUFFICIENT_DATA: 1,
        RangeStatus.NOT_RANGE: 1,
        RangeStatus.VALID_RANGE: 5,
        RangeStatus.RANGE_BROKEN: 1,
        RangeStatus.RANGE_STALE: 1,
    }
    assert dict(counts.range_reasons)[RangeReasonCode.NEED_FOUR_CONFIRMED_PIVOTS] == 2
    assert dict(counts.actions) == {
        CandidateAction.NO_TRADE: 5,
        CandidateAction.LONG_CANDIDATE: 2,
        CandidateAction.SHORT_CANDIDATE: 2,
    }
    assert dict(counts.no_trade_reasons)[RangeMeanReversionReasonCode.RANGE_BROKEN] == 1
    assert (
        dict(counts.valid_range_no_trade_reasons)[
            RangeMeanReversionReasonCode.PRICE_NOT_AT_RANGE_EDGE
        ]
        == 1
    )
    assert dict(counts.valid_range_no_trade_reasons)[RangeMeanReversionReasonCode.RANGE_BROKEN] == 0
    assert counts.candidate_utc_months == (("2026-09", 2), ("2026-10", 2))
    assert (counts.candidates_inside_warmup, counts.candidates_outside_warmup) == (1, 3)
    assert dict(counts.risk_statuses) == {
        RiskDecisionStatus.APPROVED: 3,
        RiskDecisionStatus.REJECTED: 1,
    }
    assert dict(counts.safety_statuses) == {
        OperationalSafetyStatus.CLEARED: 2,
        OperationalSafetyStatus.SAFE_MODE: 1,
        OperationalSafetyStatus.BLOCKED_BY_RISK: 1,
    }
    assert dict(counts.risk_reasons)[RiskReasonCode.STRATEGY_CANDIDATE_ACCEPTED] == 4
    assert dict(counts.safety_reasons)[OperationalSafetyReasonCode.RISK_APPROVAL_VALID] == 3
    assert (
        counts.otherwise_admissible_before_warmup,
        counts.otherwise_admissible_warmup_excluded,
        counts.final_research_admissible,
    ) == (2, 1, 1)
    assert dict(counts.terminals) == {
        stage: 1 if stage is not TerminalStage.RANGE_NOT_VALID else 3 for stage in TerminalStage
    }
    assert sum(value for _, value in counts.terminals) == counts.total_observations
    assert report.report_digest and report.integrity_seal


def test_terminal_precedence_and_fail_closed_mutation() -> None:
    rows = _all_terminal_rows()
    assert rows[0].terminal is TerminalStage.CONTEXT_NOT_READY
    assert rows[1].terminal is TerminalStage.RANGE_NOT_VALID
    assert rows[4].terminal is TerminalStage.VALID_RANGE_STRATEGY_NO_TRADE
    assert rows[5].terminal is TerminalStage.CANDIDATE_RISK_BLOCKED
    assert rows[6].terminal is TerminalStage.CANDIDATE_SAFETY_BLOCKED
    assert rows[7].terminal is TerminalStage.CANDIDATE_WARMUP_EXCLUDED
    assert rows[8].terminal is TerminalStage.RESEARCH_ADMISSIBLE
    with pytest.raises(RangeScarcityError, match="inconsistent"):
        terminal_stage(
            True,
            RangeStatus.VALID_RANGE,
            CandidateAction.LONG_CANDIDATE,
            RiskDecisionStatus.NO_ACTION,
            OperationalSafetyStatus.CLEARED,
            False,
        )
    digest = "b" * 64
    report = RangeScarcityReport(
        "B", f"range-mean-reversion-research-corpus:{digest}", digest, rows
    )
    object.__setattr__(report.observations[8], "warmup_excluded", True)
    with pytest.raises(RangeScarcityError, match="seal"):
        report.validate()


def test_descriptive_summaries_and_no_economic_dependency() -> None:
    assert classify_summary(0, 0) is ScarcitySummary.ZERO_STRATEGY_CANDIDATES
    assert classify_summary(4, 0) is ScarcitySummary.CANDIDATES_EXIST_BUT_NONE_ADMITTED
    assert classify_summary(4, 1) is ScarcitySummary.RESEARCH_ADMISSIBLE_CANDIDATES_EXIST
    with pytest.raises(RangeScarcityError):
        classify_summary(0, 1)
    names = {item.name for item in fields(RangeScarcityReport)}
    names |= {item.name for item in fields(type(_all_terminal_rows()[0]))}
    forbidden = {"pnl", "net_r", "win_rate", "profit_factor", "leverage", "liquidation"}
    assert all(not any(term in name for term in forbidden) for name in names)
    source = Path("src/adaptive_trading_bot/research/range_mean_reversion_scarcity.py").read_text()
    assert "BacktestExecutionSimulator" not in source
    assert "PositionAccountingEngine" not in source


def test_cross_run_projection_reconciles_and_seals_synthetic_counts() -> None:
    terminals = tuple(
        (stage, int(stage is TerminalStage.RESEARCH_ADMISSIBLE)) for stage in TerminalStage
    )
    empty = tuple((stage, 0) for stage in TerminalStage)
    projection = RangeScarcityProjection(
        ("A", "B", "C"),
        ("a" * 64, "b" * 64, "c" * 64),
        (1, 0, 0),
        (terminals, empty, empty),
        (1, 0, 0),
        (1, 0, 0),
        ScarcitySummary.RESEARCH_ADMISSIBLE_CANDIDATES_EXIST,
    )
    projection.validate()
    assert projection.to_json()
    object.__setattr__(projection, "strategy_candidate_counts", (0, 0, 0))
    with pytest.raises(RangeScarcityError, match="seal"):
        projection.validate()
    with pytest.raises(RangeScarcityError, match="inconsistent"):
        RangeScarcityProjection(
            ("A", "B", "C"),
            ("a" * 64, "b" * 64, "c" * 64),
            (1, 0, 0),
            (terminals, empty, empty),
            (1, 0, 0),
            (0, 0, 0),
            ScarcitySummary.RESEARCH_ADMISSIBLE_CANDIDATES_EXIST,
        )


def test_locked_prior_evidence_hashes_unchanged() -> None:
    expected = {
        "issue-144-signal-edge-b07e9eac7f373c4b": (
            "e4be5b89615197b9854e9137eae1a75b91c4d37b356d6e696b168c94e7e0d0c9"
        ),
        "issue-149-breakout-retest-4ed85e639a84d282": (
            "730fa5437a7487fd3dd21e4e8f7ade5185a77649fc751b751d406fb7f66505ed"
        ),
        "issue-153-range-mean-reversion-cd1f0858a3c6b4bc": (
            "7ad0be338213acca4e094eb41de4873780d16f850044de76b9423d30360e570a"
        ),
    }
    for directory, digest in expected.items():
        path = Path("data/research_evidence") / directory / "manifest.json"
        assert sha256(path.read_bytes()).hexdigest() == digest
