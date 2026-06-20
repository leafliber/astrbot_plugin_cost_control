import type { CompareResult } from "../lib/types";

// 环比徽标：成本/调用上升=不利（红 up），下降=有利（绿 down）；上期为 0 显示「新增」。
export function DeltaBadge({
  cmp,
  field,
}: {
  cmp: CompareResult | null;
  field: "cost" | "count" | "tokens";
}) {
  if (!cmp) return null;
  const pct = cmp.delta?.[`${field}_pct`] ?? null;
  const label = cmp.label || "上期";
  if (pct == null) {
    return <span className="delta new">{label}无用量</span>;
  }
  const cls = pct > 0 ? "up" : pct < 0 ? "down" : "flat";
  const arrow = pct > 0 ? "↑" : pct < 0 ? "↓" : "→";
  return (
    <span className={`delta ${cls}`}>
      {arrow}
      {Math.abs(pct)}% vs {label}
    </span>
  );
}
