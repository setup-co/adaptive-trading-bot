import { z } from "zod";

export const utcTimestampSchema = z.string().datetime({ offset: true }).refine(
  (value) => value.endsWith("Z") || value.endsWith("+00:00"),
  "timestamp must be UTC",
);
export const decimalStringSchema = z
  .string()
  .regex(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$/, "invalid Decimal string");

export const dashboardValueSchema = <T extends z.ZodType>(value: T) =>
  z.discriminatedUnion("availability", [
    z.object({ availability: z.literal("AVAILABLE"), value, reason: z.null() }),
    z.object({
      availability: z.literal("UNAVAILABLE"),
      value: z.null(),
      reason: z.string().min(1),
    }),
  ]);

const transportValueSchema = z.union([
  z.string(),
  z.number().int(),
  z.boolean(),
  z.array(z.string()),
]);
export const genericDashboardValueSchema = dashboardValueSchema(transportValueSchema);
const decimalValueSchema = dashboardValueSchema(decimalStringSchema);
const stringValueSchema = dashboardValueSchema(z.string());
const stringListValueSchema = dashboardValueSchema(z.array(z.string()));

export const healthSchema = z.object({ status: z.literal("ok"), service: z.string().min(1) });
export type Health = z.infer<typeof healthSchema>;

export const backendErrorSchema = z.object({
  status: z.enum(["unavailable", "invalid_request"]),
  code: z.string().min(1),
  message: z.string().min(1),
});

const marketSchema = z.object({
  source_id: z.string(),
  symbol: z.string(),
  observed_at: utcTimestampSchema,
  source_timestamp: dashboardValueSchema(utcTimestampSchema),
  midpoint: decimalStringSchema,
  spread_bps: decimalStringSchema,
  regime: stringValueSchema,
  volatility: stringValueSchema,
  technical_score: dashboardValueSchema(z.number().int()),
  fundamental_score: decimalValueSchema,
  experience_metrics: stringValueSchema,
});

const accountSchema = z.object({
  observed_at: utcTimestampSchema,
  quote_currency: z.string(),
  initial_equity: decimalStringSchema,
  equity: decimalStringSchema,
  day_start_equity: decimalStringSchema,
  peak_equity: decimalStringSchema,
  realized_gross_pnl: decimalStringSchema,
  cumulative_fees: decimalStringSchema,
  cumulative_net_pnl: decimalStringSchema,
  locked_open_risk: decimalStringSchema,
  locked_gross_exposure: decimalStringSchema,
  account_policy_version: z.string(),
});

const openPositionSchema = z.object({
  accounting_fill_id: z.string(),
  source_id: z.string(),
  symbol: z.string(),
  side: z.string(),
  quantity: decimalStringSchema,
  authorized_at: utcTimestampSchema,
  eligible_at: utcTimestampSchema,
  entry_fill_at: utcTimestampSchema,
  entry_reference_price: decimalStringSchema,
  entry_fill_price: decimalStringSchema,
  entry_fee: decimalStringSchema,
  stop_basis: decimalStringSchema,
  approved_risk: decimalStringSchema,
  approved_notional: decimalStringSchema,
  locked_open_risk: decimalStringSchema,
  locked_gross_exposure: decimalStringSchema,
  lifecycle_state: z.string(),
  risk_policy_version: z.string(),
  safety_policy_version: z.string(),
});

const snapshotTradeSchema = z.object({
  accounting_entry_fill_id: z.string(),
  accounting_exit_fill_id: z.string(),
  source_id: z.string(),
  symbol: z.string(),
  strategy_id: z.string(),
  strategy_version: z.string(),
  entry_side: z.string(),
  exit_side: z.string(),
  quantity: decimalStringSchema,
  entry_fill_at: utcTimestampSchema,
  entry_fill_price: decimalStringSchema,
  exit_fill_at: utcTimestampSchema,
  exit_fill_price: decimalStringSchema,
  stop_basis: decimalStringSchema,
  gross_realized_pnl: decimalStringSchema,
  total_fees: decimalStringSchema,
  net_realized_pnl: decimalStringSchema,
  realized_r: decimalStringSchema,
  duration: z.string(),
  outcome_classification: z.string(),
  mfe: decimalValueSchema,
  mae: decimalValueSchema,
  process_score: decimalValueSchema,
});

export const sourceSystemSchema = z.object({
  source_id: z.string(),
  symbol: z.string(),
  observed_at: utcTimestampSchema,
  market_data_observed_at: utcTimestampSchema,
  feed_integrity_state: z.string(),
  exchange_api_health_state: z.string(),
  spread_bps: decimalStringSchema,
  time_sync_offset_ms: decimalStringSchema,
  volatility_safety_state: z.string(),
  suspicious_price_anomaly: z.boolean(),
  consecutive_order_rejections: z.number().int().nonnegative(),
  safety_status: stringValueSchema,
  safety_reasons: stringListValueSchema,
});

export const snapshotSchema = z.object({
  observed_at: utcTimestampSchema,
  markets: z.array(marketSchema),
  account: accountSchema,
  open_positions: z.array(openPositionSchema),
  recent_completed_trades: z.array(snapshotTradeSchema),
  system: z.object({
    observed_at: utcTimestampSchema,
    sources: z.array(sourceSystemSchema),
    global_safety_status: stringValueSchema,
    last_error: stringValueSchema,
    last_news_update: dashboardValueSchema(utcTimestampSchema),
    live_trading_enabled: z.boolean(),
  }),
});
export type DashboardSnapshot = z.infer<typeof snapshotSchema>;

const candleSchema = z.object({
  open_time: utcTimestampSchema,
  close_time: utcTimestampSchema,
  open: decimalStringSchema,
  high: decimalStringSchema,
  low: decimalStringSchema,
  close: decimalStringSchema,
  volume: decimalStringSchema,
});
const chartMarkerSchema = z.object({
  kind: z.string(),
  timestamp: utcTimestampSchema,
  price: decimalStringSchema,
  identity: z.string(),
  side: z.string(),
  label: z.string(),
});
export const chartSchema = z.object({
  source_id: z.string(),
  symbol: z.string(),
  timeframe: z.enum(["15m", "1h", "4h"]),
  start: utcTimestampSchema,
  end: utcTimestampSchema,
  limit: z.number().int().positive(),
  candles: z.array(candleSchema),
  integrity: z.object({
    start: utcTimestampSchema,
    end: utcTimestampSchema,
    is_clean: z.boolean(),
    rows_scanned: z.number().int().nonnegative(),
    issues: z.array(
      z.object({ kind: z.string(), message: z.string(), open_time: utcTimestampSchema.nullable() }),
    ),
  }),
  markers: z.array(chartMarkerSchema),
  overlays: z.object({
    ema_20: genericDashboardValueSchema,
    ema_50: genericDashboardValueSchema,
    vwap: genericDashboardValueSchema,
    key_levels: genericDashboardValueSchema,
  }),
});
export type DashboardChart = z.infer<typeof chartSchema>;
export type DashboardCandle = z.infer<typeof candleSchema>;

export const decisionIdentitySchema = z.object({
  source_id: z.string(),
  symbol: z.string(),
  observed_at: utcTimestampSchema,
  strategy_id: z.string(),
  strategy_version: z.string(),
});

const entryIntentIdentitySchema = z.object({
  decision_identity: decisionIdentitySchema,
  purpose: z.string(),
  schema_version: z.string(),
});
const paperOrderIdentitySchema = z.object({
  intent_identity: entryIntentIdentitySchema,
  simulation_schema_version: z.string(),
});
const exitIntentIdentitySchema = z.object({
  originating_entry_intent_identity: entryIntentIdentitySchema,
  trigger_observed_at: utcTimestampSchema,
  purpose: z.string(),
  schema_version: z.string(),
});

export const detailedTradeSchema = z.object({
  originating_decision_identity: decisionIdentitySchema,
  entry_intent_identity: entryIntentIdentitySchema,
  paper_order_identity: paperOrderIdentitySchema,
  exit_intent_identity: exitIntentIdentitySchema,
  source_id: z.string(),
  symbol: z.string(),
  strategy_id: z.string(),
  strategy_version: z.string(),
  entry_side: z.string(),
  exit_side: z.string(),
  quantity: decimalStringSchema,
  approved_notional_quote: decimalStringSchema,
  approved_risk_quote: decimalStringSchema,
  stop_basis: decimalStringSchema,
  approved_reference_price: decimalStringSchema,
  entry_authorized_at: utcTimestampSchema,
  entry_reference_price: decimalStringSchema,
  entry_fill_at: utcTimestampSchema,
  entry_fill_price: decimalStringSchema,
  entry_spread_bps: decimalStringSchema,
  entry_slippage_bps: decimalStringSchema,
  entry_fee_bps: decimalStringSchema,
  entry_fee_quote: decimalStringSchema,
  exit_reason: z.string(),
  trigger_observed_at: utcTimestampSchema,
  trigger_reference_price: decimalStringSchema,
  exit_authorized_at: utcTimestampSchema,
  exit_reference_price: decimalStringSchema,
  exit_fill_at: utcTimestampSchema,
  exit_fill_price: decimalStringSchema,
  exit_spread_bps: decimalStringSchema,
  exit_slippage_bps: decimalStringSchema,
  exit_fee_bps: decimalStringSchema,
  exit_fee_quote: decimalStringSchema,
  gross_realized_pnl: decimalStringSchema,
  total_trade_fees_quote: decimalStringSchema,
  net_realized_pnl: decimalStringSchema,
  realized_r_net: decimalStringSchema,
  duration: z.string(),
  risk_policy_version: z.string(),
  safety_policy_version: z.string(),
  accounting_entry_fill_id: z.string(),
  accounting_exit_fill_id: z.string(),
  outcome_classification: z.string(),
  mfe: decimalValueSchema,
  mae: decimalValueSchema,
  process_score: decimalValueSchema,
});
export type DetailedTrade = z.infer<typeof detailedTradeSchema>;

const strategyDecisionSchema = z.object({
  action: z.string(),
  reference_price: decimalStringSchema.nullable(),
  invalidation_reference_price: decimalStringSchema.nullable(),
  reasons_for: stringListValueSchema,
  reasons_against: stringListValueSchema,
  rule_audit: stringValueSchema,
  fundamental_context: stringValueSchema,
  experience_metrics: stringValueSchema,
  confidence: decimalValueSchema,
  target_price: decimalValueSchema,
  expected_reward_risk: decimalValueSchema,
});

const riskDecisionSchema = z.object({
  status: z.string(),
  reasons: z.array(z.string()),
  policy_version: z.string(),
  reference_price: decimalStringSchema.nullable(),
  approved_risk_stop_basis: decimalStringSchema.nullable(),
  stop_distance: decimalStringSchema.nullable(),
  quote_currency: z.string(),
  risk_snapshot_observed_at: utcTimestampSchema,
  account_equity_quote: decimalStringSchema,
  day_start_equity_quote: decimalStringSchema,
  peak_equity_quote: decimalStringSchema,
  daily_loss_quote: decimalStringSchema,
  daily_loss_fraction: decimalStringSchema,
  max_daily_loss_fraction: decimalStringSchema,
  drawdown_quote: decimalStringSchema,
  drawdown_fraction: decimalStringSchema,
  max_drawdown_fraction: decimalStringSchema,
  current_open_risk_quote: decimalStringSchema,
  simultaneous_risk_cap_quote: decimalStringSchema,
  remaining_simultaneous_risk_quote: decimalStringSchema,
  current_gross_exposure_quote: decimalStringSchema,
  gross_exposure_cap_quote: decimalStringSchema,
  remaining_gross_exposure_quote: decimalStringSchema,
  per_trade_risk_budget_quote: decimalStringSchema,
  approved_quantity: decimalStringSchema.nullable(),
  approved_notional_quote: decimalStringSchema.nullable(),
  approved_risk_quote: decimalStringSchema.nullable(),
  resulting_open_risk_quote: decimalStringSchema.nullable(),
  resulting_gross_exposure_quote: decimalStringSchema.nullable(),
  requires_halt_review: z.boolean(),
});

const safetyDecisionSchema = z.object({
  status: z.string(),
  reasons: z.array(z.string()),
  policy_version: z.string(),
  observed_at: utcTimestampSchema,
  health_source_id: z.string(),
  health_symbol: z.string(),
  health_observed_at: utcTimestampSchema,
  market_data_observed_at: utcTimestampSchema,
  market_data_age: z.string(),
  max_market_data_age_seconds: z.number().int(),
  feed_integrity_state: z.string(),
  exchange_api_health_state: z.string(),
  spread_bps: decimalStringSchema,
  max_spread_bps: decimalStringSchema,
  time_sync_offset_ms: decimalStringSchema,
  max_abs_time_sync_offset_ms: decimalStringSchema,
  volatility_safety_state: z.string(),
  suspicious_price_anomaly: z.boolean(),
  consecutive_order_rejections: z.number().int(),
  max_consecutive_order_rejections: z.number().int(),
  risk_requires_halt_review: z.boolean(),
  approved_risk_stop_basis: decimalStringSchema.nullable(),
  approved_quantity: decimalStringSchema.nullable(),
  approved_notional_quote: decimalStringSchema.nullable(),
  approved_risk_quote: decimalStringSchema.nullable(),
  entry_permitted: z.boolean(),
});

export const decisionRecordSchema = z.object({
  identity: decisionIdentitySchema,
  strategy: strategyDecisionSchema,
  risk: riskDecisionSchema,
  safety: safetyDecisionSchema,
  completed_trade: dashboardValueSchema(detailedTradeSchema),
});
export type DecisionRecord = z.infer<typeof decisionRecordSchema>;

const pageFields = {
  start: utcTimestampSchema,
  end: utcTimestampSchema,
  limit: z.number().int().positive(),
  total_matched: z.number().int().nonnegative(),
  returned_count: z.number().int().nonnegative(),
  truncated: z.boolean(),
};
export const decisionsPageSchema = z.object({ ...pageFields, records: z.array(decisionRecordSchema) });
export const tradesPageSchema = z.object({ ...pageFields, records: z.array(detailedTradeSchema) });
export type DecisionsPage = z.infer<typeof decisionsPageSchema>;
export type TradesPage = z.infer<typeof tradesPageSchema>;

export const experienceFingerprintSchema = z.object({
  symbol: z.string().min(1),
  strategy_id: z.string().min(1),
  strategy_version: z.string().min(1),
  entry_timeframe: z.literal("15m"),
  regime_label: z.string().min(1),
  direction: z.string().min(1),
  trend_strength_bucket: z.string().min(1),
  volatility_state: z.string().min(1),
  volume_state: z.string().min(1),
  higher_timeframe_bias: z.string().min(1),
  multi_timeframe_alignment: z.string().min(1),
  fingerprint_schema_version: z.string().min(1),
});
export type ExperienceFingerprint = z.infer<typeof experienceFingerprintSchema>;

const experienceLevelSchema = z.enum([
  "FULL_CONTEXT",
  "REGIME_HTF",
  "REGIME",
  "STRATEGY_SYMBOL",
  "STRATEGY",
]);
const experienceKeySchema = z.object({
  level: experienceLevelSchema,
  strategy_id: z.string().min(1),
  strategy_version: z.string().min(1),
  symbol: z.string().nullable(),
  entry_timeframe: z.string().nullable(),
  regime_label: z.string().nullable(),
  direction: z.string().nullable(),
  trend_strength_bucket: z.string().nullable(),
  volatility_state: z.string().nullable(),
  volume_state: z.string().nullable(),
  higher_timeframe_bias: z.string().nullable(),
  multi_timeframe_alignment: z.string().nullable(),
  key_schema_version: z.string().min(1),
});
const processLocalFields = {
  history_scope: z.literal("PROCESS_LOCAL"),
  history_limitation: z.string().min(1),
};

export const experienceContextsSchema = z
  .object({
    as_of: utcTimestampSchema,
    limit: z.number().int().min(1).max(200),
    total_matched: z.number().int().nonnegative(),
    returned_count: z.number().int().nonnegative(),
    truncated: z.boolean(),
    contexts: z.array(experienceFingerprintSchema),
    ...processLocalFields,
  })
  .superRefine((page, context) => {
    if (
      page.returned_count !== page.contexts.length ||
      page.returned_count > page.limit ||
      page.total_matched < page.returned_count ||
      page.truncated !== (page.total_matched > page.returned_count)
    ) {
      context.addIssue({ code: "custom", message: "inconsistent Experience context page" });
    }
  });
export type ExperienceContexts = z.infer<typeof experienceContextsSchema>;

const experienceEvidenceSchema = z.object({
  key: experienceKeySchema,
  raw_eligible_sample_count: z.number().int().nonnegative(),
  effective_sample_size: decimalStringSchema,
  sufficient: z.boolean(),
  hierarchy_evidence_schema_version: z.string().min(1),
});
const nullableDecimal = decimalStringSchema.nullable();
export const experienceEstimateSchema = z
  .object({
    target_fingerprint: experienceFingerprintSchema,
    as_of: utcTimestampSchema,
    availability: z.enum(["AVAILABLE", "INSUFFICIENT_EVIDENCE", "NO_HISTORY"]),
    selected_key: experienceKeySchema.nullable(),
    selected_level: experienceLevelSchema.nullable(),
    confidence: z.enum(["NO_HISTORY", "INSUFFICIENT", "LOW", "MEDIUM", "HIGH"]),
    examined_hierarchy: z.array(experienceEvidenceSchema).length(5),
    eligible_selected_raw_sample_count: z.number().int().nonnegative(),
    selected_effective_sample_size: decimalStringSchema,
    excluded_future_sample_count: z.number().int().nonnegative(),
    prior_source: z.enum(["NEUTRAL_PRIOR", "HIERARCHICAL"]).nullable(),
    prior_source_level: experienceLevelSchema.nullable(),
    prior_source_key: experienceKeySchema.nullable(),
    prior_external_effective_sample_size: nullableDecimal,
    prior_alpha: nullableDecimal,
    prior_beta: nullableDecimal,
    posterior_alpha: nullableDecimal,
    posterior_beta: nullableDecimal,
    posterior_mean_win_probability: nullableDecimal,
    recency_weighted_win_rate: nullableDecimal,
    recency_weighted_expectancy_r: nullableDecimal,
    quote_currency: z.string().min(1).nullable(),
    recency_weighted_expectancy_net_quote: nullableDecimal,
    estimation_policy_version: z.string().min(1),
    recency_policy_version: z.string().min(1),
    estimate_schema_version: z.string().min(1),
    ...processLocalFields,
  })
  .superRefine((estimate, context) => {
    const levels = estimate.examined_hierarchy.map((row) => row.key.level);
    const canonical = ["FULL_CONTEXT", "REGIME_HTF", "REGIME", "STRATEGY_SYMBOL", "STRATEGY"];
    if (levels.some((level, index) => level !== canonical[index])) {
      context.addIssue({ code: "custom", message: "non-canonical Experience hierarchy" });
    }
    if (estimate.selected_key?.level !== estimate.selected_level) {
      context.addIssue({ code: "custom", message: "inconsistent selected Experience level" });
    }
    const unavailable = [
      estimate.selected_key,
      estimate.selected_level,
      estimate.prior_source,
      estimate.prior_alpha,
      estimate.prior_beta,
      estimate.posterior_alpha,
      estimate.posterior_beta,
      estimate.posterior_mean_win_probability,
      estimate.recency_weighted_win_rate,
      estimate.recency_weighted_expectancy_r,
      estimate.quote_currency,
      estimate.recency_weighted_expectancy_net_quote,
    ];
    if (
      estimate.availability === "NO_HISTORY" &&
      (estimate.confidence !== "NO_HISTORY" || unavailable.some((value) => value !== null))
    ) {
      context.addIssue({ code: "custom", message: "inconsistent NO_HISTORY state" });
    }
  });
export type ExperienceEstimate = z.infer<typeof experienceEstimateSchema>;
