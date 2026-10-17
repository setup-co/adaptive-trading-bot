"""Versioned, non-executable JSON codec for offline study authorities.

Only already-loaded project-owned dataclasses and enums may be reconstructed.
Artifact type names never cause an import, class lookup outside this package,
or arbitrary object deserialization. Constructor and creation seals are checked.
"""

from __future__ import annotations

import sys
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from adaptive_trading_bot.research.trend_pullback_corpus import _canonical_integrity_value

EVIDENCE_SCHEMA = "research-v2-screen-authority-evidence-v1"
_SCHEMA_MODULES = frozenset(
    {
        "adaptive_trading_bot.backtest.accounting",
        "adaptive_trading_bot.backtest.execution",
        "adaptive_trading_bot.domain.market_data",
        "adaptive_trading_bot.execution.simulation",
        "adaptive_trading_bot.features.core",
        "adaptive_trading_bot.features.market_structure",
        "adaptive_trading_bot.journal.decision",
        "adaptive_trading_bot.orders.intent",
        "adaptive_trading_bot.regime.composition",
        "adaptive_trading_bot.regime.technical",
        "adaptive_trading_bot.research.all_fill_replay",
        "adaptive_trading_bot.research.breakout_retest_replay",
        "adaptive_trading_bot.research.paired_exit_replay",
        "adaptive_trading_bot.research.range_mean_reversion_replay",
        "adaptive_trading_bot.research.research_v2_admission",
        "adaptive_trading_bot.research.research_v2_factorial",
        "adaptive_trading_bot.research.trend_pullback_corpus",
        "adaptive_trading_bot.risk.core",
        "adaptive_trading_bot.risk.safety",
        "adaptive_trading_bot.storage.historical_candles",
        "adaptive_trading_bot.strategies.breakout_retest",
        "adaptive_trading_bot.strategies.core",
        "adaptive_trading_bot.strategies.exit_core",
        "adaptive_trading_bot.strategies.range_context",
        "adaptive_trading_bot.strategies.range_mean_reversion",
        "adaptive_trading_bot.strategies.trend_pullback",
        "adaptive_trading_bot.strategies.trend_pullback_exit",
    }
)


class ScreenEvidenceError(ValueError):
    """Stored authority cannot be reconstructed or validated."""


def encode(value: object, *, exclude_seal: bool = False) -> object:
    return _canonical_integrity_value(
        value, excluded_names=frozenset({"integrity_seal"}) if exclude_seal else frozenset()
    )


def _trusted_type(path: str, *, enum: bool) -> type[Any]:
    if ".<" in path:
        raise ScreenEvidenceError("authority type is outside the fixed project namespace")
    module_name, _, class_name = path.rpartition(".")
    if module_name not in _SCHEMA_MODULES:
        raise ScreenEvidenceError("authority module is outside the versioned schema")
    module = sys.modules.get(module_name)
    if module is None:
        raise ScreenEvidenceError("authority type is not in the loaded project schema")
    candidate = vars(module).get(class_name)
    if (
        not isinstance(candidate, type)
        or candidate.__module__ != module_name
        or candidate.__qualname__ != class_name
        or (not issubclass(candidate, Enum) if enum else not is_dataclass(candidate))
    ):
        raise ScreenEvidenceError("authority type is not a declared project value")
    return candidate


def decode(
    raw: object, *, decision: object | None = None, candles: tuple[object, ...] = ()
) -> object:
    """Decode only tagged canonical values; recheck every dataclass's stored fields."""

    if not isinstance(raw, list) or not raw or not isinstance(raw[0], str):
        raise ScreenEvidenceError("authority value is not canonical tagged JSON")
    tag = raw[0]
    try:
        if tag == "decision_ref" and len(raw) == 1 and decision is not None:
            return decision
        if tag == "candle_slice" and len(raw) == 3:
            start, end = raw[1:]
            if (
                type(start) is not int
                or type(end) is not int
                or not 0 <= start <= end <= len(candles)
            ):
                raise ScreenEvidenceError("execution candle slice is outside stored context")
            return candles[start:end]
        if tag == "none" and len(raw) == 1:
            return None
        if tag == "bool" and len(raw) == 2 and type(raw[1]) is bool:
            return raw[1]
        if tag == "int" and len(raw) == 2 and type(raw[1]) is int:
            return raw[1]
        if tag == "str" and len(raw) == 2 and type(raw[1]) is str:
            return raw[1]
        if tag == "decimal" and len(raw) == 4:
            sign, digits, exponent = raw[1:]
            if (
                type(sign) is not int
                or not isinstance(digits, list)
                or not isinstance(exponent, str)
            ):
                raise ScreenEvidenceError("invalid exact decimal evidence")
            return Decimal((sign, tuple(digits), int(exponent)))
        if tag == "datetime" and len(raw) == 3:
            value = datetime.fromisoformat(raw[1]).replace(fold=raw[2])
            return value
        if tag == "timedelta" and len(raw) == 4:
            return timedelta(days=raw[1], seconds=raw[2], microseconds=raw[3])
        if tag == "tuple" and len(raw) == 2 and isinstance(raw[1], list):
            return tuple(decode(item, decision=decision, candles=candles) for item in raw[1])
        if tag == "enum" and len(raw) == 3 and isinstance(raw[1], str):
            return _trusted_type(raw[1], enum=True)(
                decode(raw[2], decision=decision, candles=candles)
            )
        if tag == "dataclass" and len(raw) == 3 and isinstance(raw[1], str):
            cls = _trusted_type(raw[1], enum=False)
            if not isinstance(raw[2], list):
                raise ScreenEvidenceError("dataclass fields are malformed")
            names = tuple(item.name for item in fields(cls))
            if tuple(pair[0] for pair in raw[2]) != names:
                raise ScreenEvidenceError("dataclass field schema differs from code")
            values = {
                name: decode(value, decision=decision, candles=candles) for name, value in raw[2]
            }
            created = cls(**{item.name: values[item.name] for item in fields(cls) if item.init})
            if any(getattr(created, name) != values[name] for name in names):
                raise ScreenEvidenceError("creation-time authority field or seal changed")
            return created
    except ScreenEvidenceError:
        raise
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        raise ScreenEvidenceError("authority evidence failed closed") from exc
    raise ScreenEvidenceError("unknown authority evidence tag or shape")
