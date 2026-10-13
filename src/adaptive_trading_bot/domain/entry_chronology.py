"""Versioned causal entry timing shared by research and exit-context boundaries."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from adaptive_trading_bot.domain.clock import require_utc

CAUSAL_ENTRY_CHRONOLOGY_V2: Final = "CAUSAL_ENTRY_CHRONOLOGY_V2"


def entry_source_start(
    *, decision_at: datetime, authorized_at: datetime, requested_start: datetime, end: datetime
) -> datetime:
    """Keep explicit bounds; let the simulator apply latency to the real request time."""

    decision_at = require_utc(decision_at, field="entry decision_at")
    authorized_at = require_utc(authorized_at, field="entry authorized_at")
    requested_start = require_utc(requested_start, field="entry requested_start")
    end = require_utc(end, field="entry end")
    if decision_at > authorized_at or requested_start >= end:
        raise ValueError("entry authorization or requested range chronology is invalid")
    start = max(requested_start, authorized_at)
    if start >= end:
        raise ValueError("bounded replay has no authorized entry interval")
    return start


def validate_entry_fill_chronology(
    *,
    decision_at: datetime,
    authorized_at: datetime,
    latency: timedelta,
    eligible_at: datetime,
    fill_at: datetime,
) -> None:
    """Validate one actual simulator fill without manufacturing event timestamps."""

    decision_at = require_utc(decision_at, field="entry decision_at")
    authorized_at = require_utc(authorized_at, field="entry authorized_at")
    eligible_at = require_utc(eligible_at, field="entry eligible_at")
    fill_at = require_utc(fill_at, field="entry fill_at")
    if (
        not isinstance(latency, timedelta)
        or latency < timedelta()
        or not decision_at <= authorized_at
        or eligible_at != authorized_at + latency
        or not eligible_at <= fill_at
    ):
        raise ValueError("entry fill chronology is invalid")
