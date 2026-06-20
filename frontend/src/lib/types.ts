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
}

export interface BudgetMetric {
  limit?: number;
  used?: number;
  ratio?: number;
  exceeded?: boolean;
  top_key?: string;
  note?: string;
}

export interface BudgetDimension {
  token?: BudgetMetric;
  cost?: BudgetMetric;
}

export type BudgetMetricKey = "token" | "cost";

export interface BudgetResponse {
  limits?: Record<string, number>;
  limits_cost?: Record<string, number>;
  strategies?: RawStrategy[];
  dimensions?: Record<string, BudgetDimension>;
}

export interface RawStrategy {
  action?: string;
  provider_ids?: string[];
  token_limit?: number;
  message?: string;
  enabled?: boolean;
}

export interface Strategy {
  action: string;
  provider_ids: string[];
  token_limit: number;
  message: string;
  enabled: boolean;
}

// ===== cache =====
export interface CacheEventState {
  history_len?: number;
  system_hash?: string;
  tools_hash?: string;
  first_diverge_at?: number;
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
export interface PriceEntry {
  input?: number;
  input_cached?: number;
  output?: number;
  cache_creation?: number;
}

export interface PricingResponse {
  pricing?: Record<string, PriceEntry>;
  unpriced?: { model: string; tokens: number; count: number }[];
  defaults?: Record<string, PriceEntry>;
}
