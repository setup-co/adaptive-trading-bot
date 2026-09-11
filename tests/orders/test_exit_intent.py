import runpy
from dataclasses import fields, replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from adaptive_trading_bot.orders import (
    ExitOrderIntent,
    ExitSide,
    exit_order_intent_fingerprint,
    exit_order_intent_is_authoritative,
)
from adaptive_trading_bot.paper import PaperExecutionObservation

_HELPERS = runpy.run_path(str(Path(__file__).parents[1] / "paper" / "protective_exit_helpers.py"))
BTC = _HELPERS["BTC"]
NOW = _HELPERS["NOW"]
SOURCE = _HELPERS["SOURCE"]
candidate = _HELPERS["candidate"]
coordinator = _HELPERS["coordinator"]


def authorized_exit() -> ExitOrderIntent:
    paper = coordinator()
    paper.start_batch((candidate(),))
    paper.observe_batch((PaperExecutionObservation(SOURCE, BTC, NOW, Decimal(100)),))
    result = paper.evaluate_protective_stops(
        (PaperExecutionObservation(SOURCE, BTC, NOW + timedelta(minutes=1), Decimal(75)),)
    )
    return cast(ExitOrderIntent, result.triggered[0])


def test_public_constructor_cannot_mint_executable_exit_authority() -> None:
    with pytest.raises(TypeError, match="only be created by its authorizer"):
        ExitOrderIntent()  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="only be created by its authorizer"):
        ExitOrderIntent(quantity=Decimal(999))  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("field_name", "value"),
    (("quantity", Decimal(999)), ("side", ExitSide.BUY), ("stop_basis", Decimal(1))),
)
def test_replace_cannot_change_authorized_exit(field_name: str, value: object) -> None:
    intent = authorized_exit()

    with pytest.raises((TypeError, ValueError)):
        replace(intent, **{field_name: value})  # type: ignore[arg-type]

    assert exit_order_intent_is_authoritative(intent)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("quantity", Decimal(999)),
        ("side", ExitSide.BUY),
        ("stop_basis", Decimal(1)),
        ("trigger_reference_price", Decimal(1)),
        ("risk_policy_version", "forged"),
        ("authorized_for_execution", False),
    ),
)
def test_raw_field_mutation_invalidates_authority(field_name: str, value: object) -> None:
    intent = authorized_exit()
    before = exit_order_intent_fingerprint(intent)

    object.__setattr__(intent, field_name, value)

    assert not exit_order_intent_is_authoritative(intent)
    assert exit_order_intent_fingerprint(intent) != before


def test_exit_contract_has_no_fill_or_execution_pricing_fields() -> None:
    names = {item.name for item in fields(ExitOrderIntent)}

    assert not names.intersection(
        {"fill", "fill_price", "fee_quote", "spread_bps", "slippage_bps", "eligible_at"}
    )
