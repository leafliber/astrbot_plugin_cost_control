import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtCompact } from "../../lib/format";
import type { ChartColors } from "../../hooks/useChartColors";

export interface TopSessionRow {
  umo: string;
  tokens: number;
  cost: number;
}

// Top 会话（按 token，横向柱）
export function TopSessionsBar({
  data,
  colors,
}: {
  data: TopSessionRow[];
  colors: ChartColors;
}) {
  const axisTick = { fill: colors.dim, fontSize: 10 };
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
        <CartesianGrid stroke={colors.border} strokeDasharray="3 3" horizontal={false} />
        <XAxis type="number" tickFormatter={(v) => fmtCompact(Number(v))} tick={axisTick} stroke={colors.border} />
        <YAxis type="category" dataKey="umo" width={120} tick={axisTick} stroke={colors.border} />
        <Tooltip />
        <Bar dataKey="tokens" name="Token" fill={colors.warn} radius={[0, 3, 3, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
