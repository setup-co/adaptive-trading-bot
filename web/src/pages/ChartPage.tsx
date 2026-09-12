import { type FormEvent, useState } from "react";

import { dashboardApi } from "../api/client";
import { AvailableFact } from "../components/Fact";
import { CandleChart } from "../components/CandleChart";
import { QueryBoundary } from "../components/QueryBoundary";
import { Status } from "../components/Status";
import { useReadOnlyQuery } from "../hooks/useReadOnlyQuery";
import { Page } from "./OverviewPage";

type ChartFilters = { source: string; symbol: string; timeframe: string; start: string; end: string };

export function ChartPage() {
  const [draft, setDraft] = useState<ChartFilters>(() => defaultFilters());
  const [filters, setFilters] = useState(draft);
  const state = useReadOnlyQuery(
    (signal) =>
      dashboardApi.chart(
        {
          source_id: filters.source,
          symbol: filters.symbol,
          timeframe: filters.timeframe,
          start: toUtc(filters.start),
          end: toUtc(filters.end),
          limit: 500,
        },
        signal,
      ),
    [filters.source, filters.symbol, filters.timeframe, filters.start, filters.end],
  );
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setFilters(draft);
  };
  return (
    <Page title="Native candle chart" eyebrow="Historical store observation">
      <form className="filter-bar" onSubmit={submit} aria-label="Chart filters">
        <label>Source<input value={draft.source} onChange={(event) => setDraft({ ...draft, source: event.target.value })} /></label>
        <label>Symbol<select value={draft.symbol} onChange={(event) => setDraft({ ...draft, symbol: event.target.value })}><option>BTC/USDT</option><option>ETH/USDT</option></select></label>
        <label>Timeframe<select value={draft.timeframe} onChange={(event) => setDraft({ ...draft, timeframe: event.target.value })}><option value="15m">15m</option><option value="1h">1h</option><option value="4h">4h</option></select></label>
        <label>UTC start<input type="datetime-local" value={draft.start} onChange={(event) => setDraft({ ...draft, start: event.target.value })} /></label>
        <label>UTC end<input type="datetime-local" value={draft.end} onChange={(event) => setDraft({ ...draft, end: event.target.value })} /></label>
        <button type="submit">Load range</button>
      </form>
      <QueryBoundary state={state}>
        {(chart) => (
          <div className="stack">
            <section className={`integrity-banner ${chart.integrity.is_clean ? "clean" : "dirty"}`}>
              <Status value={chart.integrity.is_clean ? "CLEAN" : "DIRTY"} />
              <div><strong>Chart integrity</strong><span>{chart.integrity.rows_scanned} rows scanned</span></div>
              {!chart.integrity.is_clean && <strong>Gaps or storage issues are shown as returned; no repair was applied.</strong>}
            </section>
            {chart.integrity.issues.length > 0 && <section className="panel"><h2>Integrity issues</h2><ul className="reason-list">{chart.integrity.issues.map((issue, index) => <li key={`${issue.kind}-${index}`}><Status value={issue.kind} /> {issue.message} {issue.open_time && <time>{issue.open_time}</time>}</li>)}</ul></section>}
            {chart.candles.length === 0 && (
              <div className="state-card">No authoritative candles matched this range.</div>
            )}
            <section className="panel chart-panel"><CandleChart data={chart} /></section>
            <section className="metric-grid">
              <article className="panel"><h2>Persisted overlays</h2><dl className="facts"><AvailableFact label="EMA 20" fact={chart.overlays.ema_20} /><AvailableFact label="EMA 50" fact={chart.overlays.ema_50} /><AvailableFact label="VWAP" fact={chart.overlays.vwap} /><AvailableFact label="Key levels" fact={chart.overlays.key_levels} /></dl></article>
              <article className="panel"><h2>Authoritative markers</h2>{chart.markers.length === 0 ? <p className="muted">No markers in this range.</p> : <ul className="reason-list">{chart.markers.map((marker) => <li key={`${marker.identity}-${marker.kind}`}><Status value={marker.kind} /> <strong>{marker.label}</strong> <span className="mono">{marker.price}</span><small>{marker.timestamp} · {marker.side}</small></li>)}</ul>}</article>
            </section>
            <details className="panel"><summary>Exact candle values</summary><div className="table-wrap"><table><thead><tr><th>Open time</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th></tr></thead><tbody>{chart.candles.map((candle) => <tr key={candle.open_time}><td>{candle.open_time}</td><td className="mono">{candle.open}</td><td className="mono">{candle.high}</td><td className="mono">{candle.low}</td><td className="mono">{candle.close}</td><td className="mono">{candle.volume}</td></tr>)}</tbody></table></div></details>
          </div>
        )}
      </QueryBoundary>
    </Page>
  );
}

function defaultFilters(): ChartFilters {
  const end = new Date();
  const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
  return { source: "archive", symbol: "BTC/USDT", timeframe: "15m", start: inputTime(start), end: inputTime(end) };
}

function inputTime(value: Date): string {
  return value.toISOString().slice(0, 16);
}

function toUtc(value: string): string {
  return new Date(`${value}:00Z`).toISOString();
}
