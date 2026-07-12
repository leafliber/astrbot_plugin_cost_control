import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ChartColors } from "../../hooks/useChartColors";

export interface TokenStackRow {
  label: string;
  other: number;
  cached: number;
  output: number;
}

// Token 构成（单类目堆叠柱：输入非缓存 / 缓存命中 / 输出）
// 纵轴为百分比，每组堆叠总计 100%
export function TokenStack({
  data,
  colors,
}: {
  data: TokenStackRow[];
  colors: ChartColors;
}) {
  const axisTick = { fill: colors.dim, fontSize: 11 };

  // 将原始 token 数转换为百分比（每组总和为 100%）
  const pctData = data.map((d) => {
    const total = d.other + d.cached + d.output;
    if (total <= 0) {
      return { label: d.label, other: 0, cached: 0, output: 0 };
    }
    return {
      label: d.label,
      other: +((d.other / total) * 100).toFixed(1),
      cached: +((d.cached / total) * 100).toFixed(1),
      output: +((d.output / total) * 100).toFixed(1),
    };
  });

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={pctData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={colors.border} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" tick={axisTick} stroke={colors.border} />
        <YAxis
          tickFormatter={(v) => `${v}%`}
          domain={[0, 100]}
          tick={{ fill: colors.dim, fontSize: 10 }}
          stroke={colors.border}
        />
        <Tooltip
          formatter={(value: number, name: string) => [`${value}%`, name]}
        />
        <Bar dataKey="other" name="输入(非缓存)" stackId="a" fill={colors.other} />
        <Bar dataKey="cached" name="缓存命中" stackId="a" fill={colors.cached} />
        <Bar dataKey="output" name="输出" stackId="a" fill={colors.accent} />
      </BarChart>
    </ResponsiveContainer>
  );
}
