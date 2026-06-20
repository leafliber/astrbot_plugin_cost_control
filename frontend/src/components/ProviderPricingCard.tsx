import { Segmented } from "./Segmented";
import type { PriceEntry, PricingMode, UserPricingEntry } from "../lib/types";

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
  per_token: "USD / 百万 token。按 input / 缓存命中 / output / 缓存写入 分别计价。",
  per_turn: "USD / 次。每次 LLM 调用（含 function-calling 每一步）固定费用。",
  per_request: "USD / 次。每次用户请求固定费用（一次请求含多步调用只计一次）。",
};

export function ProviderPricingCard({
  providerId,
  type,
  candidates,
  draft,
  defaults,
  onChange,
  onClear,
}: {
  providerId: string;
  type?: string;
  candidates: string[];
  draft: DraftEntry;
  defaults: Record<string, PriceEntry>;
  onChange: (patch: Partial<DraftEntry>) => void;
  onClear: () => void;
}) {
  // per_token 时，按候选模型匹配内置默认作占位提示
  const placeholder = (field: keyof DraftEntry): string => {
    for (const c of candidates) {
      const d = defaults[c];
      if (d) {
        const v = d[field as keyof PriceEntry];
        if (v != null) return String(v);
      }
    }
    return "";
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

  const fieldStyle = { width: 90 };

  return (
    <div className="override-row">
      <div className="override-head">
        <span className="mono" style={{ fontWeight: 600 }}>
          {providerId}
        </span>
        {type && <span className="muted small"> · {type}</span>}
      </div>

      {candidates.length > 0 && (
        <div className="muted small" style={{ margin: "4px 0" }}>
          候选模型：
          {candidates.map((c) => (
            <span key={c} className="provider-tag" style={{ marginRight: 4 }}>
              {c}
            </span>
          ))}
        </div>
      )}

      <div className="override-action" style={{ margin: "4px 0" }}>
        <span className="muted small">计费模式</span>
        <Segmented
          options={MODE_OPTIONS}
          value={draft.mode}
          onChange={setMode}
          variant="weak"
        />
      </div>

      <div className="muted small" style={{ marginBottom: 6 }}>
        {MODE_HINT[draft.mode]}
      </div>

      {draft.mode === "per_token" ? (
        <div className="override-limits">
          {TOKEN_FIELDS.map((f) => (
            <label key={f.key} className="limit-cell">
              <span className="muted small">{f.label}</span>
              <input
                type="number"
                step="any"
                min="0"
                className="budget-input"
                value={draft[f.key]}
                placeholder={placeholder(f.key)}
                onChange={(e) => onChange({ [f.key]: e.target.value } as Partial<DraftEntry>)}
                style={fieldStyle}
              />
            </label>
          ))}
        </div>
      ) : (
        <div className="override-limits">
          <label className="limit-cell">
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
              style={fieldStyle}
            />
          </label>
        </div>
      )}

      <div className="override-move">
        <button type="button" className="move-btn del" onClick={onClear} title="清除该 Provider 定价（回退默认）">
          ✕
        </button>
      </div>
    </div>
  );
}
