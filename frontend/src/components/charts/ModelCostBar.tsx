import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtCompact } from "../../lib/format";
import type { ChartColors } from "../../hooks/useChartColors";

export interface ModelCostRow {
  model: string;
  cost: number;
}

// 按模型成本（横向柱）
export function ModelCostBar({
  data,
  colors,
}: {
  data: ModelCostRow[];
  colors: ChartColors;
}) {
  const axisTick = { fill: colors.dim, fontSize: 10 };
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
        <CartesianGrid stroke={colors.border} strokeDasharray="3 3" horizontal={false} />
        <XAxis
          type="number"
          tickFormatter={(v) => "$" + fmtCompact(Number(v))}
          tick={axisTick}
          stroke={colors.border}
        />
        <YAxis type="category" dataKey="model" width={120} tick={axisTick} stroke={colors.border} />
        <Tooltip />
        <Bar dataKey="cost" name="成本" fill={colors.accent} radius={[0, 3, 3, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
