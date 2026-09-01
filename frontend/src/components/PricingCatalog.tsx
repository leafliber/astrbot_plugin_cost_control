import { useState, type ReactNode } from "react";
import type { PricingCluster } from "../lib/types";

function parseMultiplier(raw: string): number {
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 && value <= 100 ? value : 1;
}

export function PricingCatalog({
  clusters,
  selectedId,
  onSelect,
  multipliers,
  onMultiplierChange,
  renderProvider,
}: {
  clusters: PricingCluster[];
  selectedId: string;
  onSelect: (clusterId: string) => void;
  multipliers: Record<string, string>;
  onMultiplierChange: (clusterId: string, value: string) => void;
  renderProvider: (providerId: string, multiplier: number) => ReactNode;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const selected =
    clusters.find((cluster) => cluster.id === selectedId) ?? clusters[0];

  if (!selected) return null;

  const rawMultiplier = multipliers[selected.id] ?? "1";
  const multiplier = parseMultiplier(rawMultiplier);

  return (
    <div className="pricing-catalog">
      <aside className="pricing-cluster-sidebar" aria-label="AstrBot 供应商目录">
        <div className="pricing-cluster-sidebar-title">ASTRBOT 供应商</div>
        <div
          className="pricing-cluster-directory"
          role="tablist"
          aria-orientation="vertical"
        >
          {clusters.map((cluster) => {
            const factor = parseMultiplier(multipliers[cluster.id] ?? "1");
            const active = cluster.id === selected.id;
            return (
              <button
                key={cluster.id}
                type="button"
                role="tab"
                aria-selected={active}
                className={`pricing-cluster-item ${active ? "is-active" : ""}`}
                onClick={() => onSelect(cluster.id)}
              >
                <span className="pricing-cluster-item-main">
                  <span className="pricing-cluster-name">{cluster.name}</span>
                  <span className="pricing-cluster-count">
                    {cluster.provider_ids.length}
                  </span>
                </span>
                {factor !== 1 && (
                  <span className="pricing-cluster-factor">{factor}×</span>
                )}
              </button>
            );
          })}
        </div>
      </aside>

      <section className="pricing-cluster-detail" role="tabpanel">
        <div className="pricing-cluster-detail-head">
          <div>
            <div className="pricing-cluster-detail-title-row">
              <h3>{selected.name}</h3>
              <span className="pricing-cluster-model-count">
                {selected.provider_ids.length} 个现有模型配置
              </span>
              <span
                className={`pricing-cluster-current-factor ${multiplier !== 1 ? "is-custom" : ""}`}
              >
                当前 {multiplier}×
              </span>
            </div>
            <div className="muted small">
              卡片显示当前自定义价或内置匹配价；展开即可直接修改。
            </div>
          </div>
          <button
            type="button"
            className="btn pricing-multiplier-toggle"
            aria-expanded={editingId === selected.id}
            onClick={() =>
              setEditingId((current) =>
                current === selected.id ? null : selected.id,
              )
            }
          >
            {editingId === selected.id ? "收起倍率" : "设置聚类倍率"}
          </button>
        </div>

        {editingId === selected.id && (
          <div className="pricing-multiplier-editor">
            <div className="pricing-multiplier-copy">
              <span className="pricing-multiplier-label">
                {selected.name} 供应商倍率
              </span>
              <span className="muted small">
                对该 AstrBot Provider Source 下的现有模型统一相乘；卡片中仍填写基准价。0
                表示该分组免费。
              </span>
            </div>
            <label className="pricing-multiplier-input-wrap">
              <input
                type="number"
                min="0"
                max="100"
                step="0.05"
                className="budget-input pricing-multiplier-input"
                value={rawMultiplier}
                onChange={(event) =>
                  onMultiplierChange(selected.id, event.target.value)
                }
                onBlur={() => {
                  const value = Number(rawMultiplier);
                  if (!Number.isFinite(value) || value < 0 || value > 100) {
                    onMultiplierChange(selected.id, "1");
                  }
                }}
                aria-label={`${selected.name} 供应商倍率`}
              />
              <span>×</span>
            </label>
            {multiplier !== 1 && (
              <button
                type="button"
                className="pricing-multiplier-reset"
                onClick={() => onMultiplierChange(selected.id, "1")}
              >
                恢复 1×
              </button>
            )}
          </div>
        )}

        <div className="pricing-rule-scroll pricing-provider-rule-scroll">
          {selected.provider_ids.map((providerId) =>
            renderProvider(providerId, multiplier),
          )}
        </div>
      </section>
    </div>
  );
}
