import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtCompact } from "../../lib/format";
import type { ChartColors } from "../../hooks/useChartColors";

export interface TokenStackRow {
  label: string;
  other: number;
  cached: number;
  output: number;
}

// Token 构成（单类目堆叠柱：输入非缓存 / 缓存命中 / 输出）
export function TokenStack({
  data,
  colors,
}: {
  data: TokenStackRow[];
  colors: ChartColors;
}) {
  const axisTick = { fill: colors.dim, fontSize: 11 };
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={colors.border} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" tick={axisTick} stroke={colors.border} />
        <YAxis tickFormatter={(v) => fmtCompact(Number(v))} tick={{ fill: colors.dim, fontSize: 10 }} stroke={colors.border} />
        <Tooltip />
        <Bar dataKey="other" name="输入(非缓存)" stackId="a" fill={colors.other} />
        <Bar dataKey="cached" name="缓存命中" stackId="a" fill={colors.cached} />
        <Bar dataKey="output" name="输出" stackId="a" fill={colors.accent} />
      </BarChart>
    </ResponsiveContainer>
  );
}
