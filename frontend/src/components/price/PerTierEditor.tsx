import type { ServiceTierDraft, TierPriceDraft } from "../ProviderPricingCard";

// 结构化阶梯价编辑器（F3 per_tier）：base 四价在卡片主区域渲染，
// 本组件负责 context_tiers（阈值阶梯）与 service_tiers（服务等级倍率）两张可增删列表。

const TIER_PRICE_FIELDS: { key: keyof Omit<TierPriceDraft, "threshold">; label: string }[] = [
  { key: "input", label: "输入" },
  { key: "input_cached", label: "缓存命中" },
  { key: "output", label: "输出" },
  { key: "cache_creation", label: "缓存写入" },
];

const SVC_MULT_FIELDS: { key: keyof Omit<ServiceTierDraft, "match">; label: string }[] = [
  { key: "input_multiplier", label: "输入倍率" },
  { key: "output_multiplier", label: "输出倍率" },
  { key: "input_cached_multiplier", label: "缓存命中倍率" },
  { key: "cache_creation_multiplier", label: "缓存写入倍率" },
];

export function emptyTierPriceDraft(): TierPriceDraft {
  return { threshold: "", input: "", input_cached: "", output: "", cache_creation: "" };
}

export function emptyServiceTierDraft(): ServiceTierDraft {
  return {
    match: "",
    input_multiplier: "",
    output_multiplier: "",
    input_cached_multiplier: "",
    cache_creation_multiplier: "",
  };
}

export function PerTierEditor({
  contextTiers,
  serviceTiers,
  onChange,
}: {
  contextTiers: TierPriceDraft[];
  serviceTiers: ServiceTierDraft[];
  onChange: (patch: {
    contextTiers?: TierPriceDraft[];
    serviceTiers?: ServiceTierDraft[];
  }) => void;
}) {
  const patchTier = (i: number, patch: Partial<TierPriceDraft>) =>
    onChange({
      contextTiers: contextTiers.map((t, j) => (j === i ? { ...t, ...patch } : t)),
    });
  const removeTier = (i: number) =>
    onChange({ contextTiers: contextTiers.filter((_, j) => j !== i) });
  const patchSvc = (i: number, patch: Partial<ServiceTierDraft>) =>
    onChange({
      serviceTiers: serviceTiers.map((t, j) => (j === i ? { ...t, ...patch } : t)),
    });
  const removeSvc = (i: number) =>
    onChange({ serviceTiers: serviceTiers.filter((_, j) => j !== i) });

  return (
    <div className="tier-editor">
      <div className="tier-section">
        <div className="tier-section-head">
          <span className="muted small">上下文阶梯（总输入 token 超阈值时改用该阶价格）</span>
          <button
            type="button"
            className="btn tier-add-btn"
            onClick={() => onChange({ contextTiers: [...contextTiers, emptyTierPriceDraft()] })}
          >
            + 添加阶梯
          </button>
        </div>
        {contextTiers.length === 0 && (
          <div className="muted small">无阶梯 — 始终使用上方基础价。</div>
        )}
        {contextTiers.map((t, i) => (
          <div key={i} className="tier-row">
            <label className="pricing-field">
              <span className="muted small">阈值（token）</span>
              <input
                type="number"
                min="0"
                step="1"
                className="budget-input"
                value={t.threshold}
                placeholder="如 200000"
                onChange={(e) => patchTier(i, { threshold: e.target.value })}
              />
            </label>
            {TIER_PRICE_FIELDS.map((f) => (
              <label key={f.key} className="pricing-field">
                <span className="muted small">{f.label}</span>
                <input
                  type="number"
                  min="0"
                  step="any"
                  className="budget-input"
                  value={t[f.key]}
                  onChange={(e) => patchTier(i, { [f.key]: e.target.value })}
                />
              </label>
            ))}
            <button
              type="button"
              className="pricing-clear"
              title="删除该阶梯"
              onClick={() => removeTier(i)}
            >
              删除
            </button>
          </div>
        ))}
      </div>

      <div className="tier-section">
        <div className="tier-section-head">
          <span className="muted small">
            服务等级倍率（按请求 service_tier 命中，对基础价/阶梯价乘倍率）
          </span>
          <button
            type="button"
            className="btn tier-add-btn"
            onClick={() => onChange({ serviceTiers: [...serviceTiers, emptyServiceTierDraft()] })}
          >
            + 添加等级
          </button>
        </div>
        {serviceTiers.length === 0 && (
          <div className="muted small">无服务等级 — 不区分 priority / flex 等。</div>
        )}
        {serviceTiers.map((t, i) => (
          <div key={i} className="tier-row">
            <label className="pricing-field">
              <span className="muted small">匹配值</span>
              <input
                type="text"
                className="budget-input"
                value={t.match}
                placeholder="如 priority"
                onChange={(e) => patchSvc(i, { match: e.target.value })}
              />
            </label>
            {SVC_MULT_FIELDS.map((f) => (
              <label key={f.key} className="pricing-field">
                <span className="muted small">{f.label}</span>
                <input
                  type="number"
                  min="0"
                  step="any"
                  className="budget-input"
                  value={t[f.key]}
                  placeholder="1"
                  onChange={(e) => patchSvc(i, { [f.key]: e.target.value })}
                />
              </label>
            ))}
            <button
              type="button"
              className="pricing-clear"
              title="删除该等级"
              onClick={() => removeSvc(i)}
            >
              删除
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
