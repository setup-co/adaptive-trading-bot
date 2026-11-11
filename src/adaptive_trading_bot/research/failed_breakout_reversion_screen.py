"""Fixed BTC/ETH failed-breakout reversion screen over accepted 5m LAST data."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from adaptive_trading_bot.domain import Candle, FrozenClock, Timeframe, TradingSymbol
from adaptive_trading_bot.execution import SimulatedExecutionCostConfiguration
from adaptive_trading_bot.features import (
    FeatureEngine,
    MarketStructureEngine,
    observed_candles_from_replay,
)
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, OrderIntentAuthorizationStatus
from adaptive_trading_bot.regime import MultiTimeframeRegimeComposer, TechnicalRegimeEngine
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.research.last_5m_protective_execution import (
    ExitPolicy,
    Last5mBar,
    Last5mExecutionBinding,
    Last5mExecutionStatus,
    Last5mProtectionConfiguration,
    Last5mProtectiveExecutionResult,
    ProtectionAcknowledgement,
    TriggerKind,
    run_last_5m_protective_execution,
)
from adaptive_trading_bot.research.research_v2_corpus import FIELDS, Row, derive, epochs
from adaptive_trading_bot.research.trend_pullback_corpus import (
    ResearchSafetyAssumptionProfile,
    _research_health,
)
from adaptive_trading_bot.risk import (
    OperationalSafetyGate,
    OperationalSafetyStatus,
    RiskDecisionStatus,
    RiskManager,
    RiskPortfolioSnapshot,
)
from adaptive_trading_bot.storage import IntegrityReport, MergeResult, SourceId
from adaptive_trading_bot.strategies import (
    FailedBreakoutReversionReasonCode,
    FailedBreakoutReversionRuleAudit,
    FailedBreakoutReversionStrategy,
    assemble_strategy_contexts,
)
from adaptive_trading_bot.strategies.core import CandidateAction

PROTOCOL_ID = "BTC_ETH_FAILED_BREAKOUT_REVERSION_15M_30M_V1"
DATASET_VERSION = "weex-history-screened-196d-last-continuation-v1"
SOURCE_ID = SourceId(DATASET_VERSION)
ROOT = Path("data/research_corpus/history-screened-196d-last-continuation-v1")
MANIFEST_SHA256 = "756659f6700a2ae48b5c5aa58972f037c709116ca73b9c5c23571617d24d4e9d"
REPORT_SHA256 = "bccffb447373f8fdc0d477580c9b78dcf5f4a5ca5777620df88026507e5b7e66"
PLAN_SHA256 = "dc51e06df9c4b40935cbb705b25d4683ff63094f5b475f6fbf82f1a085e3a23f"
SHARDS = {
    "BTCUSDT": "8b0f88bb0bcb421c54f5c160c29dae9718802c29ae6d6059ce7bf4fa7ea07a78",
    "ETHUSDT": "41deb1ba9c0b473704316b69f51444c6c22c7ae4712038234cf422f74a5fc23d",
}
CONTEXT_START = datetime(2026, 4, 1, tzinfo=UTC)
SIGNAL_START = datetime(2026, 4, 10, tzinfo=UTC)
SIGNAL_END = datetime(2026, 10, 7, tzinfo=UTC)
TAIL_END = datetime(2026, 10, 7, 0, 40, tzinfo=UTC)
RESEARCH_EQUITY = Decimal("10000")
FRAMES = (Timeframe.MINUTES_15, Timeframe.HOUR_1, Timeframe.HOURS_4)
_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True, slots=True)
class ScreenInputAuthority:
    """Exact corpus/calendar authority, substitutable only by bounded synthetic tests."""

    source_id: SourceId
    dataset_version: str
    root: Path
    manifest_sha256: str
    report_sha256: str
    plan_sha256: str
    shards: tuple[tuple[str, str], ...]
    valid_5m_rows: int
    epoch_start_ms: int
    epoch_end_ms: int
    report_start: str
    report_end: str
    context_start: datetime
    signal_start: datetime
    signal_end: datetime
    tail_end: datetime

    @property
    def shard_map(self) -> dict[str, str]:
        return dict(self.shards)


DEFAULT_INPUT_AUTHORITY = ScreenInputAuthority(
    SOURCE_ID,
    DATASET_VERSION,
    ROOT,
    MANIFEST_SHA256,
    REPORT_SHA256,
    PLAN_SHA256,
    tuple(SHARDS.items()),
    56_448,
    1_775_001_600_000,
    1_791_936_000_000,
    "2026-04-01T00:00:00Z",
    "2026-10-14T00:00:00Z",
    CONTEXT_START,
    SIGNAL_START,
    SIGNAL_END,
    TAIL_END,
)


class ScreenError(ValueError):
    """The frozen protocol, input, or output state is contradictory."""


class Scenario(StrEnum):
    BASE = "BASE"
    STRESS = "STRESS"


def scenario_costs(scenario: Scenario) -> SimulatedExecutionCostConfiguration:
    if scenario is Scenario.BASE:
        return SimulatedExecutionCostConfiguration(Decimal("2"), Decimal("2"), Decimal("8"))
    if scenario is Scenario.STRESS:
        return SimulatedExecutionCostConfiguration(
            Decimal("4"), Decimal("4"), Decimal("16"), timedelta(seconds=1)
        )
    raise ScreenError("unknown scenario")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _file_sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _text(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value.total_seconds())
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_text(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _text(item) for key, item in value.items()}
    return value


def protocol_record() -> dict[str, object]:
    risk = RiskManager()
    safety = OperationalSafetyGate()
    assumptions = ResearchSafetyAssumptionProfile()
    return {
        "protocol": PROTOCOL_ID,
        "strategy": ["range_failed_breakout_reversion", "0.1.0"],
        "symbols": list(SHARDS),
        "calendar": {
            "context_start": CONTEXT_START.isoformat(),
            "signal_start": SIGNAL_START.isoformat(),
            "signal_end_exclusive_open": SIGNAL_END.isoformat(),
            "tail_end": TAIL_END.isoformat(),
        },
        "rule": {
            "reference_bars": 16,
            "entry_timeframe": "15m",
            "target": "SEALED_PREVIOUS_16_MIDPOINT",
            "stop_buffer_atr14": "0.10",
            "cost_room_factor": "3",
            "holding_minutes_from_actual_fill": 30,
            "spacing_minutes": 35,
        },
        "scenarios": {
            scenario.value: {
                "spread_bps": str(scenario_costs(scenario).spread_bps),
                "slippage_bps": str(scenario_costs(scenario).slippage_bps),
                "commission_bps": str(scenario_costs(scenario).fee_bps),
                "entry_latency_seconds": str(scenario_costs(scenario).latency.total_seconds()),
            }
            for scenario in Scenario
        },
        "equity_usdt": str(RESEARCH_EQUITY),
        "admission": _text(
            {
                "risk_manager": asdict(risk.configuration),
                "operational_safety": asdict(safety.configuration),
                "research_safety_assumptions": asdict(assumptions),
                "carried_positions": [],
                "equity_compounding": False,
            }
        ),
        "funding_status": "NOT_MODELED",
        "classification": "PREDECLARED_OPERATIONAL_SCREEN_V1",
    }


PROTOCOL_SHA256 = _sha(_canonical(protocol_record()))


class _MemoryStore:
    def __init__(
        self,
        symbol: TradingSymbol,
        candles: dict[Timeframe, tuple[Candle, ...]],
        *,
        source_id: SourceId = SOURCE_ID,
    ) -> None:
        self.source_id = source_id
        self.symbol = symbol
        self.candles = candles

    def merge(self, source_id: SourceId, candles: Sequence[Candle]) -> MergeResult:
        del source_id, candles
        raise ScreenError("screen store is immutable")

    def read(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[Candle, ...]:
        if source_id != self.source_id or symbol != self.symbol or start is None or end is None:
            raise ScreenError("screen read identity is invalid")
        return tuple(
            candle for candle in self.candles[timeframe] if start <= candle.open_time < end
        )

    def scan_integrity(
        self,
        source_id: SourceId,
        symbol: TradingSymbol,
        timeframe: Timeframe,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> IntegrityReport:
        values = self.read(source_id, symbol, timeframe, start=start, end=end)
        return IntegrityReport(source_id, symbol, timeframe, start, end, len(values), ())


class AcceptedInput:
    """Hash each accepted authority once and retain parsed BTC/ETH bars."""

    def __init__(
        self, repo: Path, authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY
    ) -> None:
        self.repo = repo.resolve(strict=True)
        self.authority = authority
        self.root = self.repo / authority.root
        self.manifest = self._json_once("manifest.json", authority.manifest_sha256)
        self.report = self._json_once("report.json", authority.report_sha256)
        self._exact(self.root / "plan.json", authority.plan_sha256)
        self._validate_common()
        self._cache: dict[str, tuple[_MemoryStore, tuple[Last5mBar, ...]]] = {}

    def _exact(self, path: Path, expected: str) -> bytes:
        body = path.read_bytes()
        if _sha(body) != expected:
            raise ScreenError(f"accepted input changed: {path.name}")
        return body

    def _json_once(self, name: str, expected: str) -> dict[str, Any]:
        value = json.loads(self._exact(self.root / name, expected))
        if not isinstance(value, dict):
            raise ScreenError(f"accepted {name} is not an object")
        return value

    def _validate_common(self) -> None:
        files = self.manifest.get("files")
        if (
            self.manifest.get("status") != "QUALIFIED"
            or self.manifest.get("capture_status") != "CAPTURE_COMPLETE"
            or self.manifest.get("dataset_version") != self.authority.dataset_version
            or self.manifest.get("plan_sha256") != self.authority.plan_sha256
            or self.report.get("status") != "QUALIFIED"
            or self.report.get("dataset_version") != self.authority.dataset_version
            or self.report.get("start") != self.authority.report_start
            or self.report.get("end") != self.authority.report_end
            or not isinstance(files, dict)
            or files.get("report.json") != self.authority.report_sha256
        ):
            raise ScreenError("accepted corpus authority is contradictory")

    def load(self, wire: str) -> tuple[_MemoryStore, tuple[Last5mBar, ...]]:
        if wire in self._cache:
            return self._cache[wire]
        shards = self.authority.shard_map
        if wire not in shards:
            raise ScreenError("screen symbol is outside BTC/ETH")
        symbols = self.report.get("symbols")
        facts = symbols.get(wire) if isinstance(symbols, dict) else None
        if (
            not isinstance(facts, dict)
            or facts.get("status") != "QUALIFIED"
            or facts.get("valid_5m_rows") != self.authority.valid_5m_rows
            or facts.get("missing_5m_rows") != 0
            or facts.get("epochs")
            != [
                {
                    "start_ms": self.authority.epoch_start_ms,
                    "end_ms": self.authority.epoch_end_ms,
                    "rows": self.authority.valid_5m_rows,
                }
            ]
        ):
            raise ScreenError("accepted member coverage report changed")
        relative = f"parquet/{wire}-5m.parquet"
        files = self.manifest["files"]
        if files.get(relative) != shards[wire]:
            raise ScreenError("5m shard manifest membership changed")
        body = self._exact(self.root / relative, shards[wire])
        table = pq.read_table(pa.BufferReader(body))
        expected_schema = pa.schema(
            [pa.field("symbol", pa.string()), pa.field("timeframe", pa.string())]
            + [
                pa.field(
                    name,
                    pa.int64() if name.endswith("_ms") or name == "trade_count" else pa.string(),
                )
                for name in FIELDS
            ]
        )
        if not table.schema.equals(expected_schema, check_metadata=False):
            raise ScreenError("5m shard schema changed")
        rows: dict[int, Row] = {}
        for item in table.to_pylist():
            if item["symbol"] != wire or item["timeframe"] != "5m":
                raise ScreenError("5m shard contains a foreign identity")
            row = Row(
                item["open_time_ms"],
                Decimal(item["open"]),
                Decimal(item["high"]),
                Decimal(item["low"]),
                Decimal(item["close"]),
                Decimal(item["base_volume"]),
                item["close_time_ms"],
                Decimal(item["quote_volume"]),
                item["trade_count"],
                Decimal(item["taker_buy_base_volume"]),
                Decimal(item["taker_buy_quote_volume"]),
            )
            if row.open_time_ms in rows:
                raise ScreenError("5m shard contains a duplicate open")
            if (
                row.close_time_ms != row.open_time_ms + 300_000
                or row.low > min(row.open, row.close)
                or row.high < max(row.open, row.close)
                or row.low > row.high
                or min(row.open, row.high, row.low, row.close) <= 0
                or row.base_volume < 0
            ):
                raise ScreenError("5m shard contains an invalid canonical row")
            rows[row.open_time_ms] = row
        if len(rows) != self.authority.valid_5m_rows or epochs(rows) != [
            (self.authority.epoch_start_ms, self.authority.epoch_end_ms)
        ]:
            raise ScreenError("5m shard coverage changed")
        derived = {
            Timeframe.MINUTES_15: derive(rows, epochs(rows), 3),
            Timeframe.HOUR_1: derive(rows, epochs(rows), 12),
            Timeframe.HOURS_4: derive(rows, epochs(rows), 48),
        }
        symbol = TradingSymbol(wire.removesuffix("USDT"), "USDT")
        candles = {
            frame: tuple(_candle(symbol, frame, row) for row in values)
            for frame, values in derived.items()
        }
        five = tuple(
            Last5mBar(
                self.authority.source_id,
                symbol,
                "accepted-complete-196d",
                _instant(row.open_time_ms),
                _instant(row.close_time_ms),
                row.open,
                row.high,
                row.low,
                row.close,
                row.base_volume,
            )
            for row in rows.values()
            if self.authority.context_start <= _instant(row.open_time_ms) < self.authority.tail_end
        )
        value = (_MemoryStore(symbol, candles, source_id=self.authority.source_id), five)
        self._cache[wire] = value
        return value


def _instant(milliseconds: int) -> datetime:
    return datetime.fromtimestamp(milliseconds // 1000, tz=UTC) + timedelta(
        milliseconds=milliseconds % 1000
    )


def _candle(symbol: TradingSymbol, timeframe: Timeframe, row: Row) -> Candle:
    return Candle(
        symbol,
        timeframe,
        _instant(row.open_time_ms),
        _instant(row.close_time_ms),
        row.open,
        row.high,
        row.low,
        row.close,
        row.base_volume,
    )


def _clean_sha(repo: Path, expected: str) -> None:
    status = subprocess.run(
        ("git", "status", "--porcelain"), cwd=repo, check=True, capture_output=True, text=True
    ).stdout
    actual = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    if status or actual != expected:
        raise ScreenError("historical screen requires the exact clean frozen executable")


def _append_jsonl(path: Path, value: dict[str, object]) -> None:
    line = _canonical(_text(value)) + b"\n"
    with path.open("ab") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _replace(path: Path, value: object) -> None:
    pending = path.with_suffix(path.suffix + ".pending")
    pending.write_bytes(_canonical(_text(value)))
    os.replace(pending, path)


def _decision_record(decision: DecisionJournalEntry[object, object]) -> dict[str, Any]:
    evaluation = decision.strategy_evaluation
    audit = evaluation.rule_audit
    return {
        "observed_at": decision.identity.observed_at,
        "action": evaluation.action.value,
        "reasons_for": [str(item) for item in evaluation.reasons_for],
        "reasons_against": [str(item) for item in evaluation.reasons_against],
        "risk_status": decision.risk_decision.status.value,
        "safety_status": decision.safety_decision.status.value,
        "range_high": getattr(audit, "reference_high", None),
        "range_low": getattr(audit, "reference_low", None),
        "midpoint": getattr(audit, "sealed_midpoint", None),
        "atr14": getattr(audit, "atr_14", None),
        "stop": evaluation.invalidation_reference_price,
    }


def _result_record(
    wire: str,
    scenario: Scenario,
    decision: DecisionJournalEntry[object, object],
    result: Last5mProtectiveExecutionResult,
) -> dict[str, Any]:
    audit = decision.strategy_evaluation.rule_audit
    assert isinstance(audit, FailedBreakoutReversionRuleAudit)
    return {
        "symbol": wire,
        "scenario": scenario.value,
        "decision_at": decision.identity.observed_at,
        "side": result.intent_audit.side.value,
        "range_high": audit.reference_high,
        "range_low": audit.reference_low,
        "midpoint": audit.sealed_midpoint,
        "atr14": audit.atr_14,
        "stop": result.original_stop_basis,
        "quantity": result.intent_audit.quantity,
        "approved_risk_quote": result.approved_risk_quote,
        "status": result.status.value,
        "entry_eligible_at": result.entry_eligible_at,
        "entry_grid_wait_seconds": (
            None if result.entry_grid_wait is None else result.entry_grid_wait.total_seconds()
        ),
        "entry": None if result.entry_fill is None else _text(asdict(result.entry_fill)),
        "exit": None if result.exit_fill is None else _text(asdict(result.exit_fill)),
        "trigger": None if result.trigger is None else _text(asdict(result.trigger)),
        "gross_pnl_quote": result.realized_gross_pnl,
        "total_fees_quote": result.total_fees,
        "net_pnl_quote": result.realized_net_pnl,
        "closed_net_r": result.closed_net_approved_risk_r,
        "unresolved_quantity": result.unresolved_quantity,
        "holding_lower_seconds": (
            None
            if result.holding_duration_lower is None
            else result.holding_duration_lower.total_seconds()
        ),
        "holding_upper_seconds": (
            None
            if result.holding_duration_upper is None
            else result.holding_duration_upper.total_seconds()
        ),
        "events": list(result.events),
        "series_identity": result.series_identity,
        "binding_identity": result.binding.identity,
    }


def run_member(
    wire: str,
    store: _MemoryStore,
    five: tuple[Last5mBar, ...],
    output: Path,
    *,
    context_start: datetime = CONTEXT_START,
    signal_start: datetime = SIGNAL_START,
    signal_end: datetime = SIGNAL_END,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    symbol = store.symbol
    source_id = store.source_id
    replay = ReplayEngine(HistoricalReplaySource(store)).prepare(
        tuple(
            ReplaySeries(source_id, symbol, frame, context_start, signal_end) for frame in FRAMES
        ),
        clock=FrozenClock(context_start),
    )
    features = FeatureEngine()
    structures = MarketStructureEngine()
    regimes = TechnicalRegimeEngine()
    composer = MultiTimeframeRegimeComposer()
    strategy = FailedBreakoutReversionStrategy()
    risk = RiskManager()
    safety = OperationalSafetyGate()
    assumptions = ResearchSafetyAssumptionProfile()
    counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    decision_count = 0
    admitted_times: list[datetime] = []
    five_by_open = {bar.open_time: bar for bar in five}
    trade_rows: list[dict[str, Any]] = []
    last_heartbeat = datetime.now(UTC)
    for batch in replay:
        observed = observed_candles_from_replay(batch)
        feature_batch = features.process(observed)
        structure_batch = structures.process(observed)
        regime_batch = regimes.process(feature_batch, structure_batch)
        mtf = composer.process(regime_batch)
        for context in assemble_strategy_contexts(observed, feature_batch, structure_batch, mtf):
            evaluation = strategy.evaluate(context)
            if not signal_start < context.observed_at <= signal_end:
                continue
            counts["observations"] += 1
            for reason in evaluation.reasons_against:
                reason_counts[str(reason)] += 1
            if evaluation.action is not CandidateAction.NO_TRADE:
                counts["raw_setups"] += 1
            elif (
                FailedBreakoutReversionReasonCode.BASE_COST_ROOM_REJECTED
                in evaluation.reasons_against
            ):
                counts["raw_setups"] += 1
                counts["cost_rejected"] += 1
            equity = RESEARCH_EQUITY
            portfolio = RiskPortfolioSnapshot(
                context.observed_at, "USDT", equity, equity, equity, ()
            )
            risk_decision = risk.evaluate(evaluation, portfolio)
            if (
                evaluation.action is not CandidateAction.NO_TRADE
                and risk_decision.status is RiskDecisionStatus.REJECTED
            ):
                counts["risk_rejected"] += 1
            safety_decision = safety.evaluate(risk_decision, _research_health(context, assumptions))
            if (
                risk_decision.status is RiskDecisionStatus.APPROVED
                and safety_decision.status is not OperationalSafetyStatus.CLEARED
            ):
                counts["safety_rejected"] += 1
            decision = DecisionJournalEntry(context, evaluation, risk_decision, safety_decision)
            decision.validate()
            decision_count += 1
            authorization = EntryOrderIntentAuthorizer().authorize(decision)
            if authorization.status is not OrderIntentAuthorizationStatus.AUTHORIZED:
                continue
            intent = authorization.intent
            assert intent is not None
            counts["admitted"] += 1
            admitted_times.append(context.observed_at)
            audit = evaluation.rule_audit
            assert isinstance(audit, FailedBreakoutReversionRuleAudit)
            assert audit.sealed_midpoint is not None
            start = context.observed_at
            end = start + timedelta(minutes=40)
            bars = tuple(five_by_open[start + timedelta(minutes=5 * index)] for index in range(8))
            binding = Last5mExecutionBinding(source_id, symbol, bars[0].epoch_id, start, end)
            for scenario in Scenario:
                result = run_last_5m_protective_execution(
                    intent=intent,
                    binding=binding,
                    bars=bars,
                    configuration=Last5mProtectionConfiguration(
                        scenario_costs(scenario),
                        ExitPolicy.FROZEN_TARGET_30M,
                        ProtectionAcknowledgement.ACCEPTED,
                        timedelta(0),
                        frozen_target_price=audit.sealed_midpoint,
                        holding_period=timedelta(minutes=30),
                    ),
                )
                row = _result_record(wire, scenario, decision, result)
                trade_rows.append(row)
                _append_jsonl(output / "trades.jsonl", row)
        if datetime.now(UTC) - last_heartbeat >= timedelta(seconds=30):
            _replace(
                output / "progress.json",
                {
                    "phase": "EVALUATING",
                    "symbol": wire,
                    "observed_at": batch.observed_at,
                    "counts": dict(counts),
                    "trade_scenario_rows": len(trade_rows),
                    "heartbeat_at": datetime.now(UTC),
                },
            )
            last_heartbeat = datetime.now(UTC)
    spaced = _spacing_mask(admitted_times)
    explicit_counts = {
        name: counts[name]
        for name in (
            "observations",
            "raw_setups",
            "cost_rejected",
            "risk_rejected",
            "safety_rejected",
            "admitted",
        )
    }
    return (
        {
            "symbol": wire,
            "counts": explicit_counts,
            "reason_counts": dict(sorted(reason_counts.items())),
            "decision_count": decision_count,
            "spacing_mask": [item.isoformat() for item in spaced],
            "spacing_mask_count": len(spaced),
        },
        trade_rows,
    )


def _spacing_mask(admitted_times: Sequence[datetime]) -> list[datetime]:
    spaced: list[datetime] = []
    for observed_at in admitted_times:
        if not spaced or observed_at >= spaced[-1] + timedelta(minutes=35):
            spaced.append(observed_at)
    return spaced


def _decision_time_identity(value: object) -> str:
    if isinstance(value, str):
        instant = datetime.fromisoformat(value)
    elif isinstance(value, datetime):
        instant = value
    else:
        raise ScreenError("decision time identity must be a datetime or ISO-8601 string")
    if instant.tzinfo is None or instant.utcoffset() != timedelta(0):
        raise ScreenError("decision time identity must be UTC")
    return instant.astimezone(UTC).isoformat()


def _decimals(rows: list[dict[str, Any]], key: str) -> list[Decimal]:
    return [Decimal(str(row[key])) for row in rows if row.get(key) is not None]


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    with localcontext(_CONTEXT):
        r_values = _decimals(rows, "closed_net_r")
        positives = [value for value in r_values if value > 0]
        negatives = [value for value in r_values if value < 0]
        gross = sum(_decimals(rows, "gross_pnl_quote"), Decimal(0))
        fees = sum(_decimals(rows, "total_fees_quote"), Decimal(0))
        net = sum(_decimals(rows, "net_pnl_quote"), Decimal(0))
        entry_fees = sum(
            (Decimal(str(row["entry"]["fee_quote"])) for row in rows if row.get("entry")),
            Decimal(0),
        )
        exit_fees = sum(
            (Decimal(str(row["exit"]["fee_quote"])) for row in rows if row.get("exit")),
            Decimal(0),
        )
        positive_sum = sum(positives, Decimal(0))
        negative_sum = -sum(negatives, Decimal(0))
        sorted_r = sorted(r_values)
        median = None
        if sorted_r:
            midpoint = len(sorted_r) // 2
            median = (
                sorted_r[midpoint]
                if len(sorted_r) % 2
                else (sorted_r[midpoint - 1] + sorted_r[midpoint]) / Decimal(2)
            )
        top_three = sorted(positives, reverse=True)[:3]
        without_top = list(r_values)
        for value in top_three:
            without_top.remove(value)
        mean = sum(r_values, Decimal(0)) / len(r_values) if r_values else None
        profit_factor = positive_sum / negative_sum if negative_sum else None
        average_win = positive_sum / len(positives) if positives else None
        average_loss = sum(negatives, Decimal(0)) / len(negatives) if negatives else None
        top_three_share = sum(top_three, Decimal(0)) / positive_sum if positive_sum else None
        without_top_mean = sum(without_top, Decimal(0)) / len(without_top) if without_top else None
    statuses = Counter(str(row["status"]) for row in rows)
    exits = Counter(str(row["trigger"]["kind"]) if row.get("trigger") else "NONE" for row in rows)
    waits = [
        Decimal(str(row["entry_grid_wait_seconds"]))
        for row in rows
        if row.get("entry_grid_wait_seconds") is not None
    ]
    holding_lower = _decimals(rows, "holding_lower_seconds")
    holding_upper = _decimals(rows, "holding_upper_seconds")
    return {
        "denominator": len(rows),
        "filled": sum(row.get("entry") is not None for row in rows),
        "closed": len(r_values),
        "unfilled": statuses[Last5mExecutionStatus.ENTRY_UNFILLED.value],
        "recovered": statuses[Last5mExecutionStatus.INVALID_GEOMETRY_RECOVERED.value],
        "unresolved": sum(Decimal(str(row["unresolved_quantity"])) != 0 for row in rows),
        "status_counts": dict(sorted(statuses.items())),
        "exit_counts": dict(sorted(exits.items())),
        "mean_net_r": mean,
        "median_net_r": median,
        "profit_factor": profit_factor,
        "wins": len(positives),
        "losses": len(negatives),
        "zeros": len(r_values) - len(positives) - len(negatives),
        "average_win_r": average_win,
        "average_loss_r": average_loss,
        "gross_pnl_quote": gross,
        "entry_commission_quote": entry_fees,
        "exit_commission_quote": exit_fees,
        "total_fees_quote": fees,
        "modeled_net_pnl_quote": net,
        "funding_status": "NOT_MODELED",
        "holding_lower_seconds_min": min(holding_lower) if holding_lower else None,
        "holding_upper_seconds_max": max(holding_upper) if holding_upper else None,
        "positive_grid_wait_count": sum(value > 0 for value in waits),
        "zero_grid_wait_count": sum(value == 0 for value in waits),
        "dual_touch_count": exits[TriggerKind.DUAL_TOUCH_STOP_FIRST.value],
        "top_three_positive_r": top_three,
        "top_three_share_of_positive_r": top_three_share,
        "mean_net_r_without_top_three_positive": without_top_mean,
    }


def _block(signal_open: datetime, signal_start: datetime = SIGNAL_START) -> str:
    day = (signal_open - signal_start).days
    return "BLOCK_1" if day < 60 else "BLOCK_2" if day < 120 else "BLOCK_3"


def _projection(
    run_id: str,
    executable_sha: str,
    members: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY,
) -> dict[str, Any]:
    shards = authority.shard_map
    by_member = {str(member["symbol"]): member for member in members}
    table: dict[str, Any] = {}
    complete = True
    candidate = True
    for wire in shards:
        mask = set(by_member[wire]["spacing_mask"])
        for scenario in Scenario:
            full = [
                row for row in rows if row["symbol"] == wire and row["scenario"] == scenario.value
            ]
            spaced = [row for row in full if _decision_time_identity(row["decision_at"]) in mask]
            table[f"{wire}:{scenario.value}:FULL"] = _metrics(full)
            table[f"{wire}:{scenario.value}:SPACED"] = _metrics(spaced)
            full_metrics = table[f"{wire}:{scenario.value}:FULL"]
            spaced_metrics = table[f"{wire}:{scenario.value}:SPACED"]
            assert isinstance(full_metrics, dict) and isinstance(spaced_metrics, dict)
            if full_metrics["unresolved"] or spaced_metrics["unresolved"]:
                complete = False
            if (
                spaced_metrics["closed"] < 30
                or full_metrics["mean_net_r"] is None
                or spaced_metrics["mean_net_r"] is None
                or full_metrics["mean_net_r"] <= 0
                or spaced_metrics["mean_net_r"] <= 0
                or full_metrics["profit_factor"] is None
                or spaced_metrics["profit_factor"] is None
                or full_metrics["profit_factor"] <= 1
                or spaced_metrics["profit_factor"] <= 1
            ):
                candidate = False
    blocks: dict[str, Any] = {}
    for wire in shards:
        mask = set(by_member[wire]["spacing_mask"])
        for scenario in Scenario:
            for block in ("BLOCK_1", "BLOCK_2", "BLOCK_3"):
                values = [
                    row
                    for row in rows
                    if row["symbol"] == wire
                    and row["scenario"] == scenario.value
                    and _decision_time_identity(row["decision_at"]) in mask
                    and _block(
                        datetime.fromisoformat(_decision_time_identity(row["decision_at"]))
                        - timedelta(minutes=15),
                        authority.signal_start,
                    )
                    == block
                ]
                blocks[f"{wire}:{scenario.value}:{block}"] = _metrics(values)
        positive_base_blocks = sum(
            isinstance(blocks[f"{wire}:BASE:{block}"], dict)
            and blocks[f"{wire}:BASE:{block}"]["mean_net_r"] is not None
            and blocks[f"{wire}:BASE:{block}"]["mean_net_r"] > 0
            for block in ("BLOCK_1", "BLOCK_2", "BLOCK_3")
        )
        if positive_base_blocks < 2:
            candidate = False
    if not complete:
        classification = "INCOMPLETE_ECONOMIC_EVIDENCE"
    elif any(
        table[f"{wire}:{scenario.value}:SPACED"]["closed"] < 30
        for wire in shards
        for scenario in Scenario
    ):
        classification = "INSUFFICIENT_EVIDENCE"
    elif candidate:
        classification = "CANDIDATE_FOR_REVIEW_PRE_FUNDING"
    else:
        means = [table[key]["mean_net_r"] for key in table if table[key]["mean_net_r"] is not None]
        classification = (
            "NEGATIVE" if means and all(value <= 0 for value in means) else "MIXED_FRAGILE"
        )
    return {
        "schema": "failed-breakout-reversion-screen-result-v1",
        "protocol": PROTOCOL_ID,
        "protocol_sha256": PROTOCOL_SHA256,
        "run_id": run_id,
        "executable_sha": executable_sha,
        "source_id": authority.source_id.value,
        "dataset_version": authority.dataset_version,
        "input_hashes": {
            "manifest": authority.manifest_sha256,
            "report": authority.report_sha256,
            "plan": authority.plan_sha256,
            **{f"{wire}-5m": digest for wire, digest in shards.items()},
        },
        "members": members,
        "economic_table": table,
        "chronological_60d_blocks": blocks,
        "classification": classification,
        "promotion": "NO_PROMOTION"
        if classification != "CANDIDATE_FOR_REVIEW_PRE_FUNDING"
        else "REVIEW_ONLY",
        "limitations": [
            "INDEPENDENT_OPPORTUNITIES_NOT_SHARED_CAPITAL_PORTFOLIO",
            "NET_AFTER_MODELED_COMMISSION_SPREAD_SLIPPAGE_BEFORE_FUNDING_AND_OTHER_PERPETUAL_MECHANICS",
            "LAST_5M_BAR_MODEL_NOT_TICK_ACCURATE_OR_GUARANTEED_VENUE_EXECUTION",
            "SELECTION_INFORMED_EXPLORATORY_DATA_NOT_UNTOUCHED_HOLDOUT",
        ],
    }


def run(
    repo: Path,
    output: Path,
    *,
    run_id: str,
    executable_sha: str,
    authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY,
) -> dict[str, Any]:
    repo = repo.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise ScreenError("run output already exists; historical retry is forbidden")
    _clean_sha(repo, executable_sha)
    output.mkdir(parents=True)
    _replace(
        output / "run-identity.json",
        {
            "run_id": run_id,
            "executable_sha": executable_sha,
            "protocol_sha256": PROTOCOL_SHA256,
            "started_at": datetime.now(UTC),
            "historical_attempt": 1,
        },
    )
    _replace(output / "protocol.json", protocol_record())
    accepted = AcceptedInput(repo, authority)
    members: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for wire in authority.shard_map:
        store, five = accepted.load(wire)
        member, member_rows = run_member(
            wire,
            store,
            five,
            output,
            context_start=authority.context_start,
            signal_start=authority.signal_start,
            signal_end=authority.signal_end,
        )
        members.append(member)
        rows.extend(member_rows)
        _replace(output / f"{wire.lower()}-member.json", member)
    projection = _projection(run_id, executable_sha, members, rows, authority)
    _replace(output / "projection.json", projection)
    _replace(
        output / "complete.json",
        {
            "completed_at": datetime.now(UTC),
            "projection_sha256": _file_sha(output / "projection.json"),
            "trade_rows": len(rows),
            "status": "COMPLETE",
        },
    )
    return projection
