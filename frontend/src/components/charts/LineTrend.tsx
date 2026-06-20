import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtCompact, shortDate } from "../../lib/format";
import type { TimelinePoint } from "../../lib/types";
import type { ChartColors } from "../../hooks/useChartColors";

// 用量趋势折线（双轴：调用左 / Token 右）
export function LineTrend({
  series,
  colors,
}: {
  series: TimelinePoint[];
  colors: ChartColors;
}) {
  const data = series.map((s) => ({
    bucket: s.bucket,
    count: s.count,
    tokens:
      (s.token_input_other || 0) + (s.token_input_cached || 0) + (s.token_output || 0),
  }));
  const axisTick = { fill: colors.dim, fontSize: 10 };
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={colors.border} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="bucket"
          tickFormatter={(v) => shortDate(String(v))}
          tick={axisTick}
          stroke={colors.border}
        />
        <YAxis yAxisId="y" tickFormatter={(v) => fmtCompact(Number(v))} tick={axisTick} stroke={colors.border} />
        <YAxis
          yAxisId="y1"
          orientation="right"
          tickFormatter={(v) => fmtCompact(Number(v))}
          tick={axisTick}
          stroke={colors.border}
        />
        <Tooltip />
        <Line yAxisId="y" type="monotone" dataKey="count" name="调用" stroke={colors.accent} dot={false} />
        <Line yAxisId="y1" type="monotone" dataKey="tokens" name="Token" stroke={colors.warn} dot={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
