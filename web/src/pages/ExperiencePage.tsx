import { type FormEvent, useState } from "react";

import { dashboardApi, type Query } from "../api/client";
import type { ExperienceEstimate, ExperienceFingerprint } from "../api/schemas";
import { Fact } from "../components/Fact";
import { QueryBoundary } from "../components/QueryBoundary";
import { Status } from "../components/Status";
import { useReadOnlyQuery } from "../hooks/useReadOnlyQuery";
import { Page } from "./OverviewPage";

export function ExperiencePage() {
  const [draftAsOf, setDraftAsOf] = useState(() => inputTime(new Date()));
  const [asOf, setAsOf] = useState(() => new Date().toISOString());
  const [selectedIndex, setSelectedIndex] = useState(0);
  const contexts = useReadOnlyQuery(
    (signal) => dashboardApi.experienceContexts({ as_of: asOf, limit: 200 }, signal),
    [asOf],
  );
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setSelectedIndex(0);
    setAsOf(toUtc(draftAsOf));
  };

  return (
    <Page title="Experience" eyebrow="Process-local statistical observation">
      <form className="filter-bar" onSubmit={submit} aria-label="Experience context query">
        <label>
          UTC as of
          <input
            type="datetime-local"
            value={draftAsOf}
            onChange={(event) => setDraftAsOf(event.target.value)}
          />
        </label>
        <button type="submit">Observe as of</button>
      </form>
      <QueryBoundary state={contexts} empty={(page) => page.contexts.length === 0}>
        {(page) => {
          const safeIndex = Math.min(selectedIndex, page.contexts.length - 1);
          const selected = page.contexts[safeIndex];
          return (
            <div className="stack">
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">Authoritative completed contexts</span>
                    <h2>Select exact fingerprint</h2>
                  </div>
                  <span className="result-count">
                    {page.returned_count} of {page.total_matched}
                    {page.truncated ? " · bounded result" : ""}
                  </span>
                </div>
                <label className="context-select">
                  Context
                  <select
                    value={safeIndex}
                    onChange={(event) => setSelectedIndex(event.target.selectedIndex)}
                  >
                    {page.contexts.map((context, index) => (
                      <option key={contextIdentity(context)} value={index}>
                        {context.symbol} · {context.strategy_id} v{context.strategy_version} · {context.regime_label}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="muted">{page.history_limitation}</p>
              </section>
              <ExperienceEstimatePanel fingerprint={selected} asOf={asOf} />
            </div>
          );
        }}
      </QueryBoundary>
    </Page>
  );
}

function ExperienceEstimatePanel({
  fingerprint,
  asOf,
}: {
  fingerprint: ExperienceFingerprint;
  asOf: string;
}) {
  const state = useReadOnlyQuery(
    (signal) => dashboardApi.experienceEstimate(estimateQuery(fingerprint, asOf), signal),
    [
      asOf,
      fingerprint.symbol,
      fingerprint.strategy_id,
      fingerprint.strategy_version,
      fingerprint.entry_timeframe,
      fingerprint.regime_label,
      fingerprint.direction,
      fingerprint.trend_strength_bucket,
      fingerprint.volatility_state,
      fingerprint.volume_state,
      fingerprint.higher_timeframe_bias,
      fingerprint.multi_timeframe_alignment,
    ],
  );
  return (
    <QueryBoundary state={state}>
      {(estimate) => <ExperienceEstimateView estimate={estimate} />}
    </QueryBoundary>
  );
}

function ExperienceEstimateView({ estimate }: { estimate: ExperienceEstimate }) {
  const noHistory = estimate.availability === "NO_HISTORY";
  return (
    <>
      <section className="panel experience-status">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">Estimate status</span>
            <h2>{estimate.target_fingerprint.symbol} · {estimate.target_fingerprint.strategy_id}</h2>
          </div>
          <div className="status-cluster">
            <Status value={estimate.availability} />
            <Status value={estimate.confidence} />
          </div>
        </div>
        <dl className="facts columns">
          <Fact label="Selected hierarchy" value={estimate.selected_level ?? "Unavailable"} />
          <Fact label="Selected raw N" value={estimate.eligible_selected_raw_sample_count} mono />
          <Fact label="Selected effective N" value={estimate.selected_effective_sample_size} mono />
        </dl>
        <p className="evidence-note">Confidence = evidence sufficiency, not probability of profit.</p>
        {estimate.availability === "INSUFFICIENT_EVIDENCE" && (
          <p className="state-card">Historical observations exist, but configured evidence minimums are not met.</p>
        )}
        {noHistory && (
          <p className="state-card">No completed historical samples matched this exact hierarchy as of the requested time. Numeric estimates remain unavailable.</p>
        )}
      </section>

      <section className="panel">
        <div className="panel-heading"><div><span className="eyebrow">Fallback provenance</span><h2>Canonical hierarchy evidence</h2></div></div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Level</th><th>Raw N</th><th>Effective N</th><th>Sufficient</th></tr></thead>
            <tbody>
              {estimate.examined_hierarchy.map((row) => (
                <tr key={row.key.level} className={row.key.level === estimate.selected_level ? "selected-row" : undefined}>
                  <td>{row.key.level}</td>
                  <td className="mono">{row.raw_eligible_sample_count}</td>
                  <td className="mono">{row.effective_sample_size}</td>
                  <td><Status value={row.sufficient ? "YES" : "NO"} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="metric-grid">
        <article className="panel">
          <span className="eyebrow">Bayesian estimate</span>
          <h2>Posterior win probability</h2>
          <dl className="facts">
            <OptionalFact label="Posterior mean win probability" value={estimate.posterior_mean_win_probability} />
            <OptionalFact label="Prior source" value={estimate.prior_source} />
            <OptionalFact label="Prior level" value={estimate.prior_source_level} />
            <OptionalFact label="External prior effective N" value={estimate.prior_external_effective_sample_size} />
            <OptionalFact label="Prior alpha / beta" value={pair(estimate.prior_alpha, estimate.prior_beta)} />
            <OptionalFact label="Posterior alpha / beta" value={pair(estimate.posterior_alpha, estimate.posterior_beta)} />
          </dl>
        </article>
        <article className="panel">
          <span className="eyebrow">Recency-weighted descriptive facts</span>
          <h2>Observed outcomes</h2>
          <dl className="facts">
            <OptionalFact label="Weighted win rate" value={estimate.recency_weighted_win_rate} />
            <OptionalFact label="Weighted expectancy R" value={estimate.recency_weighted_expectancy_r} />
            <OptionalFact
              label="Weighted net-quote expectancy"
              value={
                estimate.recency_weighted_expectancy_net_quote === null
                  ? null
                  : `${estimate.recency_weighted_expectancy_net_quote} ${estimate.quote_currency ?? ""}`.trim()
              }
            />
          </dl>
        </article>
        <article className="panel">
          <span className="eyebrow">Provenance and policy</span>
          <h2>Observation boundary</h2>
          <dl className="facts">
            <Fact label="As of" value={estimate.as_of} mono />
            <Fact label="Future samples excluded" value={estimate.excluded_future_sample_count} mono />
            <Fact label="Estimation policy" value={estimate.estimation_policy_version} mono />
            <Fact label="Recency policy" value={estimate.recency_policy_version} mono />
            <Fact label="Estimate schema" value={estimate.estimate_schema_version} mono />
            <Fact label="History scope" value={estimate.history_scope} />
          </dl>
          <p className="muted">{estimate.history_limitation}</p>
        </article>
      </section>
    </>
  );
}

function OptionalFact({ label, value }: { label: string; value: string | null }) {
  return value === null ? (
    <div className="fact unavailable-fact"><dt>{label}</dt><dd><Status value="UNAVAILABLE" /><small>No authoritative estimate is available.</small></dd></div>
  ) : (
    <Fact label={label} value={value} mono />
  );
}

function pair(left: string | null, right: string | null): string | null {
  return left === null || right === null ? null : `${left} / ${right}`;
}

function estimateQuery(fingerprint: ExperienceFingerprint, asOf: string): Query {
  return {
    as_of: asOf,
    symbol: fingerprint.symbol,
    strategy_id: fingerprint.strategy_id,
    strategy_version: fingerprint.strategy_version,
    entry_timeframe: fingerprint.entry_timeframe,
    regime_label: fingerprint.regime_label,
    direction: fingerprint.direction,
    trend_strength_bucket: fingerprint.trend_strength_bucket,
    volatility_state: fingerprint.volatility_state,
    volume_state: fingerprint.volume_state,
    higher_timeframe_bias: fingerprint.higher_timeframe_bias,
    multi_timeframe_alignment: fingerprint.multi_timeframe_alignment,
  };
}

function contextIdentity(context: ExperienceFingerprint): string {
  return [
    context.symbol,
    context.strategy_id,
    context.strategy_version,
    context.entry_timeframe,
    context.regime_label,
    context.direction,
    context.trend_strength_bucket,
    context.volatility_state,
    context.volume_state,
    context.higher_timeframe_bias,
    context.multi_timeframe_alignment,
  ].join("|");
}

function inputTime(value: Date): string {
  return value.toISOString().slice(0, 16);
}

function toUtc(value: string): string {
  return new Date(`${value}:00Z`).toISOString();
}
