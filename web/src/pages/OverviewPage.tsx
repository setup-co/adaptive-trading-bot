import { dashboardApi } from "../api/client";
import { AvailableFact, Fact } from "../components/Fact";
import { QueryBoundary } from "../components/QueryBoundary";
import { Status } from "../components/Status";
import { useReadOnlyQuery } from "../hooks/useReadOnlyQuery";

export function OverviewPage() {
  const state = useReadOnlyQuery((signal) => dashboardApi.snapshot(signal), []);
  return (
    <Page title="Market overview" eyebrow="Authoritative runtime snapshot">
      <QueryBoundary state={state}>
        {(snapshot) => (
          <div className="stack">
            <div className="observation-line">
              Observed <time>{snapshot.observed_at}</time>
              <Status value={snapshot.system.live_trading_enabled ? "LIVE ENABLED" : "LIVE DISABLED"} />
            </div>
            <section className="metric-grid" aria-label="Market observations">
              {snapshot.markets.map((market) => (
                <article className="panel market-card" key={`${market.source_id}-${market.symbol}`}>
                  <div className="panel-heading">
                    <div><span className="eyebrow">{market.source_id}</span><h2>{market.symbol}</h2></div>
                    <span className="price mono">{market.midpoint}</span>
                  </div>
                  <dl className="facts">
                    <Fact label="Spread (bps)" value={market.spread_bps} mono />
                    <AvailableFact label="Regime" fact={market.regime} />
                    <AvailableFact label="Volatility" fact={market.volatility} />
                    <AvailableFact label="Technical score" fact={market.technical_score} />
                    <AvailableFact label="Fundamental" fact={market.fundamental_score} />
                    <AvailableFact label="Experience" fact={market.experience_metrics} />
                  </dl>
                </article>
              ))}
            </section>
            <section className="panel">
              <div className="panel-heading"><div><span className="eyebrow">Paper account</span><h2>{snapshot.account.quote_currency}</h2></div><span className="price mono">{snapshot.account.equity}</span></div>
              <dl className="facts columns">
                <Fact label="Gross realized PnL" value={snapshot.account.realized_gross_pnl} mono />
                <Fact label="Cumulative fees" value={snapshot.account.cumulative_fees} mono />
                <Fact label="Net realized PnL" value={snapshot.account.cumulative_net_pnl} mono />
                <Fact label="Locked open risk" value={snapshot.account.locked_open_risk} mono />
                <Fact label="Locked gross exposure" value={snapshot.account.locked_gross_exposure} mono />
                <Fact label="Policy" value={snapshot.account.account_policy_version} mono />
              </dl>
            </section>
            <section className="panel">
              <div className="panel-heading"><div><span className="eyebrow">Open Paper positions</span><h2>{snapshot.open_positions.length} observed</h2></div></div>
              {snapshot.open_positions.length === 0 ? <p className="muted">No open Paper positions.</p> : (
                <div className="table-wrap"><table><thead><tr><th>Symbol</th><th>Side</th><th>Quantity</th><th>Entry</th><th>Stop basis</th><th>Locked risk</th><th>Locked gross</th></tr></thead><tbody>{snapshot.open_positions.map((position) => <tr key={position.accounting_fill_id}><td>{position.symbol}</td><td><Status value={position.side} /></td><td className="mono">{position.quantity}</td><td className="mono">{position.entry_fill_price}</td><td className="mono">{position.stop_basis}</td><td className="mono">{position.locked_open_risk}</td><td className="mono">{position.locked_gross_exposure}</td></tr>)}</tbody></table></div>
              )}
            </section>
            <section className="panel">
              <div className="panel-heading"><div><span className="eyebrow">Recent completed trades</span><h2>Authoritative outcomes</h2></div></div>
              {snapshot.recent_completed_trades.length === 0 ? <p className="muted">No completed trades in this snapshot.</p> : (
                <div className="table-wrap"><table><thead><tr><th>Symbol</th><th>Strategy</th><th>Outcome</th><th>Gross</th><th>Fees</th><th>Net</th><th>R</th></tr></thead><tbody>{snapshot.recent_completed_trades.map((trade) => <tr key={trade.accounting_exit_fill_id}><td>{trade.symbol}</td><td>{trade.strategy_id}</td><td><Status value={trade.outcome_classification} /></td><td className="mono">{trade.gross_realized_pnl}</td><td className="mono">{trade.total_fees}</td><td className="mono">{trade.net_realized_pnl}</td><td className="mono">{trade.realized_r}</td></tr>)}</tbody></table></div>
              )}
            </section>
          </div>
        )}
      </QueryBoundary>
    </Page>
  );
}

export function Page({ title, eyebrow, children }: { title: string; eyebrow: string; children: React.ReactNode }) {
  return <main><header className="page-header"><span className="eyebrow">{eyebrow}</span><h1>{title}</h1></header>{children}</main>;
}
