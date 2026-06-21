import { ProgressBar } from "./ProgressBar";
import { fmtCost, fmtNum } from "../lib/format";
import type { BudgetDimension } from "../lib/types";

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

// 5 维全局预算表格：每行一个维度，Token / Cost 两列同时显示限额输入、消耗与进度。
export function GlobalDefaultsPanel({
  limits,
  limitsCost,
  dimensions,
  onChangeLimit,
  onChangeLimitCost,
}: {
  limits: Record<string, number>;
  limitsCost: Record<string, number>;
  dimensions: Record<string, BudgetDimension>;
  onChangeLimit: (key: string, raw: string) => void;
  onChangeLimitCost: (key: string, raw: string) => void;
}) {
  return (
    <table className="budget-table">
      <thead>
        <tr>
          <th style={{ minWidth: 120 }}>维度</th>
          <th style={{ width: "42%" }}>Token（限额 / 消耗）</th>
          <th style={{ width: "42%" }}>花费 $（限额 / 消耗）</th>
        </tr>
      </thead>
      <tbody>
        {DEFAULT_DIMS.map((d) => {
          const dim = dimensions[d.key] || ({} as BudgetDimension);
          const t = dim.token || { limit: 0, used: 0, ratio: 0, exceeded: false };
          const c = dim.cost || { limit: 0, used: 0, ratio: 0, exceeded: false };
          const exceeded = !!(t.exceeded || c.exceeded);
          return (
            <tr key={d.key} className={exceeded ? "exceeded" : ""}>
              <td>
                <div className="budget-dim-label">{d.label}</div>
                {d.note && <div className="muted small">{d.note}</div>}
              </td>
              <td>
                <div className="budget-cell">
                  <input
                    type="number"
                    min="0"
                    className="budget-input"
                    value={limits[d.key] || 0}
                    onChange={(e) => onChangeLimit(d.key, e.target.value)}
                    style={{ width: 110 }}
                  />
                  <div className="muted small budget-cell-used">
                    {fmtNum(t.used)} / {fmtNum(limits[d.key] || 0)}
                  </div>
                  {t.limit > 0 ? (
                    <ProgressBar ratio={t.ratio}>{t.ratio || 0}%</ProgressBar>
                  ) : (
                    <div className="muted small">未设上限</div>
                  )}
                  {t.top_key && <div className="muted small">{t.top_key}</div>}
                </div>
              </td>
              <td>
                <div className="budget-cell">
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    className="budget-input"
                    value={limitsCost[d.key] || 0}
                    onChange={(e) => onChangeLimitCost(d.key, e.target.value)}
                    style={{ width: 110 }}
                  />
                  <div className="muted small budget-cell-used">
                    {fmtCost(c.used)} / {fmtCost(limitsCost[d.key] || 0)}
                  </div>
                  {c.limit > 0 ? (
                    <ProgressBar ratio={c.ratio}>{c.ratio || 0}%</ProgressBar>
                  ) : (
                    <div className="muted small">未设上限</div>
                  )}
                  {c.top_key && <div className="muted small">{c.top_key}</div>}
                </div>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
