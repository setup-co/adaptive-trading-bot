"""Deterministic realized-only account arithmetic for Paper trading."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from adaptive_trading_bot.domain.clock import require_utc

_DECIMAL_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_ZERO = Decimal(0)


class PaperAccountError(ValueError):
    """Paper account configuration or arithmetic failed closed."""


@dataclass(frozen=True, slots=True)
class PaperAccountConfiguration:
    """Versioned starting facts for one process-local Paper account."""

    initial_equity_quote: Decimal = Decimal(10000)
    quote_currency: str = "USDT"
    account_policy_version: str = field(init=False, default="1.0.0")

    def __post_init__(self) -> None:
        if not isinstance(self.initial_equity_quote, Decimal):
            raise TypeError("initial_equity_quote must be a Decimal")
        if not self.initial_equity_quote.is_finite() or self.initial_equity_quote <= 0:
            raise PaperAccountError("initial_equity_quote must be finite and positive")
        if self.quote_currency != "USDT":
            raise PaperAccountError("V1 Paper account quote_currency must be USDT")


@dataclass(frozen=True, slots=True)
class PaperAccountSnapshot:
    """Detached realized-only Paper account state at an explicit UTC instant."""

    observed_at: datetime
    quote_currency: str
    initial_equity_quote: Decimal
    account_equity_quote: Decimal
    day_start_equity_quote: Decimal
    peak_equity_quote: Decimal
    realized_gross_pnl_quote: Decimal
    cumulative_entry_fees_quote: Decimal
    cumulative_exit_fees_quote: Decimal
    cumulative_fees_quote: Decimal
    cumulative_net_pnl_quote: Decimal
    utc_day: date
    account_policy_version: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "observed_at", require_utc(self.observed_at, field="account observed_at")
        )
        _validate_snapshot(self)


@dataclass(frozen=True, slots=True)
class _PaperAccountState:
    configuration: PaperAccountConfiguration
    account_equity_quote: Decimal
    day_start_equity_quote: Decimal
    peak_equity_quote: Decimal
    realized_gross_pnl_quote: Decimal
    cumulative_entry_fees_quote: Decimal
    cumulative_exit_fees_quote: Decimal
    utc_day: date | None


def _new_account_state(configuration: PaperAccountConfiguration) -> _PaperAccountState:
    if not isinstance(configuration, PaperAccountConfiguration):
        raise TypeError("configuration must be a PaperAccountConfiguration")
    owned = PaperAccountConfiguration(
        configuration.initial_equity_quote,
        configuration.quote_currency,
    )
    return _PaperAccountState(
        owned,
        owned.initial_equity_quote,
        owned.initial_equity_quote,
        owned.initial_equity_quote,
        _ZERO,
        _ZERO,
        _ZERO,
        None,
    )


def _project_account(
    state: _PaperAccountState,
    observed_at: datetime,
) -> tuple[_PaperAccountState, PaperAccountSnapshot]:
    """Project UTC-day state without mutating the coordinator-owned value."""

    instant = require_utc(observed_at, field="account observed_at")
    projected = state
    if state.utc_day is None or instant.date() > state.utc_day:
        projected = _PaperAccountState(
            state.configuration,
            state.account_equity_quote,
            state.account_equity_quote,
            state.peak_equity_quote,
            state.realized_gross_pnl_quote,
            state.cumulative_entry_fees_quote,
            state.cumulative_exit_fees_quote,
            instant.date(),
        )
    elif instant.date() < state.utc_day:
        raise PaperAccountError("account observed_at cannot move to an earlier UTC day")
    return projected, _snapshot(projected, instant)


def _advance_account(
    state: _PaperAccountState,
    observed_at: datetime,
    *,
    entry_fees_quote: Decimal = _ZERO,
    exit_fees_quote: Decimal = _ZERO,
    gross_realized_pnl_quote: Decimal = _ZERO,
) -> tuple[_PaperAccountState, PaperAccountSnapshot]:
    """Stage one event's exact realized account effects under the local context."""

    for name, value in (
        ("entry_fees_quote", entry_fees_quote),
        ("exit_fees_quote", exit_fees_quote),
        ("gross_realized_pnl_quote", gross_realized_pnl_quote),
    ):
        if not isinstance(value, Decimal):
            raise TypeError(f"{name} must be a Decimal")
        if not value.is_finite():
            raise PaperAccountError(f"{name} must be finite")
    if entry_fees_quote < 0 or exit_fees_quote < 0:
        raise PaperAccountError("Paper account fees must be non-negative")

    projected, _ = _project_account(state, observed_at)
    with localcontext(_DECIMAL_CONTEXT):
        next_entry_fees = projected.cumulative_entry_fees_quote + entry_fees_quote
        next_exit_fees = projected.cumulative_exit_fees_quote + exit_fees_quote
        next_gross = projected.realized_gross_pnl_quote + gross_realized_pnl_quote
        next_fees = next_entry_fees + next_exit_fees
        next_net = next_gross - next_fees
        next_equity = projected.configuration.initial_equity_quote + next_net
        next_peak = max(projected.peak_equity_quote, next_equity)
    if not all(
        value.is_finite()
        for value in (
            next_entry_fees,
            next_exit_fees,
            next_gross,
            next_fees,
            next_net,
            next_equity,
            next_peak,
        )
    ):
        raise PaperAccountError("Paper account arithmetic must remain finite")
    advanced = _PaperAccountState(
        projected.configuration,
        next_equity,
        projected.day_start_equity_quote,
        next_peak,
        next_gross,
        next_entry_fees,
        next_exit_fees,
        projected.utc_day,
    )
    return advanced, _snapshot(advanced, observed_at)


def _snapshot(state: _PaperAccountState, observed_at: datetime) -> PaperAccountSnapshot:
    if state.utc_day is None:
        raise PaperAccountError("Paper account UTC day has not been projected")
    with localcontext(_DECIMAL_CONTEXT):
        fees = state.cumulative_entry_fees_quote + state.cumulative_exit_fees_quote
        net = state.realized_gross_pnl_quote - fees
    return PaperAccountSnapshot(
        observed_at,
        state.configuration.quote_currency,
        state.configuration.initial_equity_quote,
        state.account_equity_quote,
        state.day_start_equity_quote,
        state.peak_equity_quote,
        state.realized_gross_pnl_quote,
        state.cumulative_entry_fees_quote,
        state.cumulative_exit_fees_quote,
        fees,
        net,
        state.utc_day,
        state.configuration.account_policy_version,
    )


def _validate_snapshot(snapshot: PaperAccountSnapshot) -> None:
    if not isinstance(snapshot.quote_currency, str) or snapshot.quote_currency != "USDT":
        raise PaperAccountError("V1 Paper account quote_currency must be USDT")
    if not isinstance(snapshot.utc_day, date):
        raise TypeError("Paper account utc_day must be a date")
    if snapshot.utc_day != snapshot.observed_at.date():
        raise PaperAccountError("Paper account utc_day must match observed_at")
    if (
        not isinstance(snapshot.account_policy_version, str)
        or snapshot.account_policy_version != "1.0.0"
    ):
        raise PaperAccountError("unsupported Paper account policy version")
    values = (
        snapshot.initial_equity_quote,
        snapshot.account_equity_quote,
        snapshot.day_start_equity_quote,
        snapshot.peak_equity_quote,
        snapshot.realized_gross_pnl_quote,
        snapshot.cumulative_entry_fees_quote,
        snapshot.cumulative_exit_fees_quote,
        snapshot.cumulative_fees_quote,
        snapshot.cumulative_net_pnl_quote,
    )
    if not all(isinstance(value, Decimal) for value in values):
        raise TypeError("Paper account monetary fields must be Decimals")
    if not all(value.is_finite() for value in values):
        raise PaperAccountError("Paper account monetary fields must be finite")
    if snapshot.initial_equity_quote <= 0:
        raise PaperAccountError("Paper account initial equity must be positive")
    if snapshot.cumulative_entry_fees_quote < 0 or snapshot.cumulative_exit_fees_quote < 0:
        raise PaperAccountError("Paper account cumulative fees must be non-negative")
    with localcontext(_DECIMAL_CONTEXT):
        expected_fees = snapshot.cumulative_entry_fees_quote + snapshot.cumulative_exit_fees_quote
        expected_net = snapshot.realized_gross_pnl_quote - expected_fees
        expected_equity = snapshot.initial_equity_quote + expected_net
    if (
        snapshot.cumulative_fees_quote != expected_fees
        or snapshot.cumulative_net_pnl_quote != expected_net
        or snapshot.account_equity_quote != expected_equity
        or snapshot.peak_equity_quote < snapshot.account_equity_quote
        or snapshot.peak_equity_quote < snapshot.day_start_equity_quote
    ):
        raise PaperAccountError("Paper account arithmetic invariants are inconsistent")
