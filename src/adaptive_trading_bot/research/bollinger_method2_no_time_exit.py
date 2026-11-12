"""Paired hourly Bollinger Method-II exit diagnosis without a holding timer."""

from __future__ import annotations

import gzip
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Any

from adaptive_trading_bot.domain import FrozenClock, Timeframe
from adaptive_trading_bot.execution import SimulatedExecutionSide, price_simulated_market_fill
from adaptive_trading_bot.features import (
    FeatureEngine,
    MarketStructureEngine,
    observed_candles_from_replay,
)
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, OrderIntentAuthorizationStatus
from adaptive_trading_bot.regime import MultiTimeframeRegimeComposer, TechnicalRegimeEngine
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.research.bollinger_method2_screen import (
    ARMS,
    DECISION_CADENCE,
)
from adaptive_trading_bot.research.failed_breakout_reversion_screen import (
    DEFAULT_INPUT_AUTHORITY,
    FRAMES,
    RESEARCH_EQUITY,
    SIGNAL_END,
    SIGNAL_START,
    AcceptedInput,
    Scenario,
    ScreenError,
    ScreenInputAuthority,
    _append_jsonl,
    _block,
    _canonical,
    _clean_sha,
    _file_sha,
    _replace,
    _sha,
    _text,
    scenario_costs,
)
from adaptive_trading_bot.research.last_5m_protective_execution import (
    ExitPolicy,
    Last5mBar,
    Last5mExecutionBinding,
    Last5mProtectionConfiguration,
    Last5mProtectiveExecutionResult,
    ProtectionAcknowledgement,
    TriggerKind,
    run_last_5m_protective_execution,
    select_no_time_exit_prefix,
)
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
from adaptive_trading_bot.strategies.bollinger_method2 import (
    CONFIRMED_ID,
    CONTROL_ID,
    BollingerMethod2Audit,
    BollingerMethod2Configuration,
    BollingerMethod2HourlyContext,
    BollingerMethod2Strategy,
)
from adaptive_trading_bot.strategies.context import assemble_strategy_contexts
from adaptive_trading_bot.strategies.core import CandidateAction

PROTOCOL_ID = "BTC_ETH_BB_METHOD2_1H_NO_TIME_EXIT_PAIRED_V1"
RESULT_SCHEMA = "bb-method2-hourly-no-time-exit-paired-result-v1"
FOLLOWUP_END = datetime(2026, 10, 14, tzinfo=UTC)
PARENT_RELATIVE = Path("data/research_evidence/pr-236-bollinger-method2-mfi-1h-v1")
PARENT_EXECUTABLE = "706c7818573c566e9a6a78e542887e7916c03612"
PARENT_RUN_ID = "63964675-0ab4-4b46-89c3-1b034b69dfbd"
PARENT_PROTOCOL_SHA256 = "e4948ecdc43e03b072cc62f8ab6cfbeb9dfb58f80221c2e7e7aa0a5c52431672"
PARENT_PROJECTION_SHA256 = "6ecc6eae280738dd800bc2076116aa4d54a79ab0109c53eddf41f612c69ac416"
PARENT_MANIFEST_SHA256 = "190dd9eafdba3b924a560769b3089c8e4b8ab77a8b9566801dab4d2aa1348f5a"
PARENT_TRADES_BYTES = 11_018_109
PARENT_TRADES_SHA256 = "bac3ae504a7c9b21791b76fb05c7f209214d10e4e531c7ee4011a0e81dccaa4c"
_DECIMAL_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class ParentAuthority:
    """Byte and identity authority for one immutable comparator publication."""

    execution_sha: str
    run_id: str
    protocol_sha256: str
    projection_sha256: str
    publication_manifest_sha256: str
    trades_bytes: int
    trades_sha256: str
    expected_rows: int


DEFAULT_PARENT_AUTHORITY = ParentAuthority(
    execution_sha=PARENT_EXECUTABLE,
    run_id=PARENT_RUN_ID,
    protocol_sha256=PARENT_PROTOCOL_SHA256,
    projection_sha256=PARENT_PROJECTION_SHA256,
    publication_manifest_sha256=PARENT_MANIFEST_SHA256,
    trades_bytes=PARENT_TRADES_BYTES,
    trades_sha256=PARENT_TRADES_SHA256,
    expected_rows=5_152,
)


class NoTimeExitError(ScreenError):
    """The paired parent, reconstructed authority, or new result is contradictory."""


def protocol_record(
    parent: ParentAuthority = DEFAULT_PARENT_AUTHORITY,
    *,
    followup_end: datetime = FOLLOWUP_END,
) -> dict[str, object]:
    return {
        "protocol": PROTOCOL_ID,
        "parent": {
            "execution_sha": parent.execution_sha,
            "run_id": parent.run_id,
            "protocol_sha256": parent.protocol_sha256,
            "projection_sha256": parent.projection_sha256,
            "publication_manifest_sha256": parent.publication_manifest_sha256,
            "comparator": "SAVED_30M_OUTCOMES_NOT_RERUN",
        },
        "population": {
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "arms": list(ARMS),
            "scenarios": [scenario.value for scenario in Scenario],
            "admission_identity": "EXACT_PARENT_ASSET_ARM_DECISION_SIDE",
        },
        "treatment": {
            "exit_policy": ExitPolicy.FROZEN_TARGET_NO_TIME_EXIT.value,
            "stop": "UNCHANGED_OPPOSITE_FROZEN_BAND",
            "target": "UNCHANGED_DECISION_FIXED_2R",
            "holding_timer": None,
            "execution": "LAST_5M_PROTECTIVE_EXECUTION_V1",
            "data_end": followup_end.isoformat(),
            "terminal_open": "DATA_END_CENSORED",
            "terminal_valuation": "HYPOTHETICAL_SHARED_PRICING_AT_LAST_CLOSE",
        },
        "unchanged": {
            "entries": "PARENT_STRATEGY_RISK_SAFETY_JOURNAL_AUTHORIZATION",
            "costs": {
                scenario.value: _text(asdict(scenario_costs(scenario))) for scenario in Scenario
            },
            "funding": "NOT_MODELED_PRE_FUNDING",
            "signal_start_exclusive": SIGNAL_START.isoformat(),
            "signal_end_inclusive": SIGNAL_END.isoformat(),
        },
        "interpretation": "POST_RESULT_EXPLORATORY_PAIRED_NO_PROMOTION",
    }


PROTOCOL_SHA256 = _sha(_canonical(protocol_record()))


def _parent_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    try:
        return (
            str(row["symbol"]),
            str(row["arm"]),
            str(row["scenario"]),
            datetime.fromisoformat(str(row["decision_at"])).isoformat(),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise NoTimeExitError("parent row has an invalid pair key") from error


def load_parent(
    repo: Path,
    parent: Path | None = None,
    *,
    authority: ParentAuthority = DEFAULT_PARENT_AUTHORITY,
) -> tuple[dict[str, Any], dict[tuple[str, str, str, str], dict[str, Any]]]:
    root = (repo / PARENT_RELATIVE if parent is None else parent).resolve(strict=True)
    if _file_sha(root / "publication-manifest.json") != authority.publication_manifest_sha256:
        raise NoTimeExitError("parent publication manifest changed")
    if _file_sha(root / "projection.json") != authority.projection_sha256:
        raise NoTimeExitError("parent projection changed")
    projection = json.loads((root / "projection.json").read_bytes())
    if (
        projection.get("run_id") != authority.run_id
        or projection.get("executable_sha") != authority.execution_sha
        or projection.get("protocol_sha256") != authority.protocol_sha256
    ):
        raise NoTimeExitError("parent projection identity changed")
    compressed = (root / "trades.jsonl.gz").read_bytes()
    raw = gzip.decompress(compressed)
    if len(raw) != authority.trades_bytes or sha256(raw).hexdigest() != authority.trades_sha256:
        raise NoTimeExitError("parent complete trade authority changed")
    rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for line in raw.splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise NoTimeExitError("parent trade row is not an object")
        key = _parent_key(value)
        if key in rows:
            raise NoTimeExitError("parent trade pair key is duplicated")
        rows[key] = value
    if len(rows) != authority.expected_rows:
        raise NoTimeExitError("parent trade population is incomplete")
    return projection, rows


def _same_decimal(left: object, right: object) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except Exception:
        return False


def _verify_parent_authority(
    parent: dict[str, Any],
    *,
    wire: str,
    arm: str,
    scenario: Scenario,
    decision: DecisionJournalEntry[object, object],
    audit: BollingerMethod2Audit,
    result: Last5mProtectiveExecutionResult,
) -> None:
    expected = {
        "symbol": wire,
        "arm": arm,
        "scenario": scenario.value,
        "decision_at": decision.identity.observed_at.isoformat(),
        "side": result.intent_audit.side.value,
    }
    if any(str(parent[name]) != str(value) for name, value in expected.items()):
        raise NoTimeExitError("reconstructed admission identity differs from parent")
    facts = {
        "reference": audit.decision_reference,
        "stop": result.original_stop_basis,
        "target": audit.frozen_target,
        "quantity": result.intent_audit.quantity,
        "approved_risk_quote": result.approved_risk_quote,
    }
    if any(not _same_decimal(parent[name], value) for name, value in facts.items()):
        raise NoTimeExitError("reconstructed admission geometry/risk differs from parent")
    entry = result.entry_fill
    parent_entry = parent.get("entry")
    if entry is None or not isinstance(parent_entry, dict):
        raise NoTimeExitError("parent or treatment entry fill is missing")
    entry_facts = {
        "side": entry.side.value,
        "fill_at": entry.fill_at.isoformat(),
        "reference_price": entry.reference_price,
        "fill_price": entry.fill_price,
        "quantity": entry.quantity,
        "fee_quote": entry.fee_quote,
    }
    for name, value in entry_facts.items():
        if name in {"side", "fill_at"}:
            if str(parent_entry[name]) != str(value):
                raise NoTimeExitError("treatment entry identity differs from parent")
        elif not _same_decimal(parent_entry[name], value):
            raise NoTimeExitError("treatment entry economics differ from parent")


def _terminal_valuation(
    result: Last5mProtectiveExecutionResult,
    terminal_bar: Last5mBar,
) -> dict[str, Any]:
    if result.entry_fill is None:
        return {"status": "UNAVAILABLE_NO_ENTRY"}
    if result.position_closed:
        assert result.exit_fill is not None
        return {
            "status": "REALIZED_NATURAL_CLOSE",
            "valuation_at": terminal_bar.close_time,
            "reference_price": result.exit_fill.reference_price,
            "fill_price": result.exit_fill.fill_price,
            "hypothetical_exit_fee_quote": Decimal(0),
            "gross_pnl_quote": result.realized_gross_pnl,
            "total_fees_quote": result.total_fees,
            "net_pnl_quote": result.realized_net_pnl,
            "net_approved_risk_r": result.closed_net_approved_risk_r,
            "depends_on_open_position": False,
        }
    exit_side = (
        SimulatedExecutionSide.SELL
        if result.entry_fill.side is SimulatedExecutionSide.BUY
        else SimulatedExecutionSide.BUY
    )
    quote = price_simulated_market_fill(
        side=exit_side,
        quantity=result.entry_fill.quantity,
        reference_price=terminal_bar.close,
        costs=result.configuration.costs,
    )
    with localcontext(_DECIMAL_CONTEXT):
        direction = (
            Decimal(1) if result.entry_fill.side is SimulatedExecutionSide.BUY else Decimal(-1)
        )
        gross = (
            (quote.fill_price - result.entry_fill.fill_price)
            * result.entry_fill.quantity
            * direction
        )
        fees = result.entry_fill.fee_quote + quote.fee_quote
        net = gross - fees
        net_r = net / result.approved_risk_quote
    return {
        "status": "HYPOTHETICAL_DATA_END_CLOSE",
        "valuation_at": terminal_bar.close_time,
        "reference_price": terminal_bar.close,
        "fill_price": quote.fill_price,
        "hypothetical_exit_fee_quote": quote.fee_quote,
        "gross_pnl_quote": gross,
        "total_fees_quote": fees,
        "net_pnl_quote": net,
        "net_approved_risk_r": net_r,
        "depends_on_open_position": True,
    }


def _result_row(
    parent: dict[str, Any],
    result: Last5mProtectiveExecutionResult,
    terminal_bar: Last5mBar,
) -> dict[str, Any]:
    valuation = _terminal_valuation(result, terminal_bar)
    trigger = None if result.trigger is None else result.trigger.kind.value
    parent_trigger = parent.get("trigger")
    parent_trigger_kind = parent_trigger.get("kind") if isinstance(parent_trigger, dict) else None
    parent_r = Decimal(str(parent["closed_net_r"]))
    terminal_r = valuation.get("net_approved_risk_r")
    return {
        "pair_key": list(_parent_key(parent)),
        "symbol": parent["symbol"],
        "arm": parent["arm"],
        "scenario": parent["scenario"],
        "decision_at": parent["decision_at"],
        "side": parent["side"],
        "parent": {
            "status": parent["status"],
            "trigger": parent_trigger_kind,
            "gross_pnl_quote": parent["gross_pnl_quote"],
            "total_fees_quote": parent["total_fees_quote"],
            "net_pnl_quote": parent["net_pnl_quote"],
            "closed_net_r": parent["closed_net_r"],
            "holding_lower_seconds": parent["holding_lower_seconds"],
            "holding_upper_seconds": parent["holding_upper_seconds"],
        },
        "entry": _text(asdict(result.entry_fill)) if result.entry_fill is not None else None,
        "authority": {
            "approved_risk_quote": result.approved_risk_quote,
            "quantity": result.intent_audit.quantity,
            "stop_basis": result.original_stop_basis,
            "frozen_target_price": result.target_price,
            "exit_policy": result.exit_policy.value,
            "configuration": _text(asdict(result.configuration)),
            "decision_identity": _text(asdict(result.decision_identity)),
        },
        "new": {
            "status": result.status.value,
            "trigger": trigger,
            "natural_closed": result.position_closed,
            "gross_pnl_quote": result.realized_gross_pnl,
            "entry_commission_quote": (
                None if result.entry_fill is None else result.entry_fill.fee_quote
            ),
            "exit_commission_quote": (
                None if result.exit_fill is None else result.exit_fill.fee_quote
            ),
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
            "series_identity": result.series_identity,
            "binding_identity": result.binding.identity,
        },
        "terminal_valuation": valuation,
        "terminal_minus_parent_net_r": (
            None if terminal_r is None else Decimal(str(terminal_r)) - parent_r
        ),
    }


def _assert_pre_deadline_equality(parent: dict[str, Any], row: dict[str, Any]) -> None:
    trigger = parent.get("trigger")
    parent_kind = trigger.get("kind") if isinstance(trigger, dict) else None
    if parent_kind == TriggerKind.TIME_EXIT.value:
        return
    new = row["new"]
    fields = (
        "status",
        "trigger",
        "gross_pnl_quote",
        "total_fees_quote",
        "net_pnl_quote",
        "closed_net_r",
    )
    for field in fields:
        left = parent["status"] if field == "status" else parent.get(field)
        if field == "trigger":
            left = parent_kind
        right = new[field]
        if field in {"status", "trigger"}:
            if str(left) != str(right):
                raise NoTimeExitError("natural pre-deadline outcome differs from parent")
        elif not _same_decimal(left, right):
            raise NoTimeExitError("natural pre-deadline economics differ from parent")


def _metrics(rows: list[dict[str, Any]], *, terminal: bool) -> dict[str, Any]:
    values: list[Decimal] = []
    gross = Decimal(0)
    fees = Decimal(0)
    net = Decimal(0)
    closed = 0
    censored = 0
    triggers: Counter[str] = Counter()
    lower: list[Decimal] = []
    upper: list[Decimal] = []
    bins: Counter[str] = Counter()
    for row in rows:
        new = row["new"]
        valuation = row["terminal_valuation"]
        if new["natural_closed"]:
            closed += 1
            triggers[str(new["trigger"])] += 1
        else:
            censored += 1
            triggers["DATA_END_CENSORED"] += 1
        source = valuation if terminal else new
        value = source.get("net_approved_risk_r") if terminal else source.get("closed_net_r")
        if value is not None:
            values.append(Decimal(str(value)))
            gross += Decimal(str(source["gross_pnl_quote"]))
            fees += Decimal(str(source["total_fees_quote"]))
            net += Decimal(str(source["net_pnl_quote"]))
        if new["holding_lower_seconds"] is not None:
            lower.append(Decimal(str(new["holding_lower_seconds"])))
            upper.append(Decimal(str(new["holding_upper_seconds"])))
            seconds = Decimal(str(new["holding_upper_seconds"]))
            label = (
                "LE_30M"
                if seconds <= 1800
                else "GT_30M_LE_2H"
                if seconds <= 7200
                else "GT_2H_LE_8H"
                if seconds <= 28800
                else "GT_8H_LE_24H"
                if seconds <= 86400
                else "GT_24H"
            )
            bins[label] += 1
        elif not new["natural_closed"] and row["entry"] is not None:
            entry_at = datetime.fromisoformat(str(row["entry"]["fill_at"]))
            terminal_at = datetime.fromisoformat(str(valuation["valuation_at"]))
            age = Decimal(str((terminal_at - entry_at).total_seconds()))
            upper.append(age)
            lower.append(age)
            bins["STILL_OPEN"] += 1
    with localcontext(_DECIMAL_CONTEXT):
        positive = sum((value for value in values if value > 0), Decimal(0))
        negative = -sum((value for value in values if value < 0), Decimal(0))
        mean = None if not values else sum(values, Decimal(0)) / Decimal(len(values))
        pf = None if negative == 0 else positive / negative
    ordered_lower = sorted(lower)
    ordered_upper = sorted(upper)
    p90_index = max(0, (len(ordered_upper) * 9 + 9) // 10 - 1) if ordered_upper else 0
    return {
        "denominator": len(rows),
        "valued": len(values),
        "natural_closed": closed,
        "data_end_censored": censored,
        "mean_net_r": mean,
        "median_net_r": None if not values else median(values),
        "profit_factor": pf,
        "gross_pnl_quote": gross,
        "total_fees_quote": fees,
        "net_pnl_quote": net,
        "terminal_dependent_count": sum(
            bool(row["terminal_valuation"].get("depends_on_open_position")) for row in rows
        ),
        "status_counts": dict(sorted(triggers.items())),
        "holding_lower_median_seconds": None if not ordered_lower else median(ordered_lower),
        "holding_upper_median_seconds": None if not ordered_upper else median(ordered_upper),
        "holding_upper_p90_seconds": None if not ordered_upper else ordered_upper[p90_index],
        "holding_upper_max_seconds": None if not ordered_upper else ordered_upper[-1],
        "holding_bins": dict(sorted(bins.items())),
    }


def _peak_concurrency(rows: list[dict[str, Any]]) -> int:
    events: list[tuple[datetime, int]] = []
    for row in rows:
        entry = row["entry"]
        if entry is None:
            continue
        start = datetime.fromisoformat(str(entry["fill_at"]))
        upper = row["new"]["holding_upper_seconds"]
        end = FOLLOWUP_END if upper is None else start + timedelta(seconds=float(upper))
        events.extend(((start, 1), (end, -1)))
    active = peak = 0
    for _, change in sorted(events, key=lambda item: (item[0], item[1])):
        active += change
        peak = max(peak, active)
    return peak


def _projection(
    *,
    run_id: str,
    executable_sha: str,
    parent_projection: dict[str, Any],
    rows: list[dict[str, Any]],
    members: list[dict[str, Any]],
    authority: ScreenInputAuthority,
    parent_authority: ParentAuthority,
    protocol_sha256: str,
) -> dict[str, Any]:
    table: dict[str, Any] = {}
    blocks: dict[str, Any] = {}
    transitions: dict[str, Any] = {}
    selection: dict[str, Any] = {}
    concurrency: dict[str, int] = {}
    for wire in authority.shard_map:
        for scenario in Scenario:
            by_arm = {
                arm: [
                    row
                    for row in rows
                    if row["symbol"] == wire
                    and row["scenario"] == scenario.value
                    and row["arm"] == arm
                ]
                for arm in ARMS
            }
            confirmed_ids = {row["decision_at"] for row in by_arm[CONFIRMED_ID]}
            for arm, values in by_arm.items():
                key = f"{wire}:{scenario.value}:{arm}"
                table[f"{key}:NATURAL_CLOSED"] = _metrics(values, terminal=False)
                table[f"{key}:ALL_ADMISSION_TERMINAL"] = _metrics(values, terminal=True)
                concurrency[key] = _peak_concurrency(values)
                time_exit = [row for row in values if row["parent"]["trigger"] == "TIME_EXIT"]
                transitions[key] = {
                    "parent_time_exit_denominator": len(time_exit),
                    "new_outcomes": dict(
                        sorted(
                            Counter(
                                str(row["new"]["trigger"] or row["new"]["status"])
                                for row in time_exit
                            ).items()
                        )
                    ),
                    "terminal_minus_parent_r": _distribution(
                        [Decimal(str(row["terminal_minus_parent_net_r"])) for row in time_exit]
                    ),
                }
                for block in ("BLOCK_1", "BLOCK_2", "BLOCK_3"):
                    block_rows = [
                        row
                        for row in values
                        if _block(
                            datetime.fromisoformat(str(row["decision_at"])) - DECISION_CADENCE,
                            authority.signal_start,
                        )
                        == block
                    ]
                    blocks[f"{key}:{block}"] = _metrics(block_rows, terminal=True)
            retained = [row for row in by_arm[CONTROL_ID] if row["decision_at"] in confirmed_ids]
            excluded = [
                row for row in by_arm[CONTROL_ID] if row["decision_at"] not in confirmed_ids
            ]
            selection[f"{wire}:{scenario.value}"] = {
                "retained": _metrics(retained, terminal=True),
                "excluded": _metrics(excluded, terminal=True),
            }
    return {
        "schema": RESULT_SCHEMA,
        "protocol": PROTOCOL_ID,
        "protocol_sha256": protocol_sha256,
        "run_id": run_id,
        "executable_sha": executable_sha,
        "parent": {
            "execution_sha": parent_authority.execution_sha,
            "run_id": parent_authority.run_id,
            "protocol_sha256": parent_authority.protocol_sha256,
            "projection_sha256": parent_authority.projection_sha256,
            "publication_manifest_sha256": (parent_authority.publication_manifest_sha256),
            "classification": parent_projection["classification"],
            "promotion": parent_projection["promotion"],
        },
        "input_hashes": {
            "manifest": authority.manifest_sha256,
            "report": authority.report_sha256,
            "plan": authority.plan_sha256,
            **{f"{wire}-5m": digest for wire, digest in authority.shards},
        },
        "members": members,
        "paired_rows": len(rows),
        "economic_table": table,
        "parent_time_exit_transitions": transitions,
        "chronological_blocks": blocks,
        "selection_comparison": selection,
        "peak_concurrency": concurrency,
        "classification": "POST_RESULT_EXPLORATORY",
        "promotion": "NO_PROMOTION",
        "funding": "NOT_MODELED_PRE_FUNDING",
    }


def _distribution(values: list[Decimal]) -> dict[str, Any]:
    ordered = sorted(values)
    with localcontext(_DECIMAL_CONTEXT):
        return {
            "count": len(ordered),
            "mean": None if not ordered else sum(ordered, Decimal(0)) / Decimal(len(ordered)),
            "median": None if not ordered else median(ordered),
            "minimum": None if not ordered else ordered[0],
            "maximum": None if not ordered else ordered[-1],
            "positive": sum(value > 0 for value in ordered),
            "zero": sum(value == 0 for value in ordered),
            "negative": sum(value < 0 for value in ordered),
        }


def run(
    repo: Path,
    output: Path,
    *,
    run_id: str,
    executable_sha: str,
    authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY,
    parent: Path | None = None,
    parent_authority: ParentAuthority = DEFAULT_PARENT_AUTHORITY,
    followup_end: datetime = FOLLOWUP_END,
) -> dict[str, Any]:
    repo = repo.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise NoTimeExitError("output already exists")
    _clean_sha(repo, executable_sha)
    parent_projection, parent_rows = load_parent(repo, parent, authority=parent_authority)
    protocol = protocol_record(parent_authority, followup_end=followup_end)
    protocol_sha256 = _sha(_canonical(protocol))
    output.mkdir(parents=True)
    _replace(
        output / "run-identity.json",
        {
            "run_id": run_id,
            "executable_sha": executable_sha,
            "protocol_sha256": protocol_sha256,
            "parent_run_id": parent_authority.run_id,
            "started_at": datetime.now(UTC),
        },
    )
    _replace(output / "protocol.json", protocol)
    accepted = AcceptedInput(repo, authority)
    rows: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    consumed: set[tuple[str, str, str, str]] = set()
    for wire in authority.shard_map:
        store, five = accepted.load(wire, tail_end=followup_end)
        by_open = {bar.open_time: index for index, bar in enumerate(five)}
        replay = ReplayEngine(HistoricalReplaySource(store)).prepare(
            tuple(
                ReplaySeries(
                    store.source_id,
                    store.symbol,
                    frame,
                    authority.context_start,
                    authority.signal_end,
                )
                for frame in FRAMES
            ),
            clock=FrozenClock(authority.context_start),
        )
        features, structures, regimes, composer = (
            FeatureEngine(),
            MarketStructureEngine(),
            TechnicalRegimeEngine(),
            MultiTimeframeRegimeComposer(),
        )
        strategies = {
            CONTROL_ID: BollingerMethod2Strategy(BollingerMethod2Configuration(False)),
            CONFIRMED_ID: BollingerMethod2Strategy(BollingerMethod2Configuration(True)),
        }
        risk = RiskManager()
        safety = OperationalSafetyGate()
        assumptions = ResearchSafetyAssumptionProfile()
        counts: dict[str, Counter[str]] = {arm: Counter() for arm in ARMS}
        for batch in replay:
            observed = observed_candles_from_replay(batch)
            hourly = {
                event.candle.symbol: event.candle
                for event in observed.events
                if event.candle.timeframe is Timeframe.HOUR_1
            }
            feature_batch = features.process(observed)
            structure_batch = structures.process(observed)
            regime_batch = regimes.process(feature_batch, structure_batch)
            multi = composer.process(regime_batch)
            for base in assemble_strategy_contexts(observed, feature_batch, structure_batch, multi):
                candle = hourly.get(base.symbol)
                if candle is None:
                    continue
                context = BollingerMethod2HourlyContext(
                    base.source_id,
                    base.symbol,
                    base.observed_at,
                    base.candle_15m,
                    base.features_15m,
                    base.structure_15m,
                    base.multi_timeframe_regime,
                    candle,
                )
                evaluations = {
                    arm: strategy.evaluate(context) for arm, strategy in strategies.items()
                }
                if not authority.signal_start < context.observed_at <= authority.signal_end:
                    continue
                for arm, evaluation in evaluations.items():
                    counts[arm]["observations"] += 1
                    if evaluation.action is not CandidateAction.NO_TRADE:
                        counts[arm]["signals"] += 1
                    portfolio = RiskPortfolioSnapshot(
                        context.observed_at,
                        "USDT",
                        RESEARCH_EQUITY,
                        RESEARCH_EQUITY,
                        RESEARCH_EQUITY,
                        (),
                    )
                    risk_decision = risk.evaluate(evaluation, portfolio)
                    if (
                        evaluation.action is not CandidateAction.NO_TRADE
                        and risk_decision.status is RiskDecisionStatus.REJECTED
                    ):
                        counts[arm]["risk_rejected"] += 1
                    safety_decision = safety.evaluate(
                        risk_decision, _research_health(context, assumptions)
                    )
                    if (
                        risk_decision.status is RiskDecisionStatus.APPROVED
                        and safety_decision.status is not OperationalSafetyStatus.CLEARED
                    ):
                        counts[arm]["safety_rejected"] += 1
                    decision = DecisionJournalEntry(
                        context, evaluation, risk_decision, safety_decision
                    )
                    decision.validate()
                    authorization = EntryOrderIntentAuthorizer().authorize(decision)
                    if authorization.status is not OrderIntentAuthorizationStatus.AUTHORIZED:
                        continue
                    intent = authorization.intent
                    assert intent is not None
                    counts[arm]["admitted"] += 1
                    audit = evaluation.rule_audit
                    assert isinstance(audit, BollingerMethod2Audit)
                    assert audit.frozen_target is not None
                    start_index = by_open.get(context.observed_at)
                    if start_index is None:
                        raise NoTimeExitError("admission has no exact 5m follow-up start")
                    suffix = five[start_index:]
                    for scenario in Scenario:
                        key = (wire, arm, scenario.value, context.observed_at.isoformat())
                        parent_row = parent_rows.get(key)
                        if parent_row is None:
                            raise NoTimeExitError("reconstructed admission is absent from parent")
                        if key in consumed:
                            raise NoTimeExitError("reconstructed admission is duplicated")
                        configuration = Last5mProtectionConfiguration(
                            scenario_costs(scenario),
                            ExitPolicy.FROZEN_TARGET_NO_TIME_EXIT,
                            ProtectionAcknowledgement.ACCEPTED,
                            timedelta(0),
                            frozen_target_price=audit.frozen_target,
                            holding_period=None,
                        )
                        prefix = select_no_time_exit_prefix(
                            intent=intent, bars=suffix, configuration=configuration
                        )
                        binding = Last5mExecutionBinding(
                            store.source_id,
                            store.symbol,
                            prefix[0].epoch_id,
                            prefix[0].open_time,
                            prefix[-1].close_time,
                        )
                        result = run_last_5m_protective_execution(
                            intent=intent,
                            binding=binding,
                            bars=prefix,
                            configuration=configuration,
                        )
                        _verify_parent_authority(
                            parent_row,
                            wire=wire,
                            arm=arm,
                            scenario=scenario,
                            decision=decision,
                            audit=audit,
                            result=result,
                        )
                        row = _result_row(parent_row, result, prefix[-1])
                        _assert_pre_deadline_equality(parent_row, row)
                        consumed.add(key)
                        rows.append(row)
                        _append_jsonl(output / "paired-rows.jsonl", row)
            if (datetime.now(UTC).second % 30) == 0:
                _replace(
                    output / "progress.json",
                    {
                        "symbol": wire,
                        "observed_at": batch.observed_at,
                        "paired_rows": len(rows),
                        "counts": {arm: dict(value) for arm, value in counts.items()},
                    },
                )
        member = {"symbol": wire, "counts": {arm: dict(value) for arm, value in counts.items()}}
        members.append(member)
        _replace(output / f"{wire.lower()}-member.json", member)
    if consumed != set(parent_rows):
        raise NoTimeExitError("parent population has missing or extra reconstructed admissions")
    projection = _projection(
        run_id=run_id,
        executable_sha=executable_sha,
        parent_projection=parent_projection,
        rows=rows,
        members=members,
        authority=authority,
        parent_authority=parent_authority,
        protocol_sha256=protocol_sha256,
    )
    _replace(output / "projection.json", projection)
    _replace(
        output / "complete.json",
        {
            "status": "COMPLETE",
            "paired_rows": len(rows),
            "projection_sha256": _file_sha(output / "projection.json"),
            "completed_at": datetime.now(UTC),
        },
    )
    return projection
