"""Selective completion of the retained hourly no-time-exit treatment."""

from __future__ import annotations

import gzip
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from adaptive_trading_bot.domain import FrozenClock, Timeframe
from adaptive_trading_bot.features import (
    FeatureEngine,
    MarketStructureEngine,
    observed_candles_from_replay,
)
from adaptive_trading_bot.journal import DecisionJournalEntry
from adaptive_trading_bot.orders import EntryOrderIntentAuthorizer, OrderIntentAuthorizationStatus
from adaptive_trading_bot.regime import MultiTimeframeRegimeComposer, TechnicalRegimeEngine
from adaptive_trading_bot.replay import HistoricalReplaySource, ReplayEngine, ReplaySeries
from adaptive_trading_bot.research.bollinger_method2_no_time_exit import (
    DEFAULT_PARENT_AUTHORITY,
    FOLLOWUP_END,
    NoTimeExitError,
    ParentAuthority,
    _parent_key,
    _projection,
    _result_row,
    _same_decimal,
    _verify_parent_authority,
    load_parent,
)
from adaptive_trading_bot.research.bollinger_method2_no_time_exit import (
    PROTOCOL_SHA256 as INTENDED_POLICY_PROTOCOL_SHA256,
)
from adaptive_trading_bot.research.bollinger_method2_screen import ARMS
from adaptive_trading_bot.research.failed_breakout_reversion_screen import (
    DEFAULT_INPUT_AUTHORITY,
    FRAMES,
    RESEARCH_EQUITY,
    AcceptedInput,
    Scenario,
    ScreenInputAuthority,
    _append_jsonl,
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
    Last5mExecutionBinding,
    Last5mProtectionConfiguration,
    Last5mProtectiveExecutionResult,
    ProtectionAcknowledgement,
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

PROTOCOL_ID = "BTC_ETH_BB_METHOD2_1H_NO_TIME_EXIT_COMPLETION_V1"
OUTPUT_SCHEMA = "bb-method2-hourly-no-time-exit-completion-v1"
SHORTER_RELATIVE = Path("data/research_evidence/pr-236-bollinger-method2-no-time-exit-v1")
SHORTER_EXECUTABLE = "a392c72ac12981734717027fdf5bdb7cf393178a"
SHORTER_RUN_ID = "2de5c8b3-afc5-4654-bad9-d5856df3f7f5"
SHORTER_CUTOFF = datetime(2026, 10, 7, 0, 40, tzinfo=UTC)
SHORTER_PROTOCOL_SHA256 = "e525a2cb503abb2fc90f7f7dead79a1769e3ec759ae71b6551e6a37a613cc127"
SHORTER_PROJECTION_SHA256 = "c703edb89dd0c9f9145720546e14ffb1f645cf4d120ac3cc19e2a31518f3dc90"
SHORTER_MANIFEST_SHA256 = "79185f497576be4dd5a94502a4dd6577bd057803e90c994230c9d62589507b76"
SHORTER_ROWS_BYTES = 15_234_067
SHORTER_ROWS_SHA256 = "f961e8e7e93143ecf2d1d382a96d959f05bf6df32ccb7f642c3d2d97efd56199"
SHORTER_GZIP_SHA256 = "bb74c02eaff15a16c8e7f520732f819342ec3baded7e913e32acfbdb3f1e855f"
EXPECTED_ROWS = 5_152
EXPECTED_REUSED = 4_970
EXPECTED_REPLAYED = 182
EXPECTED_DISTINCT_OPEN_TIMES = {"BTCUSDT": 33, "ETHUSDT": 30}
EXPECTED_PARTITION = {
    ("BTCUSDT", "BASE", CONTROL_ID): (965, 33),
    ("BTCUSDT", "STRESS", CONTROL_ID): (965, 33),
    ("BTCUSDT", "BASE", CONFIRMED_ID): (259, 15),
    ("BTCUSDT", "STRESS", CONFIRMED_ID): (259, 15),
    ("ETHUSDT", "BASE", CONTROL_ID): (960, 30),
    ("ETHUSDT", "STRESS", CONTROL_ID): (960, 30),
    ("ETHUSDT", "BASE", CONFIRMED_ID): (301, 13),
    ("ETHUSDT", "STRESS", CONFIRMED_ID): (301, 13),
}


@dataclass(frozen=True)
class ShorterAuthority:
    """Immutable identity of one reusable shorter-follow-up capture."""

    executable_sha: str
    run_id: str
    cutoff: datetime
    protocol_sha256: str
    projection_sha256: str
    publication_manifest_sha256: str
    rows_bytes: int
    rows_sha256: str
    gzip_sha256: str
    expected_rows: int
    expected_reused: int
    expected_replayed: int


DEFAULT_SHORTER_AUTHORITY = ShorterAuthority(
    SHORTER_EXECUTABLE,
    SHORTER_RUN_ID,
    SHORTER_CUTOFF,
    SHORTER_PROTOCOL_SHA256,
    SHORTER_PROJECTION_SHA256,
    SHORTER_MANIFEST_SHA256,
    SHORTER_ROWS_BYTES,
    SHORTER_ROWS_SHA256,
    SHORTER_GZIP_SHA256,
    EXPECTED_ROWS,
    EXPECTED_REUSED,
    EXPECTED_REPLAYED,
)


def protocol_record(
    *,
    comparator: ParentAuthority = DEFAULT_PARENT_AUTHORITY,
    shorter: ShorterAuthority = DEFAULT_SHORTER_AUTHORITY,
    endpoint: datetime = FOLLOWUP_END,
) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL_ID,
        "output_schema": OUTPUT_SCHEMA,
        "economic_comparator": asdict(comparator),
        "reusable_treatment": {
            "executable_sha": shorter.executable_sha,
            "run_id": shorter.run_id,
            "cutoff": shorter.cutoff.isoformat(),
            "protocol_sha256": shorter.protocol_sha256,
            "projection_sha256": shorter.projection_sha256,
            "publication_manifest_sha256": shorter.publication_manifest_sha256,
            "rows_bytes": shorter.rows_bytes,
            "rows_sha256": shorter.rows_sha256,
            "gzip_sha256": shorter.gzip_sha256,
            "expected_rows": shorter.expected_rows,
            "expected_reused": shorter.expected_reused,
            "expected_replayed": shorter.expected_replayed,
            "known_protocol_discrepancy": "CAPTURE_CLAIMED_INTENDED_HASH_BUT_SAVED_ACTUAL_HASH",
        },
        "partition": {
            "total_pair_keys": shorter.expected_rows,
            "reuse_natural_close": shorter.expected_reused,
            "replay_terminal_open": shorter.expected_replayed,
            "selection": "SAVED_SHORTER_ROW_NATURAL_CLOSED_STATUS",
        },
        "completion": {
            "endpoint": endpoint.isoformat(),
            "entry_replay": "ORIGINAL_AUTHORIZED_ENTRY_ONLY_FOR_PREVIOUSLY_OPEN_KEYS",
            "exit_policy": ExitPolicy.FROZEN_TARGET_NO_TIME_EXIT.value,
            "intended_policy_protocol_sha256": INTENDED_POLICY_PROTOCOL_SHA256,
            "open_terminal_status": "DATA_END_CENSORED",
        },
        "unchanged": {
            "arms": list(ARMS),
            "scenarios": [scenario.value for scenario in Scenario],
            "costs": {
                scenario.value: _text(asdict(scenario_costs(scenario))) for scenario in Scenario
            },
            "funding": "NOT_MODELED_PRE_FUNDING",
        },
        "interpretation": "SELECTIVE_COMPLETION_POST_RESULT_NO_PROMOTION",
    }


PROTOCOL_SHA256 = _sha(_canonical(protocol_record()))


def load_shorter(
    repo: Path,
    root: Path | None = None,
    *,
    authority: ShorterAuthority = DEFAULT_SHORTER_AUTHORITY,
) -> tuple[dict[str, Any], dict[tuple[str, str, str, str], dict[str, Any]]]:
    directory = (repo / SHORTER_RELATIVE if root is None else root).resolve(strict=True)
    if _file_sha(directory / "publication-manifest.json") != authority.publication_manifest_sha256:
        raise NoTimeExitError("shorter publication manifest changed")
    if _file_sha(directory / "projection.json") != authority.projection_sha256:
        raise NoTimeExitError("shorter projection changed")
    protocol = json.loads((directory / "protocol.json").read_bytes())
    if _sha(_canonical(protocol)) != authority.protocol_sha256:
        raise NoTimeExitError("shorter actual protocol identity changed")
    if protocol.get("treatment", {}).get("data_end") != authority.cutoff.isoformat():
        raise NoTimeExitError("shorter actual cutoff changed")
    compressed = (directory / "paired-rows.jsonl.gz").read_bytes()
    if sha256(compressed).hexdigest() != authority.gzip_sha256:
        raise NoTimeExitError("shorter compressed rows changed")
    raw = gzip.decompress(compressed)
    if len(raw) != authority.rows_bytes or sha256(raw).hexdigest() != authority.rows_sha256:
        raise NoTimeExitError("shorter complete row authority changed")
    rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for line in raw.splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise NoTimeExitError("shorter row is not an object")
        key = _parent_key(value)
        if key in rows:
            raise NoTimeExitError("shorter pair key is duplicated")
        rows[key] = value
    if len(rows) != authority.expected_rows:
        raise NoTimeExitError("shorter pair population is incomplete")
    return json.loads((directory / "projection.json").read_bytes()), rows


def partition_shorter(
    rows: dict[tuple[str, str, str, str], dict[str, Any]],
    *,
    authority: ShorterAuthority = DEFAULT_SHORTER_AUTHORITY,
    enforce_default_counts: bool = True,
) -> tuple[set[tuple[str, str, str, str]], set[tuple[str, str, str, str]]]:
    reused: set[tuple[str, str, str, str]] = set()
    replayed: set[tuple[str, str, str, str]] = set()
    counts: Counter[tuple[str, str, str]] = Counter()
    closed_counts: Counter[tuple[str, str, str]] = Counter()
    for key, row in rows.items():
        new = row.get("new")
        if not isinstance(new, dict):
            raise NoTimeExitError("shorter row lacks treatment result")
        group = (key[0], key[2], key[1])
        counts[group] += 1
        if new.get("natural_closed") is True:
            if new.get("status") not in {"ORDINARY_CLOSED", "INVALID_GEOMETRY_RECOVERED"}:
                raise NoTimeExitError("natural-close row has an unexplained status")
            reused.add(key)
            closed_counts[group] += 1
        elif new.get("natural_closed") is False and new.get("status") == "TERMINAL_OPEN":
            entry = row.get("entry")
            authority_record = row.get("authority")
            terminal = row.get("terminal_valuation")
            if (
                not isinstance(entry, dict)
                or not isinstance(authority_record, dict)
                or not isinstance(terminal, dict)
                or new.get("unresolved_quantity") is None
            ):
                raise NoTimeExitError("selected open row lacks retained position authority")
            if terminal.get("valuation_at") != authority.cutoff.isoformat():
                raise NoTimeExitError("selected open row has the wrong shorter-cutoff valuation")
            if terminal.get("depends_on_open_position") is not True:
                raise NoTimeExitError("selected open row lacks a censored-position valuation")
            if not _same_decimal(new["unresolved_quantity"], entry.get("quantity")):
                raise NoTimeExitError("selected open quantity differs from the entry quantity")
            if not _same_decimal(entry.get("quantity"), authority_record.get("quantity")):
                raise NoTimeExitError("selected open quantity differs from saved authority")
            replayed.add(key)
        else:
            raise NoTimeExitError(
                "shorter row is neither reusable close nor eligible terminal open"
            )
    if len(reused) != authority.expected_reused or len(replayed) != authority.expected_replayed:
        raise NoTimeExitError("shorter fixed partition count changed")
    if enforce_default_counts:
        for group, (closed, opened) in EXPECTED_PARTITION.items():
            if closed_counts[group] != closed or counts[group] - closed_counts[group] != opened:
                raise NoTimeExitError("shorter asset/scenario/arm partition changed")
        for wire, expected in EXPECTED_DISTINCT_OPEN_TIMES.items():
            observed = {key[3] for key in replayed if key[0] == wire}
            if len(observed) != expected:
                raise NoTimeExitError("shorter distinct open-decision set changed")
    return reused, replayed


def _verify_shorter_entry(row: dict[str, Any], result: Last5mProtectiveExecutionResult) -> None:
    entry = result.entry_fill
    saved_entry = row.get("entry")
    authority = row.get("authority")
    if entry is None or not isinstance(saved_entry, dict) or not isinstance(authority, dict):
        raise NoTimeExitError("selected shorter entry authority is incomplete")
    entry_facts: dict[str, object] = {
        "side": entry.side.value,
        "fill_at": entry.fill_at.isoformat(),
        "reference_price": entry.reference_price,
        "fill_price": entry.fill_price,
        "quantity": entry.quantity,
        "fee_quote": entry.fee_quote,
    }
    for name, value in entry_facts.items():
        saved = saved_entry.get(name)
        if name in {"side", "fill_at"}:
            if str(saved) != str(value):
                raise NoTimeExitError("selected entry identity differs from shorter capture")
        elif not _same_decimal(saved, value):
            raise NoTimeExitError("selected entry economics differ from shorter capture")
    authority_facts: dict[str, object] = {
        "approved_risk_quote": result.approved_risk_quote,
        "quantity": result.intent_audit.quantity,
        "stop_basis": result.original_stop_basis,
        "frozen_target_price": result.target_price,
    }
    for name, value in authority_facts.items():
        if not _same_decimal(authority.get(name), value):
            raise NoTimeExitError("selected geometry/risk differs from shorter capture")


def _assert_event_not_before_cutoff(
    result: Last5mProtectiveExecutionResult, cutoff: datetime
) -> None:
    if result.trigger is not None and result.trigger.interval_open < cutoff:
        raise NoTimeExitError("selected replay found a trigger before the shorter cutoff")
    if result.exit_fill is not None and result.exit_fill.fill_at < cutoff:
        raise NoTimeExitError("selected replay found an exit fill before the shorter cutoff")


def _validate_common_economics(rows: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(
            (str(row["symbol"]), str(row["scenario"]), str(row["decision_at"])), []
        ).append(row)
    fields = (
        "status",
        "trigger",
        "gross_pnl_quote",
        "total_fees_quote",
        "net_pnl_quote",
        "closed_net_r",
        "unresolved_quantity",
    )
    for values in grouped.values():
        if len(values) != 2 or {str(value["arm"]) for value in values} != set(ARMS):
            continue
        left, right = (value["new"] for value in values)
        for field in fields:
            if field in {"status", "trigger"}:
                equal = str(left.get(field)) == str(right.get(field))
            else:
                left_value = left.get(field)
                right_value = right.get(field)
                equal = (left_value is None and right_value is None) or _same_decimal(
                    left_value, right_value
                )
            if not equal:
                raise NoTimeExitError("common control/MFI treatment economics differ")


def run(
    repo: Path,
    output: Path,
    *,
    run_id: str,
    executable_sha: str,
    authority: ScreenInputAuthority = DEFAULT_INPUT_AUTHORITY,
    comparator_root: Path | None = None,
    comparator_authority: ParentAuthority = DEFAULT_PARENT_AUTHORITY,
    shorter_root: Path | None = None,
    shorter_authority: ShorterAuthority = DEFAULT_SHORTER_AUTHORITY,
    endpoint: datetime = FOLLOWUP_END,
    expected_protocol_sha256: str | None = None,
    enforce_default_counts: bool = True,
) -> dict[str, Any]:
    repo = repo.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise NoTimeExitError("completion output already exists")
    _clean_sha(repo, executable_sha)
    protocol = protocol_record(
        comparator=comparator_authority,
        shorter=shorter_authority,
        endpoint=endpoint,
    )
    protocol_sha256 = _sha(_canonical(protocol))
    if expected_protocol_sha256 is not None and protocol_sha256 != expected_protocol_sha256:
        raise NoTimeExitError("effective completion protocol differs from declared identity")
    parent_projection, parent_rows = load_parent(
        repo, comparator_root, authority=comparator_authority
    )
    _, shorter_rows = load_shorter(repo, shorter_root, authority=shorter_authority)
    if set(parent_rows) != set(shorter_rows):
        raise NoTimeExitError("shorter and comparator pair populations differ")
    reused, replayed = partition_shorter(
        shorter_rows,
        authority=shorter_authority,
        enforce_default_counts=enforce_default_counts,
    )
    output.mkdir(parents=True)
    _replace(
        output / "run-identity.json",
        {
            "run_id": run_id,
            "executable_sha": executable_sha,
            "protocol_sha256": protocol_sha256,
            "comparator_run_id": comparator_authority.run_id,
            "shorter_run_id": shorter_authority.run_id,
            "started_at": datetime.now(UTC),
        },
    )
    _replace(output / "protocol.json", protocol)
    accepted = AcceptedInput(repo, authority)
    completed: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    members: list[dict[str, Any]] = []
    for wire in authority.shard_map:
        store, five = accepted.load(wire, tail_end=endpoint)
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
        features = FeatureEngine()
        structures = MarketStructureEngine()
        regimes = TechnicalRegimeEngine()
        composer = MultiTimeframeRegimeComposer()
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
                    if evaluation.action is CandidateAction.NO_TRADE:
                        continue
                    counts[arm]["signals"] += 1
                    risk_decision = risk.evaluate(
                        evaluation,
                        RiskPortfolioSnapshot(
                            context.observed_at,
                            "USDT",
                            RESEARCH_EQUITY,
                            RESEARCH_EQUITY,
                            RESEARCH_EQUITY,
                            (),
                        ),
                    )
                    if risk_decision.status is not RiskDecisionStatus.APPROVED:
                        counts[arm]["risk_rejected"] += 1
                        continue
                    safety_decision = safety.evaluate(
                        risk_decision, _research_health(context, assumptions)
                    )
                    if safety_decision.status is not OperationalSafetyStatus.CLEARED:
                        counts[arm]["safety_rejected"] += 1
                        continue
                    decision = DecisionJournalEntry(
                        context, evaluation, risk_decision, safety_decision
                    )
                    decision.validate()
                    authorization = EntryOrderIntentAuthorizer().authorize(decision)
                    if (
                        authorization.status is not OrderIntentAuthorizationStatus.AUTHORIZED
                        or authorization.intent is None
                    ):
                        raise NoTimeExitError("approved selected decision was not authorized")
                    counts[arm]["admitted"] += 1
                    intent = authorization.intent
                    audit = evaluation.rule_audit
                    assert isinstance(audit, BollingerMethod2Audit)
                    assert audit.frozen_target is not None
                    start_index = by_open.get(context.observed_at)
                    if start_index is None:
                        raise NoTimeExitError("selected admission lacks exact 5m follow-up start")
                    suffix = five[start_index:]
                    for scenario in Scenario:
                        key = (wire, arm, scenario.value, context.observed_at.isoformat())
                        if key not in replayed:
                            continue
                        configuration = Last5mProtectionConfiguration(
                            scenario_costs(scenario),
                            ExitPolicy.FROZEN_TARGET_NO_TIME_EXIT,
                            ProtectionAcknowledgement.ACCEPTED,
                            timedelta(0),
                            frozen_target_price=audit.frozen_target,
                            holding_period=None,
                        )
                        prefix = select_no_time_exit_prefix(
                            intent=intent,
                            bars=suffix,
                            configuration=configuration,
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
                            parent_rows[key],
                            wire=wire,
                            arm=arm,
                            scenario=scenario,
                            decision=decision,
                            audit=audit,
                            result=result,
                        )
                        _verify_shorter_entry(shorter_rows[key], result)
                        _assert_event_not_before_cutoff(result, shorter_authority.cutoff)
                        completed[key] = _result_row(parent_rows[key], result, prefix[-1])
                        _replace(
                            output / "progress.json",
                            {
                                "replayed_completed": len(completed),
                                "replayed_expected": len(replayed),
                                "last_pair_key": list(key),
                            },
                        )
        members.append(
            {"symbol": wire, "counts": {arm: dict(value) for arm, value in counts.items()}}
        )
    if set(completed) != replayed:
        raise NoTimeExitError("selective replay did not complete the exact open-key set")
    composite: list[dict[str, Any]] = []
    for key in sorted(shorter_rows):
        source = shorter_rows[key]
        if key in reused:
            final = source
            provenance = "REUSED_NATURAL_CLOSE"
        else:
            final = completed[key]
            provenance = "REPLAYED_PREVIOUSLY_OPEN"
        composite.append(final)
        _append_jsonl(
            output / "composite-rows.jsonl",
            {
                "pair_key": list(key),
                "provenance": provenance,
                "source_shorter_row": source,
                "composite_row": final,
            },
        )
    _validate_common_economics(composite)
    projection = _projection(
        run_id=run_id,
        executable_sha=executable_sha,
        parent_projection=parent_projection,
        rows=composite,
        members=members,
        authority=authority,
        parent_authority=comparator_authority,
        protocol_sha256=protocol_sha256,
    )
    projection.update(
        {
            "schema": OUTPUT_SCHEMA,
            "protocol": PROTOCOL_ID,
            "reuse_provenance": {
                "reused_natural_close": len(reused),
                "replayed_previously_open": len(replayed),
                "shorter_run_id": shorter_authority.run_id,
                "shorter_executable_sha": shorter_authority.executable_sha,
                "completion_executable_sha": executable_sha,
            },
            "classification": "COMPOSITE_COMPLETION_MIXED",
            "promotion": "NO_PROMOTION",
        }
    )
    _replace(output / "projection.json", projection)
    _replace(
        output / "complete.json",
        {
            "status": "COMPLETE",
            "pair_keys": len(composite),
            "reused": len(reused),
            "replayed": len(replayed),
            "projection_sha256": _file_sha(output / "projection.json"),
            "completed_at": datetime.now(UTC),
        },
    )
    return projection
