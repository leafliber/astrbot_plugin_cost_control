import { Segmented } from "./Segmented";
import type { MatchedDefault, PriceEntry, PricingMode, UserPricingEntry } from "../lib/types";

// 编辑中的临时态：mode + 该 mode 下所有可能字段（字符串形式便于空值处理）。
// collect 时按 mode 只取相关字段、空值不写入。
export interface DraftEntry {
  mode: PricingMode;
  input: string;
  input_cached: string;
  output: string;
  cache_creation: string;
  price: string;
}

const TOKEN_FIELDS: { key: keyof DraftEntry; label: string }[] = [
  { key: "input", label: "输入" },
  { key: "input_cached", label: "缓存命中" },
  { key: "output", label: "输出" },
  { key: "cache_creation", label: "缓存写入" },
];

export function entryToDraft(entry?: UserPricingEntry): DraftEntry {
  const d: DraftEntry = {
    mode: entry?.mode ?? "per_token",
    input: "",
    input_cached: "",
    output: "",
    cache_creation: "",
    price: "",
  };
  if (!entry) return d;
  if (entry.mode === "per_token") {
    d.input = entry.input != null ? String(entry.input) : "";
    d.input_cached = entry.input_cached != null ? String(entry.input_cached) : "";
    d.output = entry.output != null ? String(entry.output) : "";
    d.cache_creation =
      entry.cache_creation != null && entry.cache_creation !== undefined
        ? String(entry.cache_creation)
        : "";
  } else {
    d.price = entry.price != null ? String(entry.price) : "";
  }
  return d;
}

// 判断 draft 是否为空（未填写任何有效字段）→ collect 时不写入该 provider
export function isDraftEmpty(d: DraftEntry): boolean {
  if (d.mode === "per_token") {
    return TOKEN_FIELDS.every((f) => d[f.key].trim() === "");
  }
  return d.price.trim() === "";
}

export function draftToEntry(d: DraftEntry): UserPricingEntry | null {
  if (isDraftEmpty(d)) return null;
  if (d.mode === "per_token") {
    const e: import("../lib/types").PerTokenEntry = {
      mode: "per_token",
      input: 0,
      input_cached: 0,
      output: 0,
    };
    let any = false;
    const assign = (field: "input" | "input_cached" | "output" | "cache_creation", raw: string) => {
      if (raw.trim() === "") return;
      const n = parseFloat(raw);
      if (Number.isNaN(n) || n < 0) throw new Error("非法数值");
      if (field === "cache_creation") {
        e.cache_creation = n;
      } else {
        e[field] = n;
      }
      any = true;
    };
    assign("input", d.input);
    assign("input_cached", d.input_cached);
    assign("output", d.output);
    assign("cache_creation", d.cache_creation);
    return any ? e : null;
  }
  const raw = d.price.trim();
  const n = parseFloat(raw);
  if (Number.isNaN(n) || n < 0) throw new Error("单价非法数值");
  return { mode: d.mode, price: n };
}

const MODE_OPTIONS: { value: PricingMode; label: string }[] = [
  { value: "per_token", label: "按 Token" },
  { value: "per_turn", label: "按调用轮次" },
  { value: "per_request", label: "按请求次数" },
];

const MODE_HINT: Record<PricingMode, string> = {
  per_token: "USD / 百万 token。留空字段 = 用内置默认价。输入即覆盖默认。",
  per_turn: "USD / 次。每次 LLM 调用（含 function-calling 每一步）固定费用。",
  per_request: "USD / 次。每次用户请求固定费用（一次请求含多步调用只计一次）。",
};

export function ProviderPricingCard({
  providerId,
  type,
  candidates,
  draft,
  matchedDefault,
  hasUserOverride,
  onChange,
  onClear,
}: {
  providerId: string;
  type?: string;
  candidates: string[];
  draft: DraftEntry;
  matchedDefault?: MatchedDefault | null;
  hasUserOverride?: boolean;
  onChange: (patch: Partial<DraftEntry>) => void;
  onClear: () => void;
}) {
  // 输入框背景提示：用后端算出的实际匹配默认价（与计费同口径）作 placeholder。
  // 用户输入即覆盖、placeholder 自动消失。
  const placeholder = (field: keyof DraftEntry): string => {
    if (field === "price" || !matchedDefault?.entry) return "";
    const v = matchedDefault.entry[field as keyof PriceEntry];
    return v != null ? String(v) : "";
  };

  const setMode = (mode: PricingMode) => {
    // 切换 mode 时清空另一模式的字段，避免残留
    if (mode === "per_token") {
      onChange({ mode, price: "" });
    } else {
      onChange({
        mode,
        input: "",
        input_cached: "",
        output: "",
        cache_creation: "",
      });
    }
  };

  return (
    <div className="pricing-card">
      <div className="pricing-card-head">
        <div className="pricing-id-wrap">
          <span className="mono pricing-id">{providerId}</span>
          {type && <span className="muted small">{type}</span>}
          {matchedDefault ? (
            <span className={`pricing-match ${hasUserOverride ? "is-overridden" : ""}`}>
              默认匹配 <span className="mono">{matchedDefault.model}</span>
              {hasUserOverride && <span className="pm-ov">已覆盖</span>}
            </span>
          ) : (
            <span className="pricing-match is-missing">无内置匹配</span>
          )}
        </div>
        <button
          type="button"
          className="pricing-clear"
          onClick={onClear}
          title="清除该 Provider 定价（回退默认）"
        >
          清除
        </button>
      </div>

      {candidates.length > 0 && (
        <div className="pricing-candidates">
          {candidates.map((c) => (
            <span key={c} className="provider-tag">
              {c}
            </span>
          ))}
        </div>
      )}

      <div className="pricing-mode-row">
        <Segmented options={MODE_OPTIONS} value={draft.mode} onChange={setMode} variant="weak" />
      </div>
      <div className="muted small pricing-mode-hint">{MODE_HINT[draft.mode]}</div>

      <div className={`pricing-fields pf-${draft.mode}`}>
        {draft.mode === "per_token" ? (
          TOKEN_FIELDS.map((f) => (
            <label key={f.key} className="pricing-field">
              <span className="muted small">{f.label}</span>
              <input
                type="number"
                step="any"
                min="0"
                className="budget-input"
                value={draft[f.key]}
                placeholder={placeholder(f.key)}
                onChange={(e) => onChange({ [f.key]: e.target.value } as Partial<DraftEntry>)}
              />
            </label>
          ))
        ) : (
          <label className="pricing-field">
            <span className="muted small">
              {draft.mode === "per_turn" ? "USD / 每轮" : "USD / 每次请求"}
            </span>
            <input
              type="number"
              step="any"
              min="0"
              className="budget-input"
              value={draft.price}
              onChange={(e) => onChange({ price: e.target.value } as Partial<DraftEntry>)}
            />
          </label>
        )}
      </div>
    </div>
  );
}
