// API 层：经 bridge 调后端 REST，统一 extractData 解包 {success,data} 信封。
// endpoint 不带前导斜杠、不带插件名（bridge 自动补 /api/plug/<pluginName>/ 前缀）。

import { getBridge } from "./bridge";
import type {
  AlertItem,
  AiDiagCached,
  AiDiagResult,
  AiProviderInfo,
  AttributionResponse,
  Bucket,
  BudgetResponse,
  CacheResponse,
  CompareResult,
  OverviewReport,
  PricingResponse,
  Provider,
  RecordRow,
  RecordsAggregate,
  TimelineResponse,
  Window,
} from "./types";

export class ApiError extends Error {}

// 后端用非标准 {success, data}；父级 SPA 对它原样透传，前端自行解包
export function extractData<T>(response: unknown): T {
  if (response && typeof response === "object") {
    const r = response as { success?: boolean; data?: unknown; error?: string };
    if (r.success === true) return r.data as T;
    if (r.success === false) throw new ApiError(r.error || "请求失败");
  }
  return response as T;
}

async function get<T>(endpoint: string, params?: Record<string, unknown>): Promise<T> {
  const bridge = getBridge();
  if (!bridge) throw new ApiError("Bridge SDK 未就绪");
  return extractData<T>(await bridge.apiGet(endpoint, params ?? {}));
}

async function post<T>(endpoint: string, body?: unknown): Promise<T> {
  const bridge = getBridge();
  if (!bridge) throw new ApiError("Bridge SDK 未就绪");
  return extractData<T>(await bridge.apiPost(endpoint, body));
}

export const api = {
  // overview
  getOverview: (window: Window) => get<OverviewReport>("overview", { window }),
  getAlerts: (window: Window) => get<AlertItem[]>("alerts", { window }),
  getCompare: (window: Window) => get<CompareResult | null>("compare", { window }),
  getTimeline: (
    days: number,
    bucket: Bucket = "day",
    extra?: Record<string, unknown>,
  ) => get<TimelineResponse>("timeline", { days, bucket, ...extra }),

  // records
  getRecords: (filter: Record<string, unknown>) => get<RecordRow[]>("records", filter),
  getRecordsAggregate: (params: Record<string, unknown>) =>
    get<RecordsAggregate>("records/aggregate", params),

  // budgets
  getBudgets: () => get<BudgetResponse>("budgets"),
  getProviders: () => get<{ providers: Provider[] }>("providers"),

  // cache / attribution / pricing / config
  getCache: (window: Window, limit?: number) =>
    get<CacheResponse>("cache", limit != null ? { window, limit } : { window }),
  getAttribution: (window: Window, limit?: number) =>
    get<AttributionResponse>(
      "attribution",
      limit != null ? { window, limit } : { window },
    ),
  getPricing: () => get<PricingResponse>("pricing"),
  getConfig: () => get<Record<string, unknown>>("config"),

  // actions
  postCleanup: () => post<{ deleted: number; message?: string }>("actions/cleanup"),
  postPurge: (modules: string[]) =>
    post<{ results: Record<string, number> }>("actions/purge", {
      modules,
    }),
  postReport: () => post<{ message: string }>("actions/report"),
  postSaveConfig: (body: unknown) =>
    post<{ saved: string[]; config: Record<string, unknown> }>(
      "actions/save_config",
      body,
    ),
  postSyncRates: () =>
    post<{
      exchange_rates: Record<string, number>;
      exchange_rates_updated_at: string;
      count: number;
    }>("actions/sync_rates"),

  // AI 诊断
  getAiProvider: () => get<AiProviderInfo>("ai_provider"),
  getAiDiagLast: () => get<AiDiagCached>("ai_diag_last"),
  postAiDiag: () => post<AiDiagResult>("ai_diag"),
};
