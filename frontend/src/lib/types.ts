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
