"""Research-only 5m LAST entry/protection simulation for authorized 15m decisions.

This adapter is deliberately outside the runtime timeframe and strategy registries.  It
does not make decisions: it consumes an already-authorized ``EntryOrderIntent`` and a
finite, identity-bound 5m LAST series.  Bar-level ordering is an explicit conservative
model, not a claim of tick-accurate or venue-guaranteed execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from hashlib import sha256
from json import dumps
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from adaptive_trading_bot.accounting import (
    PositionAccountingCore,
    PositionAccountingFill,
    PositionAccountingFillBatch,
    PositionAccountingState,
)
from adaptive_trading_bot.domain import TradingSymbol
from adaptive_trading_bot.domain.clock import require_utc
from adaptive_trading_bot.execution import (
    SimulatedExecutionCostConfiguration,
    SimulatedExecutionSide,
    price_simulated_market_fill,
)
from adaptive_trading_bot.journal import DecisionIdentity
from adaptive_trading_bot.orders import EntryOrderIntent, EntrySide
from adaptive_trading_bot.research.breakout_retest_replay import (
    BreakoutRetestReplayError,
    fixed_2r_geometry,
)
from adaptive_trading_bot.research.research_v2_corpus import FIELDS as CORPUS_FIELDS
from adaptive_trading_bot.storage import SourceId

PROFILE_ID = "LAST_5M_PROTECTIVE_EXECUTION_V1"
PROFILE_VERSION = "1.0.0"
_BAR = timedelta(minutes=5)
_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


class Last5mProtectiveExecutionError(ValueError):
    """The research binding, series, or execution request is contradictory."""


class TriggerBasis(StrEnum):
    """Only LAST is authorized by this bounded research profile."""

    LAST = "LAST"
    MARK = "MARK"


class ProtectionAcknowledgement(StrEnum):
    """Modeled protective-order acknowledgement outcome."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNACKNOWLEDGED = "UNACKNOWLEDGED"


class ExitPolicy(StrEnum):
    """Explicit exit-policy selection supported by this research adapter."""

    FIXED_2R = "BREAKOUT_RETEST_FIXED_2R_V1"


class Last5mExecutionStatus(StrEnum):
    """Terminal adapter outcomes; open exposure is never called a close."""

    ENTRY_UNFILLED = "ENTRY_UNFILLED"
    ORDINARY_CLOSED = "ORDINARY_CLOSED"
    INVALID_GEOMETRY_RECOVERED = "INVALID_GEOMETRY_RECOVERED"
    INVALID_GEOMETRY_UNRESOLVED = "INVALID_GEOMETRY_UNRESOLVED"
    PROTECTION_REJECTED_OPEN = "PROTECTION_REJECTED_OPEN"
    PROTECTION_UNACKNOWLEDGED_OPEN = "PROTECTION_UNACKNOWLEDGED_OPEN"
    ACTIVATION_ORDER_AMBIGUOUS_OPEN = "ACTIVATION_ORDER_AMBIGUOUS_OPEN"
    TERMINAL_OPEN = "TERMINAL_OPEN"


class TriggerKind(StrEnum):
    """Modeled trigger facts retained by the result."""

    STOP_OPEN = "STOP_OPEN"
    STOP_OPEN_GAP = "STOP_OPEN_GAP"
    TARGET_OPEN = "TARGET_OPEN"
    STOP_INTRABAR = "STOP_INTRABAR"
    TARGET_INTRABAR = "TARGET_INTRABAR"
    DUAL_TOUCH_STOP_FIRST = "DUAL_TOUCH_STOP_FIRST"
    ACTIVATION_ORDER_AMBIGUOUS = "ACTIVATION_ORDER_AMBIGUOUS"
    INVALID_GEOMETRY_RECOVERY = "INVALID_GEOMETRY_RECOVERY"


@dataclass(frozen=True, slots=True)
class Last5mBar:
    """One exact UTC-aligned 5m LAST OHLCV observation."""

    source_id: SourceId
    symbol: TradingSymbol
    epoch_id: str
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "open_time", require_utc(self.open_time, field="open_time"))
        object.__setattr__(self, "close_time", require_utc(self.close_time, field="close_time"))
        if not isinstance(self.source_id, SourceId) or not isinstance(self.symbol, TradingSymbol):
            raise TypeError("5m source and symbol must be typed identities")
        if not isinstance(self.epoch_id, str) or not self.epoch_id.strip():
            raise Last5mProtectiveExecutionError("epoch_id must be non-empty")
        if self.open_time.second or self.open_time.microsecond or self.open_time.minute % 5:
            raise Last5mProtectiveExecutionError("5m open_time must be UTC-grid aligned")
        if self.close_time - self.open_time != _BAR:
            raise Last5mProtectiveExecutionError("5m interval must be exactly five minutes")
        for name, value in (
            ("open", self.open),
            ("high", self.high),
            ("low", self.low),
            ("close", self.close),
            ("volume", self.volume),
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise Last5mProtectiveExecutionError(f"{name} must be a finite Decimal")
        if min(self.open, self.high, self.low, self.close) <= 0 or self.volume < 0:
            raise Last5mProtectiveExecutionError(
                "5m prices must be positive and volume non-negative"
            )
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise Last5mProtectiveExecutionError("5m OHLC geometry is invalid")


@dataclass(frozen=True, slots=True)
class Last5mExecutionBinding:
    """Exact finite source/symbol/epoch/range authority for one execution."""

    source_id: SourceId
    symbol: TradingSymbol
    epoch_id: str
    start: datetime
    end: datetime
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        start = require_utc(self.start, field="binding start")
        end = require_utc(self.end, field="binding end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        if not isinstance(self.source_id, SourceId) or not isinstance(self.symbol, TradingSymbol):
            raise TypeError("binding source and symbol must be typed identities")
        if not isinstance(self.epoch_id, str) or not self.epoch_id.strip():
            raise Last5mProtectiveExecutionError("binding epoch_id must be non-empty")
        if start >= end or start.second or start.microsecond or start.minute % 5:
            raise Last5mProtectiveExecutionError("binding must be a positive aligned finite range")
        if end.second or end.microsecond or end.minute % 5:
            raise Last5mProtectiveExecutionError("binding end must be 5m-grid aligned")
        payload = dumps(
            {
                "end": end.isoformat(),
                "epoch_id": self.epoch_id,
                "source_id": str(self.source_id),
                "start": start.isoformat(),
                "symbol": str(self.symbol),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        object.__setattr__(self, "identity", sha256(payload).hexdigest())


@dataclass(frozen=True, slots=True)
class Last5mProtectionConfiguration:
    """Opt-in execution costs and modeled protective acceptance latency."""

    costs: SimulatedExecutionCostConfiguration
    exit_policy: ExitPolicy
    acknowledgement: ProtectionAcknowledgement = ProtectionAcknowledgement.ACCEPTED
    activation_latency: timedelta = timedelta(0)
    trigger_basis: TriggerBasis = TriggerBasis.LAST

    def __post_init__(self) -> None:
        if not isinstance(self.costs, SimulatedExecutionCostConfiguration):
            raise TypeError("costs must use the shared simulated cost authority")
        self.costs.validate()
        if self.exit_policy is not ExitPolicy.FIXED_2R:
            raise Last5mProtectiveExecutionError("an explicit supported exit policy is required")
        if not isinstance(self.acknowledgement, ProtectionAcknowledgement):
            raise TypeError("acknowledgement is invalid")
        if not isinstance(
            self.activation_latency, timedelta
        ) or self.activation_latency < timedelta(0):
            raise Last5mProtectiveExecutionError("activation latency must be non-negative")
        if self.trigger_basis is not TriggerBasis.LAST:
            raise Last5mProtectiveExecutionError("LAST_5M profile rejects non-LAST triggers")


@dataclass(frozen=True, slots=True)
class CompactFill:
    """Small retained fill record backed by shared pricing/accounting."""

    fill_id: str
    side: SimulatedExecutionSide
    fill_at: datetime
    reference_price: Decimal
    fill_price: Decimal
    quantity: Decimal
    fee_quote: Decimal


@dataclass(frozen=True, slots=True)
class Last5mIntentAudit:
    """Compact immutable authorization facts needed to interpret one result."""

    decision_identity: DecisionIdentity
    strategy_id: str
    strategy_version: str
    side: EntrySide
    quantity: Decimal
    approved_risk_quote: Decimal
    original_stop_basis: Decimal
    reference_price: Decimal


@dataclass(frozen=True, slots=True)
class Last5mTriggerAudit:
    """Interval-level trigger and ambiguity facts without invented tick ordering."""

    kind: TriggerKind
    interval_open: datetime
    interval_close: datetime
    stop_touched: bool
    target_touched: bool
    activated_inside_interval: bool = False
    gap_through_stop: bool = False
    same_bar_stop_precedence: bool = False
    alternate_trigger: TriggerKind | None = None


@dataclass(frozen=True, slots=True)
class Last5mProtectiveExecutionResult:
    """Compact research result retaining clocks, uncertainty, fills, and economics."""

    profile_id: str
    profile_version: str
    decision_identity: DecisionIdentity
    intent_audit: Last5mIntentAudit
    binding: Last5mExecutionBinding
    series_identity: str
    configuration: Last5mProtectionConfiguration
    exit_policy: ExitPolicy
    status: Last5mExecutionStatus
    authorization_at: datetime
    entry_eligible_at: datetime
    entry_grid_wait: timedelta | None
    entry_fill: CompactFill | None
    protection_requested_at: datetime | None
    protection_active_at: datetime | None
    trigger: Last5mTriggerAudit | None
    exit_fill: CompactFill | None
    accounting: PositionAccountingState
    approved_risk_quote: Decimal
    original_stop_basis: Decimal
    risk_distance: Decimal | None
    actual_fill_risk_quote: Decimal | None
    target_price: Decimal | None
    realized_gross_pnl: Decimal
    total_fees: Decimal
    realized_net_pnl: Decimal
    closed_net_pnl_quote: Decimal | None
    open_realized_fees_quote: Decimal | None
    closed_net_approved_risk_r: Decimal | None
    open_fee_burden_approved_risk_r: Decimal | None
    unresolved_quantity: Decimal
    holding_interval_start: datetime | None
    holding_interval_end: datetime | None
    holding_duration_lower: timedelta | None
    holding_duration_upper: timedelta | None
    holding_time_exact: bool
    events: tuple[str, ...]

    @property
    def position_closed(self) -> bool:
        return self.accounting.quantity == 0 and self.entry_fill is not None


class Last5mParquetSource:
    """Locked reader for a small identity-bound research 5m parquet artifact."""

    def __init__(self, path: Path, *, expected_sha256: str) -> None:
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            raise Last5mProtectiveExecutionError("expected parquet hash must be canonical SHA-256")
        self._path = path
        self._expected_sha256 = expected_sha256

    def read(self, binding: Last5mExecutionBinding) -> tuple[Last5mBar, ...]:
        body = self._path.read_bytes()
        if sha256(body).hexdigest() != self._expected_sha256:
            raise Last5mProtectiveExecutionError("5m parquet hash differs from its binding")
        table = pq.read_table(pa.BufferReader(body))
        expected_schema = pa.schema(
            [pa.field("symbol", pa.string()), pa.field("timeframe", pa.string())]
            + [
                pa.field(
                    name,
                    pa.int64() if name.endswith("_ms") or name == "trade_count" else pa.string(),
                )
                for name in CORPUS_FIELDS
            ]
        )
        if not table.schema.equals(expected_schema, check_metadata=False):
            raise Last5mProtectiveExecutionError("5m parquet schema differs from canonical corpus")
        wire_symbol = f"{binding.symbol.base}{binding.symbol.quote}"
        bars: list[Last5mBar] = []
        for row in table.to_pylist():
            if row["symbol"] != wire_symbol:
                raise Last5mProtectiveExecutionError("5m parquet contains a foreign symbol")
            if row["timeframe"] != "5m":
                raise Last5mProtectiveExecutionError("5m parquet contains a foreign timeframe")
            open_at = _from_millis(row["open_time_ms"])
            if binding.start <= open_at < binding.end:
                bars.append(
                    Last5mBar(
                        binding.source_id,
                        binding.symbol,
                        binding.epoch_id,
                        open_at,
                        _from_millis(row["close_time_ms"]),
                        Decimal(row["open"]),
                        Decimal(row["high"]),
                        Decimal(row["low"]),
                        Decimal(row["close"]),
                        Decimal(row["base_volume"]),
                    )
                )
        _validate_series(binding, tuple(bars))
        return tuple(bars)


def _from_millis(value: int) -> datetime:
    if not isinstance(value, int):
        raise Last5mProtectiveExecutionError("canonical candle timestamp must be int64")
    seconds, milliseconds = divmod(value, 1000)
    return datetime.fromtimestamp(seconds, tz=UTC) + timedelta(milliseconds=milliseconds)


def run_last_5m_protective_execution(
    *,
    intent: EntryOrderIntent,
    binding: Last5mExecutionBinding,
    bars: tuple[Last5mBar, ...],
    configuration: Last5mProtectionConfiguration,
) -> Last5mProtectiveExecutionResult:
    """Run one authorized decision through the explicit research-only 5m model."""

    if not isinstance(intent, EntryOrderIntent) or not intent.authorized_for_execution:
        raise TypeError("an authorized EntryOrderIntent is required")
    if not isinstance(binding, Last5mExecutionBinding):
        raise TypeError("binding must be a Last5mExecutionBinding")
    if not isinstance(configuration, Last5mProtectionConfiguration):
        raise TypeError("configuration must be a Last5mProtectionConfiguration")
    configuration.__post_init__()
    if intent.symbol != binding.symbol or intent.source_id != binding.source_id:
        raise Last5mProtectiveExecutionError("intent source/symbol differs from 5m binding")
    if not binding.start <= intent.authorized_at < binding.end:
        raise Last5mProtectiveExecutionError("authorization is outside the finite 5m binding")
    _validate_series(binding, bars)
    series_identity = _series_identity(binding, bars)

    eligible_at = intent.authorized_at + configuration.costs.latency
    entry_index = next((i for i, bar in enumerate(bars) if bar.open_time >= eligible_at), None)
    if entry_index is None:
        return _result(
            intent,
            binding,
            series_identity,
            configuration,
            Last5mExecutionStatus.ENTRY_UNFILLED,
            eligible_at,
            None,
            None,
            None,
            None,
            None,
            PositionAccountingState(),
            None,
            None,
            ("ENTRY_ELIGIBLE_NO_LATER_5M_OPEN",),
        )

    entry_bar = bars[entry_index]
    entry_side = _execution_side(intent.side)
    entry = _compact_fill(
        fill_id=_fill_id(intent, "entry"),
        side=entry_side,
        quantity=intent.quantity,
        fill_at=entry_bar.open_time,
        reference_price=entry_bar.open,
        costs=configuration.costs,
    )
    accounting = PositionAccountingCore()
    _account(accounting, intent.symbol, entry)
    grid_wait = entry.fill_at - eligible_at

    try:
        risk_distance, target = fixed_2r_geometry(intent.side, entry.fill_price, intent.stop_basis)
    except BreakoutRetestReplayError:
        recovery_index = next(
            (i for i in range(entry_index + 1, len(bars)) if bars[i].open_time > entry.fill_at),
            None,
        )
        if recovery_index is None:
            return _result(
                intent,
                binding,
                series_identity,
                configuration,
                Last5mExecutionStatus.INVALID_GEOMETRY_UNRESOLVED,
                eligible_at,
                grid_wait,
                entry,
                None,
                None,
                None,
                accounting.position(intent.symbol),
                None,
                None,
                ("ENTRY_FILLED", "INVALID_2R_GEOMETRY", "RECOVERY_UNFILLED"),
            )
        recovery_bar = bars[recovery_index]
        recovery = _compact_fill(
            fill_id=_fill_id(intent, "recovery"),
            side=_opposite(entry_side),
            quantity=intent.quantity,
            fill_at=recovery_bar.open_time,
            reference_price=recovery_bar.open,
            costs=configuration.costs,
        )
        _account(accounting, intent.symbol, recovery)
        trigger = Last5mTriggerAudit(
            TriggerKind.INVALID_GEOMETRY_RECOVERY,
            recovery_bar.open_time,
            recovery_bar.close_time,
            False,
            False,
        )
        return _result(
            intent,
            binding,
            series_identity,
            configuration,
            Last5mExecutionStatus.INVALID_GEOMETRY_RECOVERED,
            eligible_at,
            grid_wait,
            entry,
            None,
            trigger,
            recovery,
            accounting.position(intent.symbol),
            None,
            None,
            ("ENTRY_FILLED", "INVALID_2R_GEOMETRY", "RECOVERY_FILLED"),
        )

    requested_at = entry.fill_at
    if configuration.acknowledgement is not ProtectionAcknowledgement.ACCEPTED:
        status = (
            Last5mExecutionStatus.PROTECTION_REJECTED_OPEN
            if configuration.acknowledgement is ProtectionAcknowledgement.REJECTED
            else Last5mExecutionStatus.PROTECTION_UNACKNOWLEDGED_OPEN
        )
        return _result(
            intent,
            binding,
            series_identity,
            configuration,
            status,
            eligible_at,
            grid_wait,
            entry,
            requested_at,
            None,
            None,
            accounting.position(intent.symbol),
            risk_distance,
            target,
            ("ENTRY_FILLED", f"PROTECTION_{configuration.acknowledgement.value}"),
        )

    active_at = requested_at + configuration.activation_latency
    for bar in bars[entry_index:]:
        if active_at >= bar.close_time:
            continue
        inside = bar.open_time < active_at < bar.close_time
        stop_touch, target_touch = _touches(intent.side, bar, intent.stop_basis, target)
        if inside:
            if stop_touch or target_touch:
                trigger = Last5mTriggerAudit(
                    TriggerKind.ACTIVATION_ORDER_AMBIGUOUS,
                    bar.open_time,
                    bar.close_time,
                    stop_touch,
                    target_touch,
                    activated_inside_interval=True,
                )
                return _result(
                    intent,
                    binding,
                    series_identity,
                    configuration,
                    Last5mExecutionStatus.ACTIVATION_ORDER_AMBIGUOUS_OPEN,
                    eligible_at,
                    grid_wait,
                    entry,
                    requested_at,
                    trigger,
                    None,
                    accounting.position(intent.symbol),
                    risk_distance,
                    target,
                    ("ENTRY_FILLED", "PROTECTION_ACCEPTED", "INTRABAR_ORDER_UNRESOLVED"),
                    protection_active_at=active_at,
                )
            continue

        open_kind, open_reference = _open_trigger(intent.side, bar.open, intent.stop_basis, target)
        if open_kind is not None:
            is_gap = open_kind is TriggerKind.STOP_OPEN_GAP
            trigger = Last5mTriggerAudit(
                open_kind,
                bar.open_time,
                bar.close_time,
                open_kind in {TriggerKind.STOP_OPEN, TriggerKind.STOP_OPEN_GAP},
                open_kind is TriggerKind.TARGET_OPEN,
                gap_through_stop=is_gap,
            )
            return _closed_result(
                intent,
                binding,
                series_identity,
                configuration,
                accounting,
                eligible_at,
                grid_wait,
                entry,
                requested_at,
                active_at,
                risk_distance,
                target,
                trigger,
                bar.open_time,
                open_reference,
            )
        if not stop_touch and not target_touch:
            continue
        if stop_touch:
            kind = TriggerKind.DUAL_TOUCH_STOP_FIRST if target_touch else TriggerKind.STOP_INTRABAR
            reference = intent.stop_basis
        else:
            kind = TriggerKind.TARGET_INTRABAR
            reference = target
        trigger = Last5mTriggerAudit(
            kind,
            bar.open_time,
            bar.close_time,
            stop_touch,
            target_touch,
            same_bar_stop_precedence=stop_touch and target_touch,
            alternate_trigger=TriggerKind.TARGET_INTRABAR if stop_touch and target_touch else None,
        )
        return _closed_result(
            intent,
            binding,
            series_identity,
            configuration,
            accounting,
            eligible_at,
            grid_wait,
            entry,
            requested_at,
            active_at,
            risk_distance,
            target,
            trigger,
            bar.close_time,
            reference,
        )

    return _result(
        intent,
        binding,
        series_identity,
        configuration,
        Last5mExecutionStatus.TERMINAL_OPEN,
        eligible_at,
        grid_wait,
        entry,
        requested_at,
        None,
        None,
        accounting.position(intent.symbol),
        risk_distance,
        target,
        ("ENTRY_FILLED", "PROTECTION_ACCEPTED", "FOLLOW_UP_ENDED_OPEN"),
        protection_active_at=active_at,
    )


def _validate_series(binding: Last5mExecutionBinding, bars: tuple[Last5mBar, ...]) -> None:
    if not isinstance(bars, tuple) or not bars:
        raise Last5mProtectiveExecutionError("5m series must be a non-empty immutable tuple")
    expected = binding.start
    for bar in bars:
        if not isinstance(bar, Last5mBar):
            raise TypeError("5m series contains an invalid value")
        if (
            bar.source_id != binding.source_id
            or bar.symbol != binding.symbol
            or bar.epoch_id != binding.epoch_id
        ):
            raise Last5mProtectiveExecutionError("5m row identity differs from binding")
        if bar.open_time != expected:
            raise Last5mProtectiveExecutionError(
                "5m series is missing, duplicated, or out of order"
            )
        expected = bar.close_time
    if expected != binding.end:
        raise Last5mProtectiveExecutionError("5m series does not exactly cover its finite binding")


def _series_identity(binding: Last5mExecutionBinding, bars: tuple[Last5mBar, ...]) -> str:
    payload = dumps(
        {
            "binding": binding.identity,
            "rows": [
                [
                    str(bar.source_id),
                    str(bar.symbol),
                    bar.epoch_id,
                    bar.open_time.isoformat(),
                    bar.close_time.isoformat(),
                    str(bar.open),
                    str(bar.high),
                    str(bar.low),
                    str(bar.close),
                    str(bar.volume),
                ]
                for bar in bars
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(payload).hexdigest()


def _execution_side(side: EntrySide) -> SimulatedExecutionSide:
    if side is EntrySide.BUY:
        return SimulatedExecutionSide.BUY
    if side is EntrySide.SELL:
        return SimulatedExecutionSide.SELL
    raise Last5mProtectiveExecutionError("entry side is invalid")


def _opposite(side: SimulatedExecutionSide) -> SimulatedExecutionSide:
    return (
        SimulatedExecutionSide.SELL
        if side is SimulatedExecutionSide.BUY
        else SimulatedExecutionSide.BUY
    )


def _decision_key(identity: DecisionIdentity) -> str:
    payload = dumps(
        {
            "observed_at": identity.observed_at.isoformat(),
            "source_id": identity.source_id.value,
            "strategy_id": identity.strategy_id,
            "strategy_version": identity.strategy_version,
            "symbol": str(identity.symbol),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(payload).hexdigest()


def _fill_id(intent: EntryOrderIntent, purpose: str) -> str:
    if purpose not in {"entry", "protective-exit", "recovery"}:
        raise Last5mProtectiveExecutionError("fill purpose is invalid")
    return f"{PROFILE_ID}:{_decision_key(intent.decision_identity)}:{purpose}"


def _compact_fill(
    *,
    fill_id: str,
    side: SimulatedExecutionSide,
    quantity: Decimal,
    fill_at: datetime,
    reference_price: Decimal,
    costs: SimulatedExecutionCostConfiguration,
) -> CompactFill:
    quote = price_simulated_market_fill(
        side=side, quantity=quantity, reference_price=reference_price, costs=costs
    )
    return CompactFill(
        fill_id,
        side,
        fill_at,
        reference_price,
        quote.fill_price,
        quantity,
        quote.fee_quote,
    )


def _account(core: PositionAccountingCore, symbol: TradingSymbol, fill: CompactFill) -> None:
    accounting_fill = PositionAccountingFill(
        fill.fill_id,
        symbol,
        fill.side,
        fill.quantity,
        fill.fill_at,
        fill.fill_price,
        fill.fee_quote,
    )
    core.process(PositionAccountingFillBatch(fill.fill_at, (accounting_fill,)))


def _touches(side: EntrySide, bar: Last5mBar, stop: Decimal, target: Decimal) -> tuple[bool, bool]:
    if side is EntrySide.BUY:
        return bar.low <= stop, bar.high >= target
    if side is EntrySide.SELL:
        return bar.high >= stop, bar.low <= target
    raise Last5mProtectiveExecutionError("entry side is invalid")


def _open_trigger(
    side: EntrySide, open_price: Decimal, stop: Decimal, target: Decimal
) -> tuple[TriggerKind | None, Decimal]:
    if side is EntrySide.BUY:
        if open_price <= stop:
            return (
                TriggerKind.STOP_OPEN if open_price == stop else TriggerKind.STOP_OPEN_GAP,
                open_price,
            )
        if open_price >= target:
            return TriggerKind.TARGET_OPEN, open_price
    elif side is EntrySide.SELL:
        if open_price >= stop:
            return (
                TriggerKind.STOP_OPEN if open_price == stop else TriggerKind.STOP_OPEN_GAP,
                open_price,
            )
        if open_price <= target:
            return TriggerKind.TARGET_OPEN, open_price
    else:
        raise Last5mProtectiveExecutionError("entry side is invalid")
    return None, open_price


def _closed_result(
    intent: EntryOrderIntent,
    binding: Last5mExecutionBinding,
    series_identity: str,
    configuration: Last5mProtectionConfiguration,
    accounting: PositionAccountingCore,
    eligible_at: datetime,
    grid_wait: timedelta,
    entry: CompactFill,
    requested_at: datetime,
    active_at: datetime,
    risk_distance: Decimal,
    target: Decimal,
    trigger: Last5mTriggerAudit,
    fill_at: datetime,
    reference: Decimal,
) -> Last5mProtectiveExecutionResult:
    exit_fill = _compact_fill(
        fill_id=_fill_id(intent, "protective-exit"),
        side=_opposite(entry.side),
        quantity=intent.quantity,
        fill_at=fill_at,
        reference_price=reference,
        costs=configuration.costs,
    )
    _account(accounting, intent.symbol, exit_fill)
    return _result(
        intent,
        binding,
        series_identity,
        configuration,
        Last5mExecutionStatus.ORDINARY_CLOSED,
        eligible_at,
        grid_wait,
        entry,
        requested_at,
        trigger,
        exit_fill,
        accounting.position(intent.symbol),
        risk_distance,
        target,
        ("ENTRY_FILLED", "PROTECTION_ACCEPTED", trigger.kind.value, "POSITION_CLOSED"),
        protection_active_at=active_at,
    )


def _result(
    intent: EntryOrderIntent,
    binding: Last5mExecutionBinding,
    series_identity: str,
    configuration: Last5mProtectionConfiguration,
    status: Last5mExecutionStatus,
    eligible_at: datetime,
    grid_wait: timedelta | None,
    entry: CompactFill | None,
    requested_at: datetime | None,
    trigger: Last5mTriggerAudit | None,
    exit_fill: CompactFill | None,
    state: PositionAccountingState,
    risk_distance: Decimal | None,
    target: Decimal | None,
    events: tuple[str, ...],
    *,
    protection_active_at: datetime | None = None,
) -> Last5mProtectiveExecutionResult:
    approved_risk = intent.approved_risk_quote
    if not approved_risk.is_finite() or approved_risk <= 0:
        raise Last5mProtectiveExecutionError("authorized approved risk must be positive")
    closed = entry is not None and state.quantity == 0
    closed_net_r: Decimal | None = None
    open_fee_burden_r: Decimal | None = None
    actual_fill_risk: Decimal | None = None
    with localcontext(_DECIMAL_CONTEXT):
        if closed:
            closed_net_r = state.cumulative_net_realized_pnl / approved_risk
        elif entry is not None:
            open_fee_burden_r = state.cumulative_fees / approved_risk
        if entry is not None and risk_distance is not None:
            actual_fill_risk = risk_distance * entry.quantity
    holding_start = entry.fill_at if entry is not None else None
    holding_end = exit_fill.fill_at if exit_fill is not None else None
    exact = bool(
        trigger is not None
        and trigger.kind
        in {
            TriggerKind.STOP_OPEN_GAP,
            TriggerKind.STOP_OPEN,
            TriggerKind.TARGET_OPEN,
            TriggerKind.INVALID_GEOMETRY_RECOVERY,
        }
    )
    duration_lower: timedelta | None = None
    duration_upper: timedelta | None = None
    if entry is not None and exit_fill is not None and trigger is not None:
        if exact:
            duration_lower = duration_upper = exit_fill.fill_at - entry.fill_at
        else:
            duration_lower = max(trigger.interval_open - entry.fill_at, timedelta(0))
            duration_upper = trigger.interval_close - entry.fill_at
    return Last5mProtectiveExecutionResult(
        PROFILE_ID,
        PROFILE_VERSION,
        intent.decision_identity,
        Last5mIntentAudit(
            intent.decision_identity,
            intent.strategy_id,
            intent.strategy_version,
            intent.side,
            intent.quantity,
            intent.approved_risk_quote,
            intent.stop_basis,
            intent.reference_price,
        ),
        binding,
        series_identity,
        configuration,
        configuration.exit_policy,
        status,
        intent.authorized_at,
        eligible_at,
        grid_wait,
        entry,
        requested_at,
        protection_active_at,
        trigger,
        exit_fill,
        state,
        approved_risk,
        intent.stop_basis,
        risk_distance,
        actual_fill_risk,
        target,
        state.cumulative_gross_realized_pnl,
        state.cumulative_fees,
        state.cumulative_net_realized_pnl,
        state.cumulative_net_realized_pnl if closed else None,
        state.cumulative_fees if entry is not None and not closed else None,
        closed_net_r,
        open_fee_burden_r,
        state.quantity.copy_abs(),
        holding_start,
        holding_end,
        duration_lower,
        duration_upper,
        exact,
        events,
    )
