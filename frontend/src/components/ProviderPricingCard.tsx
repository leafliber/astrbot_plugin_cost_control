import { Segmented } from "./Segmented";
import type {
  MatchedDefault,
  PerRequestEntry,
  PerTokenEntry,
  PerTurnEntry,
  PriceEntry,
  PricingMode,
  UserPricingEntry,
} from "../lib/types";
import { CURRENCY_OPTIONS, currencyToSymbol } from "../lib/format";

// 编辑中的临时态：mode + 该 mode 下所有可能字段（字符串形式便于空值处理）。
// collect 时按 mode 只取相关字段、空值不写入。
// currency: "" = USD（内部定价 USD 基准）；其它代码表示该 provider 以该货币计价，结算时换算到主货币。
export interface DraftEntry {
  mode: PricingMode;
  input: string;
  input_cached: string;
  output: string;
  cache_creation: string;
  price: string;
  currency: string;
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
    currency: entry?.currency ?? "",
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
    const e: PerTokenEntry = {
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
    if (!any) return null;
    // 空串 / "USD" = USD 基准，省略字段由后端兜底为 USD
    if (d.currency && d.currency !== "USD") e.currency = d.currency;
    return e;
  }
  const raw = d.price.trim();
  const n = parseFloat(raw);
  if (Number.isNaN(n) || n < 0) throw new Error("单价非法数值");
  const pe: PerTurnEntry | PerRequestEntry = { mode: d.mode, price: n } as
    | PerTurnEntry
    | PerRequestEntry;
  if (d.currency && d.currency !== "USD") pe.currency = d.currency;
  return pe;
}

const MODE_OPTIONS: { value: PricingMode; label: string }[] = [
  { value: "per_token", label: "按 Token" },
  { value: "per_turn", label: "按调用轮次" },
  { value: "per_request", label: "按请求次数" },
];

// 按当前选中货币生成计费提示（替换基准 "USD"）。
function modeHint(mode: PricingMode, code: string, symbol: string): string {
  const unit = `${symbol} (${code})`;
  if (mode === "per_token") {
    return `${unit} / 百万 token。留空字段 = 用内置默认价。输入即覆盖默认。`;
  }
  if (mode === "per_turn") {
    return `${unit} / 次。每次 LLM 调用（含 function-calling 每一步）固定费用。`;
  }
  return `${unit} / 次。每次用户请求固定费用（一次请求含多步调用只计一次）。`;
}

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

  // 空串 = USD（内部定价基准）。该 provider 以此货币计价，结算时换算到主货币。
  const curCode = draft.currency || "USD";
  const curSym = currencyToSymbol(curCode);

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
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <label
            style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
            title="该 Provider 计价使用的货币，结算时按汇率换算到主货币"
          >
            <span className="muted small">计价货币</span>
            <select
              className="budget-input"
              value={draft.currency}
              onChange={(e) => onChange({ currency: e.target.value } as Partial<DraftEntry>)}
            >
              <option value="">USD（默认）</option>
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
            onClick={onClear}
            title="清除该 Provider 定价（回退默认）"
          >
            清除
          </button>
        </div>
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
      <div className="muted small pricing-mode-hint">{modeHint(draft.mode, curCode, curSym)}</div>

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
              {curSym} / {draft.mode === "per_turn" ? "每轮" : "每次请求"}
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
