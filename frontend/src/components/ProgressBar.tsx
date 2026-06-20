import type { ReactNode } from "react";

// 通用进度条：阈值着色（pct>=badAt 用 bad 色，>=warnAt 用 warn 色）。
// records 聚合用 warnAt=25/badAt=50，budgets 用 warnAt=80/badAt=100。
export function ProgressBar({
  ratio,
  warnAt = 80,
  badAt = 100,
  children,
}: {
  ratio: number;
  warnAt?: number;
  badAt?: number;
  children?: ReactNode;
}) {
  const pct = Math.min(100, Math.max(0, ratio || 0));
  const cls = pct >= badAt ? "bad" : pct >= warnAt ? "warn" : "";
  return (
    <div className="row" style={{ alignItems: "center", gap: 8 }}>
      <div className="bar-wrap" style={{ flex: 1 }}>
        <div className={`bar ${cls}`} style={{ width: `${pct}%` }} />
      </div>
      {children != null && <span>{children}</span>}
    </div>
  );
}
