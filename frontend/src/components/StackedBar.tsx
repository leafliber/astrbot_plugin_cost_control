import { fmtNum } from "../lib/format";

export interface StackedSegment {
  label: string;
  value: number;
  color: string;
  /** 悬浮提示：该维度的简介与可能来源（可选，传入后 legend 项显示 tooltip）。 */
  tooltip?: string;
}

// 组件占比堆叠条 + 图例。color 可为 CSS 变量（如 "var(--accent)"）。
export function StackedBar({ segments }: { segments: StackedSegment[] }) {
  const total = segments.reduce((s, c) => s + (c.value || 0), 0);
  if (total <= 0) return <div className="empty">暂无组件数据</div>;
  return (
    <>
      <div className="stacked-bar">
        {segments.map((c) => {
          const pct = Math.round((c.value * 100) / total);
          if (pct <= 0) return null;
          return (
            <div
              key={c.label}
              className="stacked-seg"
              style={{ width: `${pct}%`, background: c.color }}
              title={c.tooltip ? `${c.label} ${pct}%\n${c.tooltip}` : `${c.label} ${pct}%`}
            >
              {pct >= 8 ? `${pct}%` : ""}
            </div>
          );
        })}
      </div>
      <div className="legend">
        {segments.map((c) => {
          const pct = total > 0 ? Math.round((c.value * 100) / total) : 0;
          const body = (
            <>
              <span className="legend-dot" style={{ background: c.color }} />
              {c.label} {fmtNum(c.value)} ({pct}%)
            </>
          );
          if (c.tooltip) {
            return (
              <span key={c.label} className="legend-item legend-item-tip">
                {body}
                <span className="legend-tip">{c.tooltip}</span>
              </span>
            );
          }
          return (
            <span key={c.label} className="legend-item">
              {body}
            </span>
          );
        })}
      </div>
    </>
  );
}
