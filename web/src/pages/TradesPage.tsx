import { type FormEvent, useState } from "react";

import { dashboardApi } from "../api/client";
import type { DetailedTrade } from "../api/schemas";
import { AvailableFact, Fact } from "../components/Fact";
import { QueryBoundary } from "../components/QueryBoundary";
import { Status } from "../components/Status";
import { useReadOnlyQuery } from "../hooks/useReadOnlyQuery";
import { Page } from "./OverviewPage";

type Filters = {
  start: string;
  end: string;
  source: string;
  symbol: string;
  strategy: string;
  classification: string;
};

export function TradesPage() {
  const [draft, setDraft] = useState<Filters>(() => defaultFilters());
  const [filters, setFilters] = useState(draft);
  const state = useReadOnlyQuery(
    (signal) =>
      dashboardApi.trades(
        {
          start: filters.start,
          end: filters.end,
          source_id: filters.source || undefined,
          symbol: filters.symbol || undefined,
          strategy_id: filters.strategy || undefined,
          classification: filters.classification || undefined,
          limit: 500,
        },
        signal,
      ),
    [
      filters.start,
      filters.end,
      filters.source,
      filters.symbol,
      filters.strategy,
      filters.classification,
    ],
  );
  const update = (key: keyof Filters, value: string) => setDraft({ ...draft, [key]: value });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setFilters(draft);
  };
  return (
    <Page title="Completed trades" eyebrow="Authoritative post-trade journal">
      <form className="filter-bar" onSubmit={submit} aria-label="Trade filters">
        <label>
          UTC start
          <input
            type="datetime-local"
            value={toInput(draft.start)}
            onChange={(event) => update("start", toUtc(event.target.value))}
          />
        </label>
        <label>
          UTC end
          <input
            type="datetime-local"
            value={toInput(draft.end)}
            onChange={(event) => update("end", toUtc(event.target.value))}
          />
        </label>
        <label>
          Source
          <input
            value={draft.source}
            onChange={(event) => update("source", event.target.value)}
            placeholder="Any"
          />
        </label>
        <label>
          Symbol
          <select value={draft.symbol} onChange={(event) => update("symbol", event.target.value)}>
            <option value="">Any</option>
            <option>BTC/USDT</option>
            <option>ETH/USDT</option>
          </select>
        </label>
        <label>
          Strategy
          <input
            value={draft.strategy}
            onChange={(event) => update("strategy", event.target.value)}
            placeholder="Any"
          />
        </label>
        <label>
          Classification
          <select
            value={draft.classification}
            onChange={(event) => update("classification", event.target.value)}
          >
            <option value="">Any</option>
            <option>PROFIT</option>
            <option>LOSS</option>
            <option>BREAKEVEN</option>
          </select>
        </label>
        <button type="submit">Apply filters</button>
      </form>
      <QueryBoundary state={state} empty={(page) => page.records.length === 0}>
        {(page) => (
          <div className="stack">
            <p className="result-count">
              Showing {page.returned_count} of {page.total_matched} matches
              {page.truncated ? " · bounded result" : ""}
            </p>
            {page.records.map((trade) => (
              <TradeCard key={trade.accounting_exit_fill_id} trade={trade} />
            ))}
          </div>
        )}
      </QueryBoundary>
    </Page>
  );
}

function TradeCard({ trade }: { trade: DetailedTrade }) {
  return (
    <article className="panel trade-card">
      <header className="record-header">
        <div>
          <span className="eyebrow">
            {trade.source_id} · {trade.symbol}
          </span>
          <h2>
            {trade.strategy_id} <small>v{trade.strategy_version}</small>
          </h2>
          <time>{trade.exit_fill_at}</time>
        </div>
        <div className="status-cluster">
          <Status value={trade.outcome_classification} />
          <Status value={`${trade.entry_side} → ${trade.exit_side}`} />
        </div>
      </header>
      <div className="trade-grid">
        <section>
          <h3>Authorization & entry</h3>
          <dl className="facts">
            <Fact label="Decision observed" value={trade.originating_decision_identity.observed_at} />
            <Fact label="Authorized" value={trade.entry_authorized_at} />
            <Fact label="Reference" value={trade.entry_reference_price} mono />
            <Fact label="Filled" value={`${trade.entry_fill_at} @ ${trade.entry_fill_price}`} mono />
            <Fact label="Quantity" value={trade.quantity} mono />
            <Fact label="Approved notional" value={trade.approved_notional_quote} mono />
            <Fact label="Approved risk" value={trade.approved_risk_quote} mono />
            <Fact label="Stop basis" value={trade.stop_basis} mono />
            <Fact label="Spread / slippage / fee bps" value={`${trade.entry_spread_bps} / ${trade.entry_slippage_bps} / ${trade.entry_fee_bps}`} mono />
            <Fact label="Entry fee" value={trade.entry_fee_quote} mono />
          </dl>
        </section>
        <section>
          <h3>Trigger & exit</h3>
          <dl className="facts">
            <Fact label="Exit reason" value={trade.exit_reason} />
            <Fact label="Trigger" value={`${trade.trigger_observed_at} @ ${trade.trigger_reference_price}`} mono />
            <Fact label="Authorized" value={trade.exit_authorized_at} />
            <Fact label="Reference" value={trade.exit_reference_price} mono />
            <Fact label="Filled" value={`${trade.exit_fill_at} @ ${trade.exit_fill_price}`} mono />
            <Fact label="Spread / slippage / fee bps" value={`${trade.exit_spread_bps} / ${trade.exit_slippage_bps} / ${trade.exit_fee_bps}`} mono />
            <Fact label="Exit fee" value={trade.exit_fee_quote} mono />
            <Fact label="Duration" value={trade.duration} />
          </dl>
        </section>
        <section className="outcome-column">
          <h3>Recorded outcome</h3>
          <dl className="facts">
            <Fact label="Gross realized PnL" value={trade.gross_realized_pnl} mono />
            <Fact label="Total fees" value={trade.total_trade_fees_quote} mono />
            <Fact label="Net realized PnL" value={trade.net_realized_pnl} mono />
            <Fact label="Realized R net" value={trade.realized_r_net} mono />
            <AvailableFact label="MFE" fact={trade.mfe} />
            <AvailableFact label="MAE" fact={trade.mae} />
            <AvailableFact label="Process score" fact={trade.process_score} />
          </dl>
        </section>
      </div>
      <footer className="audit-footer mono">
        <span>Risk {trade.risk_policy_version}</span>
        <span>Safety {trade.safety_policy_version}</span>
        <span>Entry fill {trade.accounting_entry_fill_id}</span>
        <span>Exit fill {trade.accounting_exit_fill_id}</span>
      </footer>
    </article>
  );
}

function defaultFilters(): Filters {
  const end = new Date();
  const start = new Date(end.getTime() - 7 * 24 * 60 * 60 * 1000);
  return {
    start: start.toISOString(),
    end: end.toISOString(),
    source: "",
    symbol: "",
    strategy: "",
    classification: "",
  };
}

function toInput(value: string): string {
  return value.slice(0, 16);
}

function toUtc(value: string): string {
  return new Date(`${value}:00Z`).toISOString();
}
