import { useEffect, useRef, useState, type ReactNode } from "react";
import { Segmented } from "./Segmented";
import type {
  MatchedDefault,
  PerRequestEntry,
  PerTierEntry,
  ContextTierRule,
  ServiceTierRule,
  PerTokenEntry,
  PerTurnEntry,
  PriceEntry,
  PricingMode,
  TieredExprEntry,
  UserPricingEntry,
} from "../lib/types";
import type { CandidateBrief, PriceSelection } from "../lib/types";
import { CURRENCY_OPTIONS, currencyToSymbol } from "../lib/format";
import {
  PerTierEditor,
  emptyServiceTierDraft,
  emptyTierPriceDraft,
} from "./price/PerTierEditor";
import { TieredExprEditor } from "./price/TieredExprEditor";
import { CandidateTable } from "./price/CandidateTable";
import { sourceLabel } from "./price/SourceStatusBar";

// context_tiers 编辑态：阈值 + 四价（字符串便于留空；留空字段 = 沿用上一阶/基础价）
export interface TierPriceDraft {
  threshold: string;
  input: string;
  input_cached: string;
  output: string;
  cache_creation: string;
}

// service_tiers 编辑态：匹配值 + 各字段倍率（留空 = 1）
export interface ServiceTierDraft {
  match: string;
  input_multiplier: string;
  input_cached_multiplier: string;
  output_multiplier: string;
  cache_creation_multiplier: string;
}

// 编辑中的临时态：mode + 该 mode 下所有可能字段（字符串形式便于空值处理）。
// currency: "" = USD（内部定价 USD 基准）；其它代码表示该 provider 以该货币计价，结算时换算到主货币。
export interface DraftEntry {
  mode: PricingMode;
  input: string;
  input_cached: string;
  output: string;
  cache_creation: string;
  price: string;
  currency: string;
  contextTiers: TierPriceDraft[]; // per_tier：上下文阶梯
  serviceTiers: ServiceTierDraft[]; // per_tier：服务等级倍率
  expr: string; // tiered_expr：New API 兼容计费表达式
}

const TOKEN_FIELDS: { key: keyof DraftEntry; label: string }[] = [
  { key: "input", label: "输入" },
  { key: "input_cached", label: "缓存命中" },
  { key: "output", label: "输出" },
  { key: "cache_creation", label: "缓存写入" },
];

const fmtStr = (v: number | null | undefined): string => (v == null ? "" : String(v));

// 新建草稿的默认计价货币：跟随主货币；USD 归一为 ""（内部基准，不落库 currency 字段），
// 不在受支持列表的代码回退 ""，避免下拉框出现无法命中的值。
export function normalizeDefaultCurrency(code?: string | null): string {
  const c = String(code || "").trim().toUpperCase();
  return c && c !== "USD" && CURRENCY_OPTIONS.includes(c) ? c : "";
}

export function entryToDraft(
  entry?: UserPricingEntry,
  defaultCurrency?: string,
): DraftEntry {
  const d: DraftEntry = {
    mode: entry?.mode ?? "per_token",
    input: "",
    input_cached: "",
    output: "",
    cache_creation: "",
    price: "",
    // 已保存条目：currency 缺省即 USD 基准价，保持 USD 展示；新建草稿跟随主货币
    currency: entry ? (entry.currency ?? "") : normalizeDefaultCurrency(defaultCurrency),
    contextTiers: [],
    serviceTiers: [],
    expr: "",
  };
  if (!entry) return d;
  if (entry.mode === "per_token") {
    d.input = fmtStr(entry.input);
    d.input_cached = fmtStr(entry.input_cached);
    d.output = fmtStr(entry.output);
    d.cache_creation = fmtStr(entry.cache_creation);
  } else if (entry.mode === "per_tier") {
    const t = entry as PerTierEntry;
    d.input = fmtStr(t.base?.input);
    d.input_cached = fmtStr(t.base?.input_cached);
    d.output = fmtStr(t.base?.output);
    d.cache_creation = fmtStr(t.base?.cache_creation);
    d.contextTiers = (t.context_tiers ?? []).map((x: ContextTierRule) => ({
      threshold: fmtStr(x.threshold_tokens),
      input: fmtStr(x.input),
      input_cached: fmtStr(x.input_cached),
      output: fmtStr(x.output),
      cache_creation: fmtStr(x.cache_creation),
    }));
    d.serviceTiers = (t.service_tiers ?? []).map((x: ServiceTierRule) => ({
      match: x.match ?? "",
      input_multiplier: fmtStr(x.input_multiplier),
      input_cached_multiplier: fmtStr(x.input_cached_multiplier),
      output_multiplier: fmtStr(x.output_multiplier),
      cache_creation_multiplier: fmtStr(x.cache_creation_multiplier),
    }));
  } else if (entry.mode === "tiered_expr") {
    d.expr = (entry as TieredExprEntry).expr ?? "";
  } else {
    d.price = fmtStr((entry as PerTurnEntry | PerRequestEntry).price);
  }
  return d;
}

// 判断 draft 是否为空（未填写任何有效字段）→ collect 时不写入该 provider
export function isDraftEmpty(d: DraftEntry): boolean {
  if (d.mode === "per_token") {
    return TOKEN_FIELDS.every((f) => (d[f.key] as string).trim() === "");
  }
  if (d.mode === "per_tier") {
    const baseEmpty = TOKEN_FIELDS.every((f) => (d[f.key] as string).trim() === "");
    const noTiers = d.contextTiers.every(
      (t) =>
        !t.threshold.trim() && !t.input.trim() && !t.input_cached.trim() &&
        !t.output.trim() && !t.cache_creation.trim(),
    );
    const noSvc = d.serviceTiers.every((s) => !s.match.trim());
    return baseEmpty && noTiers && noSvc;
  }
  if (d.mode === "tiered_expr") return d.expr.trim() === "";
  return d.price.trim() === "";
}

function parseNum(raw: string): number {
  const n = parseFloat(raw.trim());
  if (Number.isNaN(n) || n < 0) throw new Error("非法数值");
  return n;
}

export function draftToEntry(d: DraftEntry): UserPricingEntry | null {
  if (isDraftEmpty(d)) return null;
  if (d.mode === "per_token") {
    const e: PerTokenEntry = { mode: "per_token" };
    let any = false;
    const assign = (
      field: "input" | "input_cached" | "output" | "cache_creation",
      raw: string,
    ) => {
      if (raw.trim() === "") return;
      if (field === "cache_creation") {
        e.cache_creation = parseNum(raw);
      } else {
        e[field] = parseNum(raw);
      }
      any = true;
    };
    assign("input", d.input);
    assign("input_cached", d.input_cached);
    assign("output", d.output);
    assign("cache_creation", d.cache_creation);
    if (!any) return null;
    if (d.currency && d.currency !== "USD") e.currency = d.currency;
    return e;
  }
  if (d.mode === "per_tier") {
    const base: PerTierEntry["base"] = {};
    if (d.input.trim()) base.input = parseNum(d.input);
    if (d.input_cached.trim()) base.input_cached = parseNum(d.input_cached);
    if (d.output.trim()) base.output = parseNum(d.output);
    if (d.cache_creation.trim()) base.cache_creation = parseNum(d.cache_creation);
    const context_tiers: ContextTierRule[] = d.contextTiers
      .filter((t) => t.threshold.trim() !== "")
      .map((t) => {
        const tier: ContextTierRule = {
          threshold_tokens: Math.max(0, Math.floor(parseNum(t.threshold))),
        };
        if (t.input.trim()) tier.input = parseNum(t.input);
        if (t.input_cached.trim()) tier.input_cached = parseNum(t.input_cached);
        if (t.output.trim()) tier.output = parseNum(t.output);
        if (t.cache_creation.trim()) tier.cache_creation = parseNum(t.cache_creation);
        return tier;
      });
    const service_tiers: ServiceTierRule[] = d.serviceTiers
      .filter((s) => s.match.trim() !== "")
      .map((s) => {
        const st: ServiceTierRule = { match: s.match.trim() };
        const mult = (raw: string): number | undefined => {
          if (!raw.trim()) return undefined;
          const n = parseNum(raw);
          return n > 0 ? n : undefined;
        };
        const im = mult(s.input_multiplier);
        if (im !== undefined) st.input_multiplier = im;
        const icm = mult(s.input_cached_multiplier);
        if (icm !== undefined) st.input_cached_multiplier = icm;
        const om = mult(s.output_multiplier);
        if (om !== undefined) st.output_multiplier = om;
        const ccm = mult(s.cache_creation_multiplier);
        if (ccm !== undefined) st.cache_creation_multiplier = ccm;
        return st;
      });
    const e: PerTierEntry = { mode: "per_tier", base, context_tiers, service_tiers };
    if (d.currency && d.currency !== "USD") e.currency = d.currency;
    return e;
  }
  if (d.mode === "tiered_expr") {
    const e: TieredExprEntry = { mode: "tiered_expr", expr: d.expr.trim() };
    if (d.currency && d.currency !== "USD") e.currency = d.currency;
    return e;
  }
  const pe: PerTurnEntry | PerRequestEntry = {
    mode: d.mode,
    price: parseNum(d.price),
  } as PerTurnEntry | PerRequestEntry;
  if (d.currency && d.currency !== "USD") pe.currency = d.currency;
  return pe;
}

const MODE_OPTIONS: { value: PricingMode; label: string }[] = [
  { value: "per_token", label: "按 Token" },
  { value: "per_tier", label: "阶梯定价" },
  { value: "tiered_expr", label: "表达式" },
  { value: "per_turn", label: "按调用轮次" },
  { value: "per_request", label: "按请求次数" },
];

// 按当前选中货币生成计费提示（替换基准 "USD"）。
function modeHint(mode: PricingMode, code: string, symbol: string): string {
  const unit = `${symbol} (${code})`;
  if (mode === "per_token") {
    return `${unit} / 百万 token。留空 = 用内置默认价。输入即覆盖默认。`;
  }
  if (mode === "per_tier") {
    return `${unit} / 百万 token。基础价始终生效；总输入超阈值时改用该阶价格，服务等级命中时乘倍率。`;
  }
  if (mode === "tiered_expr") {
    return "New API 兼容表达式，系数 $ / 百万 token，结果折算 USD。保存前请验证。";
  }
  if (mode === "per_turn") {
    return `${unit} / 次。每次 LLM 调用（含 function-calling 每一步）固定费用。`;
  }
  return `${unit} / 次。每次用户请求固定费用（一次请求含多步调用只计一次）。`;
}

// 折叠态摘要：只显示定价信息，不显示匹配模型名
function collapsedSummary(
  draft: DraftEntry,
  matchedDefault: MatchedDefault | null,
  hasOverride: boolean,
  activePrice: PriceSelection["price"] | null,
): string {
  if (hasOverride) {
    if (draft.mode === "per_token" || draft.mode === "per_tier") {
      const parts: string[] = [];
      if (draft.input.trim()) parts.push(`输入 ${draft.input}`);
      if (draft.output.trim()) parts.push(`输出 ${draft.output}`);
      if (draft.mode === "per_tier" && draft.contextTiers.length > 0) {
        parts.push(`阶梯×${draft.contextTiers.length}`);
      }
      return parts.join(" / ") || "空";
    }
    if (draft.mode === "tiered_expr") return "表达式计价";
    return `${draft.price} ${draft.currency || "USD"} / ${
      draft.mode === "per_turn" ? "轮" : "次"
    }`;
  }
  // 已选/自动来源价优先于内置默认（与徽标优先级一致）
  if (activePrice) {
    const parts: string[] = [];
    if (activePrice.input != null) parts.push(`输入 ${activePrice.input}`);
    if (activePrice.output != null) parts.push(`输出 ${activePrice.output}`);
    const s = parts.join(" / ");
    if (s) return s;
  }
  if (matchedDefault) {
    const e = matchedDefault.entry;
    const parts: string[] = [];
    if (e.input != null) parts.push(`输入 ${e.input}`);
    if (e.output != null) parts.push(`输出 ${e.output}`);
    return parts.join(" / ") || "默认";
  }
  return "未定价";
}

export function ProviderPricingCard({
  providerId,
  type,
  candidates,
  draft,
  matchedDefault,
  hasUserOverride,
  isDeletedResidue,
  hasUsage,
  highlightSignal,
  selections,
  autoPriceKeys,
  priceCandidatesByModel,
  newApiSource,
  selectingKey,
  onSelectCandidate,
  onResetSelection,
  onEnableNewApiSource,
  onDisableNewApiSource,
  onChange,
  onClear,
  onDeleteData,
  expanded: expandedProp,
  onExpandedChange,
  timePricingEditor,
  timePeriodCount,
}: {
  providerId: string;
  type?: string;
  candidates: string[];
  draft: DraftEntry;
  matchedDefault?: MatchedDefault | null;
  hasUserOverride?: boolean;
  isDeletedResidue?: boolean;
  /** 该 provider 是否存在未定价用量（告警） */
  hasUsage?: boolean;
  /** 外部跳转信号：每次点击未定价告警时递增，触发脉冲动画 */
  highlightSignal?: number;
  /** 按模型保存的已确认来源价格（F2/F6.3） */
  selections?: Record<string, PriceSelection>;
  /** 按模型保存的唯一高置信自动匹配 price_key */
  autoPriceKeys?: Record<string, string>;
  /** 按模型分组的待选候选（F2/F6.4） */
  priceCandidatesByModel?: Record<string, CandidateBrief[]>;
  /** 该 provider 对应的 New API 源（F1.2/F6.2）；null=已探测非 New API 或未配置 */
  newApiSource?: { sourceId: string; enabled: boolean };
  /** 正在选取/取消的 (provider|model) 键，用于按模型级 loading */
  selectingKey?: string;
  onSelectCandidate?: (model: string, priceKey: string) => Promise<void>;
  onResetSelection?: (model: string) => Promise<void>;
  onEnableNewApiSource?: () => Promise<void>;
  onDisableNewApiSource?: () => Promise<void>;
  onChange: (patch: Partial<DraftEntry>) => void;
  onClear: () => void;
  /** 已删除供应商残留的删除按钮回调 */
  onDeleteData?: () => void;
  /** 受控展开态（父级记忆）；缺省时按未定价默认展开 */
  expanded?: boolean;
  onExpandedChange?: (expanded: boolean) => void;
  /** 独立于基础定价的分时策略编辑器。 */
  timePricingEditor?: ReactNode;
  timePeriodCount?: number;
}) {
  const cardRef = useRef<HTMLDivElement>(null);
  // 展开态由父级记忆（providerId 粒度），过滤/切聚类重挂载不丢
  const expanded = expandedProp ?? !matchedDefault;
  const setExpanded = (v: boolean) => onExpandedChange?.(v);
  const [newApiBusy, setNewApiBusy] = useState(false);
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const deleteArmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 外部跳转信号 → 滚动到视图 + 触发脉冲动画
  useEffect(() => {
    if (highlightSignal && highlightSignal > 0 && cardRef.current) {
      cardRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
      cardRef.current.classList.remove("pricing-pulse");
      // force reflow to restart animation
      void cardRef.current.offsetWidth;
      cardRef.current.classList.add("pricing-pulse");
    }
  }, [highlightSignal]);

  const curCode = draft.currency || "USD";
  const curSym = currencyToSymbol(curCode);

  const placeholder = (field: keyof DraftEntry): string => {
    if (field === "price") return "";
    // 已选/自动来源价优先回显，其次内置默认
    const active = activeSelection?.price ?? null;
    if (active) {
      const v = active[field as keyof NonNullable<PriceSelection["price"]>];
      if (v != null) return String(v);
      return "";
    }
    if (!matchedDefault?.entry) return "";
    const dv = matchedDefault.entry[field as keyof PriceEntry];
    return dv != null ? String(dv) : "";
  };

  const setMode = (mode: PricingMode) => {
    if (mode === "per_token" || mode === "per_tier") {
      onChange({ mode, price: "", expr: "" });
    } else if (mode === "tiered_expr") {
      onChange({
        mode,
        input: "",
        input_cached: "",
        output: "",
        cache_creation: "",
        price: "",
        contextTiers: [],
        serviceTiers: [],
      });
    } else {
      onChange({
        mode,
        input: "",
        input_cached: "",
        output: "",
        cache_creation: "",
        expr: "",
        contextTiers: [],
        serviceTiers: [],
      });
    }
  };

  const sourceModels = Array.from(
    new Set([
      ...candidates,
      ...Object.keys(selections || {}),
      ...Object.keys(autoPriceKeys || {}),
      ...Object.keys(priceCandidatesByModel || {}),
    ].filter(Boolean)),
  );
  const mainModel = sourceModels[0] || "";
  const primarySelection = mainModel ? selections?.[mainModel] : undefined;
  const selectedFallback = Object.values(selections || {})[0];
  const primaryAutoKey = mainModel ? autoPriceKeys?.[mainModel] : undefined;
  const autoFallback = Object.values(autoPriceKeys || {})[0];
  const activeSelection = primarySelection || selectedFallback;
  const activeAutoKey = primaryAutoKey || autoFallback;

  const sourceFromPriceKey = (priceKey: string): string => {
    if (priceKey.startsWith("newapi:")) {
      return priceKey.split(":").slice(0, 2).join(":");
    }
    return priceKey.split(":", 1)[0];
  };

  // 未定价 = 无自定义定价、无内置匹配、无已选/自动价格源
  const hasSource = !!activeSelection || !!activeAutoKey;
  const isUnpriced = !hasUserOverride && !matchedDefault && !hasSource;

  const deleteResidueData = async () => {
    if (!onDeleteData || deleting) return;
    if (!deleteArmed) {
      setDeleteArmed(true);
      setDeleteError("");
      if (deleteArmTimer.current) clearTimeout(deleteArmTimer.current);
      deleteArmTimer.current = setTimeout(() => setDeleteArmed(false), 4000);
      return;
    }
    if (deleteArmTimer.current) clearTimeout(deleteArmTimer.current);
    setDeleteArmed(false);
    setDeleting(true);
    setDeleteError("");
    try {
      await onDeleteData();
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : String(e));
      setDeleting(false);
    }
  };

  // 生效来源徽标（F6.3）：自定义 > 已选源 > 自动匹配 > 内置默认 > 未定价
  const sourceBadge = hasUserOverride
    ? null
    : activeSelection
      ? {
          text: `${activeSelection.confirmed ? "已选源" : "自动"}：${sourceLabel(
            activeSelection.source || sourceFromPriceKey(activeSelection.price_key),
          )}`,
          cls: "pricing-badge--blue",
        }
      : activeAutoKey
        ? {
            text: `自动：${sourceLabel(sourceFromPriceKey(activeAutoKey))}`,
            cls: "pricing-badge--blue",
          }
        : null;

  const cardClass = [
    "pricing-card",
    isDeletedResidue ? "is-deleted-residue" : "",
    !expanded ? "is-collapsed" : "",
    isUnpriced && hasUsage ? "is-unpriced-alert" : "",
    isUnpriced && !hasUsage ? "is-unpriced-warn" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <div className={cardClass} ref={cardRef}>
      <div
        className="pricing-card-head"
        onClick={() => !expanded && setExpanded(true)}
        style={!expanded ? { cursor: "pointer" } : undefined}
      >
        <div className="pricing-id-wrap">
          <span className="mono pricing-id">{providerId}</span>
          {isDeletedResidue && (
            <span className="pricing-tag-residue" title="该 Provider 已不在当前配置中，但仍有用量记录">
              残留
            </span>
          )}
          {type && <span className="muted small">{type}</span>}
          {hasUserOverride ? (
            <span className="pricing-badge pricing-badge--blue">自定义</span>
          ) : sourceBadge ? (
            <span className={`pricing-badge ${sourceBadge.cls}`}>{sourceBadge.text}</span>
          ) : matchedDefault ? (
            <span className="pricing-badge pricing-badge--gray">内置匹配</span>
          ) : (
            <span className="pricing-badge pricing-badge--red">未定价</span>
          )}
          {!!timePeriodCount && (
            <span className="pricing-badge pricing-badge--purple">
              分时 × {timePeriodCount}
            </span>
          )}
        </div>
        <div
          className="pricing-head-right"
          style={{ display: "flex", alignItems: "center", gap: 8 }}
        >
          {isDeletedResidue && onDeleteData && (
            <button
              type="button"
              className={`pricing-delete-residue ${deleteArmed ? "is-armed" : ""}`}
              disabled={deleting}
              onClick={(e) => {
                e.stopPropagation();
                void deleteResidueData();
              }}
              title="永久删除该 Provider 的历史用量、补充记录和旧定价"
            >
              {deleting ? "删除中…" : deleteArmed ? "⚠ 确认删除" : "删除残留数据"}
            </button>
          )}
          {deleteError && <span className="pricing-delete-error">{deleteError}</span>}
          {!expanded && (
            <span className="pricing-collapsed-summary">
              {selectingKey?.startsWith(`${providerId}|`)
                ? "加载中…"
                : collapsedSummary(
                    draft,
                    matchedDefault ?? null,
                    !!hasUserOverride,
                    activeSelection?.price ?? null,
                  )}
            </span>
          )}
          {!expanded && (
            <button
              type="button"
              className="pricing-expand-btn"
              title="展开编辑"
            >
              ▸
            </button>
          )}
          {expanded && (
            <>
              <label
                className="pricing-currency-label"
                title="该 Provider 计价使用的货币，结算时按汇率换算到主货币"
              >
                <span className="muted small">货币</span>
                <select
                  className="budget-input pricing-currency-select"
                  value={draft.currency}
                  disabled={draft.mode === "tiered_expr"}
                  title={
                    draft.mode === "tiered_expr" ? "表达式输出固定为 USD" : undefined
                  }
                  onChange={(e) =>
                    onChange({ currency: e.target.value } as Partial<DraftEntry>)
                  }
                  onClick={(e) => e.stopPropagation()}
                >
                  <option value="">USD</option>
                  {CURRENCY_OPTIONS.filter((c) => c !== "USD").map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="pricing-clear"
                onClick={(e) => {
                  e.stopPropagation();
                  onClear();
                }}
                title="清除该 Provider 定价（回退默认）"
              >
                清除
              </button>
              <button
                type="button"
                className="pricing-collapse-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  setExpanded(false);
                }}
                title="折叠"
              >
                ▾
                {/* collapse */}
              </button>
            </>
          )}
        </div>
      </div>

      {expanded && (
        <>
          {candidates.length > 0 && (
            <div className="pricing-candidates">
              {candidates.map((c) => (
                <span key={c} className="provider-tag">
                  {c}
                </span>
              ))}
            </div>
          )}

          {/* New API 源开关（F1.2/F6.2） */}
          {(onEnableNewApiSource || newApiSource) && (
            <div className="newapi-source-row muted small">
              {newApiSource ? (
                <>
                  <span
                    className={`pricing-badge ${newApiSource.enabled ? "pricing-badge--blue" : "pricing-badge--gray"}`}
                  >
                    New API 源{newApiSource.enabled ? "已启用" : "已停用"}
                  </span>
                  <button
                    type="button"
                    className="pricing-clear"
                    disabled={newApiBusy || !!selectingKey}
                    onClick={async () => {
                      setNewApiBusy(true);
                      try {
                        if (newApiSource.enabled) await onDisableNewApiSource?.();
                        else await onEnableNewApiSource?.();
                      } finally {
                        setNewApiBusy(false);
                      }
                    }}
                  >
                    {newApiSource.enabled ? "停用" : "启用"}
                  </button>
                </>
              ) : (
                onEnableNewApiSource && (
                  <button
                    type="button"
                    className="pricing-clear"
                    disabled={newApiBusy}
                    title="探测该供应商是否为 New API 实例，并将其 /api/pricing 作为独立价格源"
                    onClick={async () => {
                      setNewApiBusy(true);
                      try {
                        await onEnableNewApiSource();
                      } finally {
                        setNewApiBusy(false);
                      }
                    }}
                  >
                    探测并启用 New API 价格源
                  </button>
                )
              )}
            </div>
          )}

          {/* 多源候选与已确认选择，按实际模型分别展示（F2/F6.4）。 */}
          {sourceModels.map((model) => {
            const modelCandidates = priceCandidatesByModel?.[model] || [];
            const modelSelection = selections?.[model];
            const modelAutoKey = autoPriceKeys?.[model];
            if (!modelCandidates.length && !modelSelection && !modelAutoKey) return null;
            return (
              <div className="candidate-model-group" key={model}>
                {modelCandidates.length > 0 && (
                  <CandidateTable
                    model={model}
                    candidates={modelCandidates}
                    selecting={!!selectingKey && selectingKey === `${providerId}|${model}`}
                    onSelect={(selectedModel, priceKey) =>
                      void onSelectCandidate?.(selectedModel, priceKey)
                    }
                  />
                )}
                {modelSelection && onResetSelection && (
                  <div className="muted small source-selection-row">
                    <span>当前使用来源价格：</span>
                    <span className="mono">{modelSelection.price_key}</span>
                    <button
                      type="button"
                      className="pricing-clear"
                      disabled={!!selectingKey && selectingKey === `${providerId}|${model}`}
                      onClick={() => void onResetSelection(model)}
                      title="清除该模型的价格源选择，回退到自动匹配或内置默认"
                    >
                      清除选择
                    </button>
                  </div>
                )}
                {!modelSelection && modelAutoKey && (
                  <div className="muted small source-selection-row">
                    <span>自动匹配：</span>
                    <span className="mono">{modelAutoKey}</span>
                  </div>
                )}
              </div>
            );
          })}

          <div className="pricing-mode-row">
            <Segmented
              options={MODE_OPTIONS}
              value={draft.mode}
              onChange={setMode}
              variant="weak"
            />
          </div>
          <div className="muted small pricing-mode-hint">
            {modeHint(draft.mode, curCode, curSym)}
          </div>

          {draft.mode === "tiered_expr" ? (
            <TieredExprEditor
              expr={draft.expr}
              lockedSource=""
              onChange={(expr) => onChange({ expr })}
              onUnlock={() => {}}
            />
          ) : (
            <div className={`pricing-fields pf-${draft.mode}`}>
              {draft.mode === "per_token" || draft.mode === "per_tier" ? (
                TOKEN_FIELDS.map((f) => (
                  <label key={f.key} className="pricing-field">
                    <span className="muted small">
                      {draft.mode === "per_tier" ? `${f.label}（基础价）` : f.label}
                    </span>
                    <input
                      type="number"
                      step="any"
                      min="0"
                      className="budget-input"
                      value={draft[f.key] as string}
                      placeholder={placeholder(f.key)}
                      onChange={(e) =>
                        onChange({ [f.key]: e.target.value } as Partial<DraftEntry>)
                      }
                    />
                  </label>
                ))
              ) : (
                <label className="pricing-field">
                  <span className="muted small">
                    {curSym} / {draft.mode === "per_turn" ? "每轮" : "每次请求"}
                  </span>
                  <input
                    type="number"
                    step="any"
                    min="0"
                    className="budget-input"
                    value={draft.price}
                    onChange={(e) =>
                      onChange({ price: e.target.value } as Partial<DraftEntry>)
                    }
                  />
                </label>
              )}
            </div>
          )}

          {draft.mode === "per_tier" && (
            <PerTierEditor
              contextTiers={draft.contextTiers}
              serviceTiers={draft.serviceTiers}
              onChange={(patch) => onChange(patch)}
            />
          )}
          {timePricingEditor}
        </>
      )}
    </div>
  );
}

export { emptyTierPriceDraft, emptyServiceTierDraft };
