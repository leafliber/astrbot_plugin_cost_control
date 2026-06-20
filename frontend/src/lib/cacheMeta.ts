import type { CacheEvent, CacheEventState } from "./types";

// 缓存破坏事件类型元数据：标题 + 处置建议
export const CACHE_EVENT_META: Record<string, { title: string; tip: string }> = {
  context_reset: {
    title: "上下文重置",
    tip: "检查上下文是否被截断 / 会话被重置 / 历史被清空",
  },
  system_prompt_change: {
    title: "system 变更",
    tip: "稳定 system prompt，避免每轮改动前缀导致缓存失效",
  },
  tools_change: {
    title: "工具定义变更",
    tip: "保持 func_tool 集合稳定，避免增删工具破坏缓存键",
  },
  order_drift: {
    title: "顺序漂移",
    tip: "避免重排或改写历史消息，保持追加式增长",
  },
};

export function cacheEvtMeta(t?: string): { title: string; tip: string } {
  return CACHE_EVENT_META[t || ""] || { title: t || "?", tip: "" };
}

function nz(v: unknown): string {
  return v != null ? String(v) : "-";
}

export function cacheDiffText(
  type: string,
  b?: CacheEventState,
  a?: CacheEventState,
): string {
  if (type === "context_reset") return `历史 ${nz(b?.history_len)} → ${nz(a?.history_len)}`;
  if (type === "system_prompt_change")
    return `system ${b?.system_hash || "-"} → ${a?.system_hash || "-"}`;
  if (type === "tools_change")
    return `tools ${b?.tools_hash || "-"} → ${a?.tools_hash || "-"}`;
  if (type === "order_drift") return `首个分歧 #${nz(a?.first_diverge_at)}`;
  return "";
}

export interface DiffRow {
  label: string;
  before: string;
  after: string;
  changed: boolean;
}

export interface CacheDetail {
  rows: DiffRow[];
  firstDiv?: number;
  tip: string;
  detail: string;
}

export function cacheDetailRows(ev: CacheEvent): CacheDetail {
  const b = ev.before || {};
  const a = ev.after || {};
  const row = (label: string, bv: unknown, av: unknown): DiffRow => ({
    label,
    before: nz(bv),
    after: nz(av),
    changed: String(bv) !== String(av),
  });
  const rows: DiffRow[] = [
    row("历史长度", b.history_len, a.history_len),
    row("system hash", b.system_hash, a.system_hash),
    row("tools hash", b.tools_hash, a.tools_hash),
  ];
  const firstDiv =
    ev.type === "order_drift" && a.first_diverge_at != null
      ? a.first_diverge_at
      : undefined;
  return { rows, firstDiv, tip: cacheEvtMeta(ev.type).tip, detail: ev.detail || "" };
}
