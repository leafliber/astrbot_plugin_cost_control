import { useMemo } from "react";

export interface ChartColors {
  accent: string;
  cached: string;
  warn: string;
  other: string;
  ok: string;
  dim: string;
  border: string;
  text: string;
}

// 颜色直接由 isDark 决定（与 styles/tokens.css 的 CSS 变量值保持一致），
// 避免「CSS 变量切换」与「recharts 读色」之间的 DOM 时序问题。
const LIGHT: ChartColors = {
  accent: "#4f7cff",
  cached: "#2f9e44",
  warn: "#f08c00",
  other: "#8ab4ff",
  ok: "#2f9e44",
  dim: "#6b7280",
  border: "#e3e6ea",
  text: "#1f2329",
};

const DARK: ChartColors = {
  accent: "#6b94ff",
  cached: "#51cf66",
  warn: "#ffa94d",
  other: "#8ab4ff",
  ok: "#51cf66",
  dim: "#8b909a",
  border: "#2e333d",
  text: "#e6e8ec",
};

export function useChartColors(isDark: boolean): ChartColors {
  return useMemo(() => (isDark ? DARK : LIGHT), [isDark]);
}
