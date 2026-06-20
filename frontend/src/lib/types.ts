// 后端响应类型。字段以 web_api.py 实际返回为准，宽松可选以兼容后端微调。

export type Window = "daily" | "weekly" | "monthly";
export type Bucket = "day" | "hour";

export interface Usage {
  count?: number;
  token_input_other?: number;
  token_input_cached?: number;
  token_output?: number;
}

export interface CostByModel {
  model: string;
  cost: number;
  tokens?: number;
  count?: number;
}

export interface TopSession {
  umo: string;
  tokens: number;
  cost?: number;
}

export interface OverviewReport {
  cost?: number;
  usage?: Usage;
  cache_hit_rate?: number;
  cache_samples?: number;
  avg_injection?: number;
  injection_samples?: number;
  cost_by_model?: CostByModel[];
  top_sessions?: TopSession[];
  top_sessions_by_cost?: TopSession[];
}

export interface TimelinePoint {
  bucket: string;
  count: number;
  token_input_other?: number;
  token_input_cached?: number;
  token_output?: number;
}

export interface TimelineResponse {
  series: TimelinePoint[];
  bucket: Bucket;
  days: number;
  coverage_note?: string;
}

export interface CompareMetrics {
  cost: number;
  count: number;
  tokens: number;
}

export interface CompareResult {
  window: Window;
  current: CompareMetrics;
  previous: CompareMetrics;
  delta: {
    cost_pct: number | null;
    count_pct: number | null;
    tokens_pct: number | null;
  };
  label: string;
}

// ===== records =====
export interface RecordRow {
  umo?: string;
  provider_id?: string;
  provider_model?: string;
  conversation_id?: string;
  token_input_other?: number;
  token_input_cached?: number;
  token_output?: number;
  cache_creation?: number;
  cache_read?: number;
  injection_total?: number | null;
  cost?: number;
  created_at?: string;
}

export interface RecordsAggregateGroup {
  key: string;
  count: number;
  tokens: number;
  cost: number;
  pct: number;
}

export interface RecordsAggregate {
  by: string;
  total_tokens: number;
  groups: RecordsAggregateGroup[];
}

export type RecordsPreset = "today" | "7d" | "30d" | "custom";
export type RecordsOrderBy = "created_at" | "token_input_other" | "token_output" | "umo";
export type RecordsOrderDir = "asc" | "desc";

export interface RecordsFilter {
  preset: RecordsPreset;
  start: string;
  end: string;
  model: string;
  umo: string;
  provider: string;
  order_by: RecordsOrderBy;
  order_dir: RecordsOrderDir;
}

// ===== budgets =====
export interface Provider {
  id: string;
  model?: string;
  type?: string;
  candidates?: string[];
}

export interface MetricProgress {
  limit: number;
  used: number;
  ratio: number;
  exceeded: boolean;
  top_key?: string;
  note?: string;
}

export interface BudgetDimension {
  token: MetricProgress;
  cost: MetricProgress;
}

export type Metric = "token" | "cost";
export type OverrideTarget = "umo" | "provider" | "user";
export type OnExceeded = "stop" | "fallback" | "warn";

export interface OverrideCurrent {
  token: { used: number; ratio: number; exceeded: boolean };
  cost: { used: number; ratio: number; exceeded: boolean };
}

export interface BudgetOverride {
  id?: string;
  enabled: boolean;
  target_type: OverrideTarget;
  target_value: string;
  token_limit: number;
  cost_limit: number;
  on_exceeded: OnExceeded;
  stop_message?: string;
  fallback_provider_ids: string[];
  fallback_token_limit: number;
}

export interface BudgetOverrideRow extends BudgetOverride {
  current: OverrideCurrent;
}

export interface FallbackProvider {
  id: string;
  enabled: boolean;
  note?: string;
}

export interface BudgetResponse {
  limits?: Record<string, number>;
  limits_cost?: Record<string, number>;
  dimensions?: Record<string, BudgetDimension>;
  overrides?: BudgetOverrideRow[];
  fallback_providers?: FallbackProvider[];
  global_default_on_exceeded?: OnExceeded;
}

// ===== cache =====
export interface DiffLine {
  op: "+" | "-" | " ";
  text: string;
}

export interface CacheEventState {
  history_len?: number;
  system_hash?: string;
  tools_hash?: string;
  first_diverge_at?: number;
  system_diff?: DiffLine[];
  tools_diff?: DiffLine[];
}

export interface CacheEvent {
  umo?: string;
  type?: string;
  severity?: string;
  detail?: string;
  before?: CacheEventState;
  after?: CacheEventState;
  created_at?: string;
}

export interface CacheResponse {
  cache_hit_rate?: number;
  samples?: number;
  total_input_other?: number;
  total_input_cached?: number;
  total_output?: number;
  events?: CacheEvent[];
}

// ===== attribution =====
export interface AttributionComponents {
  system?: number;
  tools?: number;
  history?: number;
  user?: number;
}

export interface AttributionItem {
  umo?: string;
  injection_total?: number | null;
  attribution?: AttributionComponents;
  created_at?: string;
}

export interface AttributionResponse {
  recent?: AttributionItem[];
  avg_components?: AttributionComponents;
}

// ===== pricing =====
// 旧 PriceEntry（内置默认表 defaults 用，per_token 四字段）
export interface PriceEntry {
  input?: number;
  input_cached?: number;
  output?: number;
  cache_creation?: number;
}

export type PricingMode = "per_token" | "per_turn" | "per_request";

// 用户自定义定价 entry（key=provider_id），按 mode 区分字段
export interface PerTokenEntry {
  mode: "per_token";
  input: number;
  input_cached: number;
  output: number;
  cache_creation?: number | null;
}
export interface PerTurnEntry {
  mode: "per_turn";
  price: number;
}
export interface PerRequestEntry {
  mode: "per_request";
  price: number;
}
export type UserPricingEntry = PerTokenEntry | PerTurnEntry | PerRequestEntry;

// provider 及其候选模型（GET /providers / GET /pricing.provider_models）
export interface ProviderModelInfo {
  id: string;
  model?: string;
  type?: string;
  candidates: string[];
}

export interface PricingUnpriced {
  provider_id?: string;
  model: string;
  tokens: number;
  count: number;
}

export interface PricingResponse {
  provider_models?: ProviderModelInfo[];
  user_pricing?: Record<string, UserPricingEntry>; // key=provider_id
  defaults?: Record<string, PriceEntry>; // key=模型名，per_token
  unpriced?: PricingUnpriced[];
}
