import type { CandidateBrief } from "../../lib/types";
import { Button } from "../Button";
import { sourceLabel } from "./SourceStatusBar";

// 候选价格对比表（F2）：同一 (provider, model) 的多个来源并排比较，
// New API 源置顶；选择操作由父组件持久化。

function fmtUsdPerM(v: number | null | undefined): string {
  if (v == null) return "—";
  return `$${v.toFixed(4)}`;
}

function priceSummary(c: CandidateBrief): string {
  if (c.mode === "tiered_expr") return "表达式";
  if (c.mode === "per_tier") {
    return `阶梯${c.context_tiers > 0 ? ` ×${c.context_tiers}` : ""}`;
  }
  if (c.mode === "per_turn") return c.price != null ? `$${c.price}/轮` : "按轮";
  if (c.mode === "per_request") return c.price != null ? `$${c.price}/次` : "按次";
  return "按 Token";
}

export function CandidateTable({
  model,
  candidates,
  selecting,
  onSelect,
}: {
  model: string;
  candidates: CandidateBrief[];
  selecting: boolean;
  onSelect: (model: string, priceKey: string) => void;
}) {
  // New API 源排最前，其余保持后端分数排序。
  const sorted = [...candidates].sort((a, b) => {
    const an = a.source.startsWith("newapi:") ? 0 : 1;
    const bn = b.source.startsWith("newapi:") ? 0 : 1;
    return an - bn;
  });

  return (
    <div className="candidate-block">
      <div className="muted small candidate-title">
        模型 <b>{model}</b> 有 {candidates.length} 个来源价格，请选用一个：
      </div>
      <table className="candidate-table">
        <thead>
          <tr>
            <th>来源</th>
            <th>源模型 ID</th>
            <th>输入</th>
            <th>输出</th>
            <th>缓存读</th>
            <th>缓存写</th>
            <th>方式</th>
            <th>分数</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((c) => {
            const isNewApi = c.source.startsWith("newapi:");
            return (
              <tr key={c.price_key} className={isNewApi ? "candidate-row--newapi" : ""}>
                <td>
                  {sourceLabel(c.source)}
                  {isNewApi && <span className="pricing-badge pricing-badge--blue">本网关</span>}
                </td>
                <td className="muted">{c.source_model_id}</td>
                <td>{fmtUsdPerM(c.prompt)}</td>
                <td>{fmtUsdPerM(c.completion)}</td>
                <td>{fmtUsdPerM(c.cache_read)}</td>
                <td>{fmtUsdPerM(c.cache_creation)}</td>
                <td className="muted" title={c.expr || undefined}>
                  {priceSummary(c)}
                </td>
                <td className="muted" title={c.reason}>
                  {c.score.toFixed(2)}
                </td>
                <td>
                  <Button
                    variant="primary"
                    disabled={selecting}
                    onClick={() => onSelect(model, c.price_key)}
                  >
                    选用
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
