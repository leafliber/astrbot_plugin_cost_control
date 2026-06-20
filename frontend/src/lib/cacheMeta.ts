import type { CacheEvent, CacheEventState, DiffLine } from "./types";

// 折叠阈值：连续未变更的 context 行数大于此值时折叠为一段
const CONTEXT_COLLAPSE_THRESHOLD = 3;

export interface CollapsedSegment {
  kind: "lines";
  lines: DiffLine[];
}

export interface CollapsedPlaceholder {
  kind: "placeholder";
  count: number;
  segments: CollapsedSegment[]; // 折叠时被隐藏的原始段，点击展开后渲染
}

export type CollapsedLine = DiffLine | CollapsedPlaceholder;

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

export interface DiffPayload {
  label: string;
  segments: CollapsedSegment[];
  // 初始折叠状态：true 表示所有长 context 段默认收起
  initiallyCollapsed: boolean;
}

export interface CacheDetail {
  rows: DiffRow[];
  firstDiv?: number;
  tip: string;
  detail: string;
  diff?: DiffPayload;
}

// 将连续的 context 行合并为一个段，便于折叠
function collapseContext(
  lines: DiffLine[],
  threshold: number,
): CollapsedSegment[] {
  const segments: CollapsedSegment[] = [];
  let buf: DiffLine[] = [];
  const flush = () => {
    if (buf.length === 0) return;
    segments.push({ kind: "lines", lines: buf });
    buf = [];
  };
  for (const l of lines) {
    if (l.op === " ") {
      buf.push(l);
    } else {
      flush();
      segments.push({ kind: "lines", lines: [l] });
    }
  }
  flush();
  // 仅折叠长度超过阈值的纯 context 段；首尾 context 段保留原样以提供上下文
  return segments.map((s) => {
    if (s.lines.every((l) => l.op === " ") && s.lines.length > threshold) {
      return s; // 由 buildCollapsedView 决定是否折叠
    }
    return s;
  });
}

// 把 segments 中过长的 context 段折叠为 placeholder，返回 CollapsedLine[]
export function buildCollapsedView(
  segments: CollapsedSegment[],
  collapsed: boolean,
): CollapsedLine[] {
  const out: CollapsedLine[] = [];
  segments.forEach((s, idx) => {
    const isLongContext =
      s.lines.every((l) => l.op === " ") && s.lines.length > CONTEXT_COLLAPSE_THRESHOLD;
    // 收尾段保留展开：避免用户失去变更的上下文边界
    const isBoundary = idx === 0 || idx === segments.length - 1;
    if (collapsed && isLongContext && !isBoundary) {
      out.push({ kind: "placeholder", count: s.lines.length, segments: [s] });
    } else {
      out.push(...s.lines);
    }
  });
  return out;
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
  // 内容级 diff（后端 _line_diff 产出，仅 system/tools 变更事件有）
  const sysLines = (a.system_diff || []).filter((l) => l && l.op);
  const toolLines = (a.tools_diff || []).filter((l) => l && l.op);
  const rawLines = sysLines.length > 0 ? sysLines : toolLines.length > 0 ? toolLines : [];
  if (rawLines.length === 0) {
    return { rows, firstDiv, tip: cacheEvtMeta(ev.type).tip, detail: ev.detail || "" };
  }
  const label = sysLines.length > 0 ? "system prompt 变更" : "工具定义变更";
  const segments = collapseContext(rawLines, CONTEXT_COLLAPSE_THRESHOLD);
  return {
    rows,
    firstDiv,
    tip: cacheEvtMeta(ev.type).tip,
    detail: ev.detail || "",
    diff: { label, segments, initiallyCollapsed: true },
  };
}
