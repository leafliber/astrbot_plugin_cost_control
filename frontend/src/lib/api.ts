// API 层：经 bridge 调后端 REST，统一 extractData 解包 {success,data} 信封。
// endpoint 不带前导斜杠、不带插件名（bridge 自动补 /api/plug/<pluginName>/ 前缀）。

import { getBridge } from "./bridge";
import type {
  Bucket,
  CompareResult,
  OverviewReport,
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
  // 阶段 1（overview）
  getOverview: (window: Window) => get<OverviewReport>("overview", { window }),
  getCompare: (window: Window) => get<CompareResult | null>("compare", { window }),
  getTimeline: (
    days: number,
    bucket: Bucket = "day",
    extra?: Record<string, unknown>,
  ) => get<TimelineResponse>("timeline", { days, bucket, ...extra }),

  // 通用 action（后续阶段视图复用）
  postCleanup: () => post<{ deleted: number; message?: string }>("actions/cleanup"),
  postReport: () => post<{ message: string }>("actions/report"),
  postSaveConfig: (body: unknown) =>
    post<{ saved: string[]; config: Record<string, unknown> }>(
      "actions/save_config",
      body,
    ),
};
