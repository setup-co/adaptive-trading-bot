import { dashboardApi } from "../api/client";
import { AvailableFact, Fact } from "../components/Fact";
import { QueryBoundary } from "../components/QueryBoundary";
import { Status } from "../components/Status";
import { useReadOnlyQuery } from "../hooks/useReadOnlyQuery";
import { Page } from "./OverviewPage";

export function HealthPage() {
  const health = useReadOnlyQuery((signal) => dashboardApi.health(signal), []);
  const snapshot = useReadOnlyQuery((signal) => dashboardApi.snapshot(signal), []);
  return (
    <Page title="System health" eyebrow="Process liveness & source facts">
      <div className="stack">
        <section className="panel">
          <h2>API application</h2>
          <QueryBoundary state={health}>
            {(value) => (
              <div className="health-line">
                <Status value={value.status} /> <strong>{value.service}</strong>
                <span>Liveness only — not trading readiness.</span>
              </div>
            )}
          </QueryBoundary>
        </section>
        <QueryBoundary state={snapshot}>
          {(value) => (
            <>
              <section className="integrity-banner">
                <Status value={value.system.live_trading_enabled ? "LIVE ENABLED" : "LIVE DISABLED"} />
                <div>
                  <strong>Backend-provided live trading fact</strong>
                  <span>Observed {value.system.observed_at}</span>
                </div>
              </section>
              <section className="metric-grid">
                {value.system.sources.map((source) => (
                  <article className="panel" key={`${source.source_id}-${source.symbol}`}>
                    <div className="panel-heading">
                      <div>
                        <span className="eyebrow">{source.source_id}</span>
                        <h2>{source.symbol}</h2>
                      </div>
                      {source.safety_status.availability === "AVAILABLE" ? (
                        <Status value={source.safety_status.value} />
                      ) : (
                        <Status value="UNAVAILABLE" />
                      )}
                    </div>
                    <dl className="facts">
                      <Fact label="Market timestamp" value={source.market_data_observed_at} />
                      <Fact label="Feed integrity" value={source.feed_integrity_state} />
                      <Fact label="Exchange API" value={source.exchange_api_health_state} />
                      <Fact label="Spread (bps)" value={source.spread_bps} mono />
                      <Fact label="Time sync (ms)" value={source.time_sync_offset_ms} mono />
                      <Fact label="Volatility safety" value={source.volatility_safety_state} />
                      <Fact label="Price anomaly" value={source.suspicious_price_anomaly ? "Detected" : "Not detected"} />
                      <Fact label="Order rejections" value={source.consecutive_order_rejections} />
                      <AvailableFact label="Safety reasons" fact={source.safety_reasons} />
                    </dl>
                  </article>
                ))}
              </section>
              <section className="panel">
                <h2>Explicitly reported system facts</h2>
                <dl className="facts columns">
                  <AvailableFact label="Global safety status" fact={value.system.global_safety_status} />
                  <AvailableFact label="Last error" fact={value.system.last_error} />
                  <AvailableFact label="Last news update" fact={value.system.last_news_update} />
                </dl>
                <p className="muted">Per-source decisions remain separate; this view does not infer a global safety state.</p>
              </section>
            </>
          )}
        </QueryBoundary>
      </div>
    </Page>
  );
}
