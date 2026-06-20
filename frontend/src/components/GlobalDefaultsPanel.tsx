import { ProgressBar } from "./ProgressBar";
import { fmtCost, fmtNum } from "../lib/format";
import type { BudgetDimension, Metric } from "../lib/types";

export interface DimMeta {
  key: string;
  label: string;
  note?: string;
}

const DEFAULT_DIMS: DimMeta[] = [
  { key: "global_daily", label: "全局每日" },
  { key: "global_monthly", label: "全局每月" },
  { key: "per_session_daily", label: "单会话每日", note: "代表值" },
  { key: "per_user_daily", label: "单用户每日", note: "代表值" },
  { key: "per_model_daily", label: "单模型每日", note: "代表值" },
];

// 5 张「全局预算」卡片网格。切换 metric 时 token / cost 卡片高亮互换。
export function GlobalDefaultsPanel({
  limits,
  limitsCost,
  dimensions,
  metric,
  onChangeLimit,
  onChangeLimitCost,
}: {
  limits: Record<string, number>;
  limitsCost: Record<string, number>;
  dimensions: Record<string, BudgetDimension>;
  metric: Metric;
  onChangeLimit: (key: string, raw: string) => void;
  onChangeLimitCost: (key: string, raw: string) => void;
}) {
  return (
    <div className="budget-grid">
      {DEFAULT_DIMS.map((d) => {
        const dim = dimensions[d.key] || ({} as BudgetDimension);
        const t = dim.token || { limit: 0, used: 0, ratio: 0, exceeded: false };
        const c = dim.cost || { limit: 0, used: 0, ratio: 0, exceeded: false };
        const activeT = metric === "token";
        const activeC = metric === "cost";
        return (
          <div
            key={d.key}
            className={`budget-card ${t.exceeded || c.exceeded ? "exceeded" : ""}`.trim()}
          >
            <div className="budget-card-head">
              <div className="budget-card-label">{d.label}</div>
              {d.note && <div className="muted small">{d.note}</div>}
            </div>
            <div className={`budget-card-metric ${activeT ? "active" : "dim"}`}>
              <div className="muted small">Token</div>
              <div className="budget-card-input">
                <input
                  type="number"
                  min="0"
                  className="budget-input"
                  value={limits[d.key] || 0}
                  onChange={(e) => onChangeLimit(d.key, e.target.value)}
                />
              </div>
              <div className="muted small">
                {fmtNum(t.used)} / {fmtNum(limits[d.key] || 0)}
              </div>
              {t.limit > 0 ? (
                <ProgressBar ratio={t.ratio}>
                  {t.ratio || 0}%
                </ProgressBar>
              ) : (
                <div className="muted small">未设上限</div>
              )}
              {t.top_key && <div className="muted small">{t.top_key}</div>}
            </div>
            <div className={`budget-card-metric ${activeC ? "active" : "dim"}`}>
              <div className="muted small">花费 $</div>
              <div className="budget-card-input">
                <input
                  type="number"
                  min="0"
                  step="0.01"
                  className="budget-input"
                  value={limitsCost[d.key] || 0}
                  onChange={(e) => onChangeLimitCost(d.key, e.target.value)}
                />
              </div>
              <div className="muted small">
                {fmtCost(c.used)} / {fmtCost(limitsCost[d.key] || 0)}
              </div>
              {c.limit > 0 ? (
                <ProgressBar ratio={c.ratio}>
                  {c.ratio || 0}%
                </ProgressBar>
              ) : (
                <div className="muted small">未设上限</div>
              )}
              {c.top_key && <div className="muted small">{c.top_key}</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
