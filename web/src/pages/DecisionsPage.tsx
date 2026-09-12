import { type FormEvent, useState } from "react";

import { dashboardApi } from "../api/client";
import type { DecisionRecord } from "../api/schemas";
import { AvailableFact, Fact } from "../components/Fact";
import { QueryBoundary } from "../components/QueryBoundary";
import { Status } from "../components/Status";
import { useReadOnlyQuery } from "../hooks/useReadOnlyQuery";
import { Page } from "./OverviewPage";

type Filters = {
  start: string; end: string; source: string; symbol: string; strategy: string;
  action: string; risk: string; safety: string;
};

export function DecisionsPage() {
  const [draft, setDraft] = useState<Filters>(() => defaultFilters());
  const [filters, setFilters] = useState(draft);
  const state = useReadOnlyQuery(
    (signal) => dashboardApi.decisions({
      start: filters.start,
      end: filters.end,
      source_id: filters.source || undefined,
      symbol: filters.symbol || undefined,
      strategy_id: filters.strategy || undefined,
      action: filters.action || undefined,
      risk_status: filters.risk || undefined,
      safety_status: filters.safety || undefined,
      limit: 500,
    }, signal),
    [filters.start, filters.end, filters.source, filters.symbol, filters.strategy, filters.action, filters.risk, filters.safety],
  );
  const update = (key: keyof Filters, value: string) => setDraft({ ...draft, [key]: value });
  const submit = (event: FormEvent) => { event.preventDefault(); setFilters(draft); };
  return (
    <Page title="Decision journal" eyebrow="Strategy · Risk · Operational Safety">
      <form className="filter-bar" onSubmit={submit} aria-label="Decision filters">
        <label>UTC start<input type="datetime-local" value={toInput(draft.start)} onChange={(event) => update("start", toUtc(event.target.value))} /></label>
        <label>UTC end<input type="datetime-local" value={toInput(draft.end)} onChange={(event) => update("end", toUtc(event.target.value))} /></label>
        <label>Source<input value={draft.source} onChange={(event) => update("source", event.target.value)} placeholder="Any" /></label>
        <label>Symbol<select value={draft.symbol} onChange={(event) => update("symbol", event.target.value)}><option value="">Any</option><option>BTC/USDT</option><option>ETH/USDT</option></select></label>
        <label>Strategy<input value={draft.strategy} onChange={(event) => update("strategy", event.target.value)} placeholder="Any" /></label>
        <label>Action<select value={draft.action} onChange={(event) => update("action", event.target.value)}><option value="">Any</option><option>LONG_CANDIDATE</option><option>SHORT_CANDIDATE</option><option>NO_TRADE</option></select></label>
        <label>Risk<select value={draft.risk} onChange={(event) => update("risk", event.target.value)}><option value="">Any</option><option>APPROVED</option><option>REJECTED</option><option>NO_ACTION</option></select></label>
        <label>Safety<select value={draft.safety} onChange={(event) => update("safety", event.target.value)}><option value="">Any</option><option>CLEARED</option><option>BLOCKED_BY_RISK</option><option>SAFE_MODE</option><option>NO_ACTION</option></select></label>
        <button type="submit">Apply filters</button>
      </form>
      <QueryBoundary state={state} empty={(page) => page.records.length === 0}>
        {(page) => <div className="stack"><p className="result-count">Showing {page.returned_count} of {page.total_matched} matches{page.truncated ? " · bounded result" : ""}</p>{page.records.map((record) => <DecisionCard key={identityKey(record)} record={record} />)}</div>}
      </QueryBoundary>
    </Page>
  );
}

function DecisionCard({ record }: { record: DecisionRecord }) {
  return (
    <article className="decision-card panel">
      <header className="record-header"><div><span className="eyebrow">{record.identity.source_id} · {record.identity.symbol}</span><h2>{record.identity.strategy_id} <small>v{record.identity.strategy_version}</small></h2><time>{record.identity.observed_at}</time></div><div className="status-cluster"><Status value={record.strategy.action} /><Status value={record.risk.status} /><Status value={record.safety.status} /></div></header>
      <div className="layer-grid">
        <section className="layer strategy-layer"><span className="layer-number">01</span><h3>Strategy</h3><Status value={record.strategy.action} /><dl className="facts"><Fact label="Reference" value={record.strategy.reference_price ?? "Not supplied"} mono /><Fact label="Invalidation" value={record.strategy.invalidation_reference_price ?? "Not supplied"} mono /><AvailableFact label="Reasons for" fact={record.strategy.reasons_for} /><AvailableFact label="Reasons against" fact={record.strategy.reasons_against} /><AvailableFact label="Rule audit" fact={record.strategy.rule_audit} /><AvailableFact label="Fundamental" fact={record.strategy.fundamental_context} /><AvailableFact label="Experience" fact={record.strategy.experience_metrics} /><AvailableFact label="Confidence" fact={record.strategy.confidence} /><AvailableFact label="Target" fact={record.strategy.target_price} /><AvailableFact label="Expected R/R" fact={record.strategy.expected_reward_risk} /></dl></section>
        <section className="layer risk-layer"><span className="layer-number">02</span><h3>Risk</h3><Status value={record.risk.status} /><ul className="reason-list">{record.risk.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul><dl className="facts"><Fact label="Policy" value={record.risk.policy_version} /><Fact label="Daily loss" value={record.risk.daily_loss_quote} mono /><Fact label="Daily loss fraction / max" value={`${record.risk.daily_loss_fraction} / ${record.risk.max_daily_loss_fraction}`} mono /><Fact label="Drawdown fraction / max" value={`${record.risk.drawdown_fraction} / ${record.risk.max_drawdown_fraction}`} mono /><Fact label="Open risk / cap" value={`${record.risk.current_open_risk_quote} / ${record.risk.simultaneous_risk_cap_quote}`} mono /><Fact label="Gross / cap" value={`${record.risk.current_gross_exposure_quote} / ${record.risk.gross_exposure_cap_quote}`} mono /><Fact label="Approved quantity" value={record.risk.approved_quantity ?? "Not approved"} mono /><Fact label="Approved risk" value={record.risk.approved_risk_quote ?? "Not approved"} mono /><Fact label="Halt review" value={record.risk.requires_halt_review ? "Required" : "Not required"} /></dl></section>
        <section className="layer safety-layer"><span className="layer-number">03</span><h3>Operational Safety</h3><Status value={record.safety.status} /><ul className="reason-list">{record.safety.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul><dl className="facts"><Fact label="Policy" value={record.safety.policy_version} /><Fact label="Feed" value={record.safety.feed_integrity_state} /><Fact label="Exchange API" value={record.safety.exchange_api_health_state} /><Fact label="Market data age" value={record.safety.market_data_age} /><Fact label="Spread / max (bps)" value={`${record.safety.spread_bps} / ${record.safety.max_spread_bps}`} mono /><Fact label="Time sync / max (ms)" value={`${record.safety.time_sync_offset_ms} / ${record.safety.max_abs_time_sync_offset_ms}`} mono /><Fact label="Volatility" value={record.safety.volatility_safety_state} /><Fact label="Price anomaly" value={record.safety.suspicious_price_anomaly ? "Detected" : "Not detected"} /><Fact label="Entry permission" value={record.safety.entry_permitted ? "Permitted" : "Not permitted"} /></dl></section>
      </div>
      <footer className="outcome-link">{record.completed_trade.availability === "AVAILABLE" ? <><Status value={record.completed_trade.value.outcome_classification} /><span>Completed outcome linked by exact decision identity · Net <strong className="mono">{record.completed_trade.value.net_realized_pnl}</strong></span></> : <><Status value="UNAVAILABLE" /><span>{record.completed_trade.reason}</span></>}</footer>
    </article>
  );
}

function identityKey(record: DecisionRecord): string {
  const id = record.identity;
  return `${id.observed_at}-${id.source_id}-${id.symbol}-${id.strategy_id}-${id.strategy_version}`;
}

function defaultFilters(): Filters {
  const end = new Date(); const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
  return { start: endUtc(start), end: endUtc(end), source: "", symbol: "", strategy: "", action: "", risk: "", safety: "" };
}
function endUtc(value: Date): string { return value.toISOString(); }
function toInput(value: string): string { return value.slice(0, 16); }
function toUtc(value: string): string { return new Date(`${value}:00Z`).toISOString(); }
