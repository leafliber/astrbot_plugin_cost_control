import type { ReactNode } from "react";

export interface SegmentedOption<T extends string> {
  value: T;
  label: ReactNode;
}

// 切换按钮组。variant "default" 复用 .window-switch/.win-btn（panel 背景），
// "weak" 复用 .agg-switch/.agg-btn（panel-2 背景）。消除原 app.js 5 处重复。
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  variant = "default",
}: {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (v: T) => void;
  variant?: "default" | "weak";
}) {
  const containerCls = variant === "weak" ? "agg-switch" : "window-switch";
  const itemCls = variant === "weak" ? "agg-btn" : "win-btn";
  return (
    <span className={containerCls}>
      {options.map((o) => (
        <button
          key={o.value}
          className={`${itemCls} ${o.value === value ? "active" : ""}`.trim()}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </span>
  );
}
