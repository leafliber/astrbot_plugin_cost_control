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

// ===== alerts =====
// 总览页顶部黄色告警条目，引导用户跳转对应页处理
export type AlertTab = "cache" | "pricing" | "budgets";

export interface AlertItem {
  level: "warn";
  code: string;
  title: string;
  detail: string;
  tab: AlertTab;
}

export interface TimelinePoint {
  bucket: string;
  count: number;
  token_input_other?: number;
  token_input_cached?: number;
  token_output?: number;
}

export interface CostTimelinePoint {
  bucket: string;
  cost: number;
}

export interface TimelineResponse {
  series: TimelinePoint[];
  cost_series?: CostTimelinePoint[];
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
  cost?: number | null;
  cost_error?: string | null;
  cost_original?: number;
  currency_symbol?: string;
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
  cost: { used: number; limit: number; ratio: number; exceeded: boolean };
}

export interface BudgetOverride {
  id?: string;
  enabled: boolean;
  target_type: OverrideTarget;
  target_value: string;
  token_limit: number;
  cost_limit: number;
  cost_currency?: string;
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
  limits_cost_main?: Record<string, number>;
  limits_cost_currency?: Record<string, string>;
  currency_symbol?: string;
  exchange_rates?: Record<string, number>;
  exchange_rates_updated_at?: string;
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
  tools_text?: string;
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
  cache_note?: string;
}

// ===== attribution =====
export interface AttributionComponents {
  system?: number;
  tools?: number;
  history?: number;
  user?: number;
  extra?: number;
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
  estimation_note?: string;
}

// ===== pricing =====
// 旧 PriceEntry（内置默认表 defaults 用，per_token 四字段）
export interface PriceEntry {
  input?: number;
  input_cached?: number;
  output?: number;
  cache_creation?: number;
}

export type PricingMode =
  | "per_token"
  | "per_turn"
  | "per_request"
  | "per_tier"
  | "tiered_expr";

// 用户自定义定价 entry（key=provider_id），按 mode 区分字段
export interface PerTokenEntry {
  mode: "per_token";
  input?: number | null;
  input_cached?: number | null;
  output?: number | null;
  cache_creation?: number | null;
  currency?: string;
}
export interface PerTurnEntry {
  mode: "per_turn";
  price: number;
  currency?: string;
}
export interface PerRequestEntry {
  mode: "per_request";
  price: number;
  currency?: string;
}

// 结构化阶梯价：base + 可选 context_tier / service_tier（F3）
export interface ContextTierRule {
  threshold_tokens: number;
  input?: number | null;
  input_cached?: number | null;
  output?: number | null;
  cache_creation?: number | null;
}
export interface ServiceTierRule {
  match: string; // 匹配的 service_tier 值，如 priority / flex
  input_multiplier?: number;
  input_cached_multiplier?: number;
  output_multiplier?: number;
  cache_creation_multiplier?: number;
}
export interface PerTierEntry {
  mode: "per_tier";
  currency?: string;
  base: {
    input?: number | null;
    input_cached?: number | null;
    output?: number | null;
    cache_creation?: number | null;
  };
  context_tiers: ContextTierRule[];
  service_tiers: ServiceTierRule[];
}

// New API 兼容表达式动态计费（F3）
export interface TieredExprEntry {
  mode: "tiered_expr";
  currency?: string;
  expr: string;
  // 来自 New API 候选时只读锁定（8.6），可解锁编辑
  locked_source?: string;
}

export type UserPricingEntry =
  | PerTokenEntry
  | PerTurnEntry
  | PerRequestEntry
  | PerTierEntry
  | TieredExprEntry;
// provider 实际匹配到的内置默认（主模型经 _best_match_key 模糊匹配，与后端计费同口径）
export interface MatchedDefault {
  model: string;
  entry: PriceEntry;
}

// provider 及其候选模型（GET /providers / GET /pricing.provider_models）
export interface ProviderModelInfo {
  id: string;
  model?: string;
  type?: string;
  candidates: string[];
  supplier_id?: string;
  supplier_name?: string;
  matched_default?: MatchedDefault | null;
}

// 已从 AstrBot 配置删除、但仍有历史用量或自定义定价的 Provider 残留
export interface DeletedProviderInfo {
  provider_id: string;
  tokens: number;
  count: number;
  has_pricing?: boolean;
  models?: string[];
  matched_default?: MatchedDefault | null;
}

// 按 provider_source_id 聚类的供应商目录（上游聚类布局）
export interface PricingCluster {
  id: string;
  name: string;
  provider_ids: string[];
}

// POST /actions/delete_provider_data 结果
export interface DeleteProviderDataResult {
  provider_id: string;
  usage_deleted: number;
  supplements_deleted: number;
  pricing_deleted: boolean;
}

export interface PricingUnpriced {
  provider_id?: string;
  model: string;
  tokens: number;
  count: number;
}

// ===== 多源价格目录（F1/F2）=====

// 目录中一条标准化价格（$ / 1M tokens，或 per_turn 时 $ / 次）
export interface CatalogPrice {
  source: string;
  source_model_id: string;
  mode: PricingMode;
  prompt?: number | null;
  completion?: number | null;
  cache?: number | null;
  cache_read?: number | null;
  cache_creation?: number | null;
  price?: number | null;
  configured?: Record<string, boolean>;
  context_tiers?: Record<string, unknown>[];
  service_tiers?: Record<string, unknown>[];
  expr?: string | null;
  fetched_at?: string;
}

// 单个源的同步状态
export interface SourceStatus {
  source: string;
  enabled: boolean;
  status: "ok" | "error" | "pending";
  updated_at?: string;
  models: number;
  skipped?: number;
  error?: string;
  etag?: string;
  provider_id?: string | null;
  base_url?: string | null;
}

// 候选条目（后端 _candidate_brief 的紧凑形状）
export interface CandidateBrief {
  price_key: string;
  source: string;
  source_model_id: string;
  score: number;
  reason: string;
  mode: PricingMode;
  prompt?: number | null;
  completion?: number | null;
  cache_read?: number | null;
  cache_creation?: number | null;
  price?: number | null;
  context_tiers: number; // tier 数量（brief 只给计数）
  service_tiers: number;
  expr?: string;
}

// 用户对某 (provider, model) 的已确认/自动选择
export interface PriceSelection {
  price_key: string;
  source: string;
  source_model_id: string;
  confirmed: boolean;
  auto: boolean;
  score: number;
  reason: string;
  /** 生效价格摘要（后端从 catalog 附带），供输入框回显 */
  price?: {
    input: number | null;
    input_cached: number | null;
    output: number | null;
    cache_creation: number | null;
  };
}

// POST /pricing/sync 报告
export interface SyncSourceResult {
  source: string;
  status: "ok" | "error";
  models: number;
  skipped: number;
  error?: string;
  not_modified: boolean;
}

export interface SyncReport {
  ok: boolean;
  updated_at?: string;
  results: SyncSourceResult[];
}

// POST /pricing/sources/detect 结果
export interface DetectResult {
  provider_id: string;
  base_url: string;
  is_newapi: boolean;
  models: number;
  needs_key: boolean;
  error?: string;
  /** 同 base_url 已有的源 id（URL 去重判据），前端直接复用 */
  existing_source?: string | null;
}

// POST /pricing/expr/validate 结果
export interface ExprSample {
  p: number;
  c: number;
  usd: number;
  tier: string;
}
export interface ExprValidateResult {
  valid: boolean;
  error?: string;
  samples?: ExprSample[];
}

export interface PricingResponse {
  provider_models?: ProviderModelInfo[];
  deleted_providers?: DeletedProviderInfo[];
  user_pricing?: Record<string, UserPricingEntry>; // key=provider_id
  defaults?: Record<string, PriceEntry>; // key=模型名，per_token
  pricing_clusters?: PricingCluster[];
  pricing_multipliers?: Record<string, number>; // key=provider_source_id，缺省=1
  unpriced?: PricingUnpriced[];
  currency_symbol?: string;
  exchange_rates?: Record<string, number>;
  exchange_rates_updated_at?: string;
  // 多源目录（F1/F2）
  sources?: Record<string, SourceStatus>;
  selections?: Record<string, Record<string, PriceSelection>>;
  candidates?: Record<string, Record<string, CandidateBrief[]>>;
  auto_selected?: Record<string, Record<string, string>>; // model → price_key
}

// ===== AI 诊断 =====
export interface AiProviderInfo {
  provider_id: string | null;
  provider_name: string | null;
  available: boolean;
  providers?: { id: string; name: string }[];
  error?: string;
}

export interface AiDiagRisk {
  module: string;
  level: "high" | "medium" | "low" | "info";
  issue: string;
  advice: string;
}

export interface AiDiagConclusion {
  overall?: string;
  overall_score?: number;
  highlights?: string[];
  risks?: AiDiagRisk[];
  summary?: string;
}

export interface AiDiagResult {
  timestamp?: number;
  provider_id?: string | null;
  provider_name?: string | null;
  conclusion?: AiDiagConclusion | null;
  raw_text?: string | null;
  error?: string | null;
}

export interface AiDiagCached {
  result?: AiDiagResult | null;
  timestamp?: number;
  age_seconds?: number;
  stale?: boolean;
}
