import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { currencyToSymbol, fmtCompact, getCurrencyCode, shortDate } from "../../lib/format";
import type { CostTimelinePoint } from "../../lib/types";
import type { ChartColors } from "../../hooks/useChartColors";

// 成本趋势面积图（每日成本，与用量趋势折线对应）
export function CostTrend({
  data,
  colors,
}: {
  data: CostTimelinePoint[];
  colors: ChartColors;
}) {
  const sym = currencyToSymbol(getCurrencyCode());
  const axisTick = { fill: colors.dim, fontSize: 10 };
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="costGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={colors.accent} stopOpacity={0.35} />
            <stop offset="100%" stopColor={colors.accent} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={colors.border} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="bucket"
          tickFormatter={(v) => shortDate(String(v))}
          tick={axisTick}
          stroke={colors.border}
        />
        <YAxis
          tickFormatter={(v) => sym + fmtCompact(Number(v))}
          tick={axisTick}
          stroke={colors.border}
        />
        <Tooltip
          formatter={(v: number) => [fmtCostTip(v, sym), "成本"]}
          labelFormatter={(l) => shortDate(String(l))}
        />
        <Area
          type="monotone"
          dataKey="cost"
          name="成本"
          stroke={colors.accent}
          strokeWidth={2}
          fill="url(#costGradient)"
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

function fmtCostTip(v: number, sym: string): string {
  const n = Number(v ?? 0);
  if (!Number.isFinite(n)) return sym + "0";
  return sym + n.toFixed(4);
}
