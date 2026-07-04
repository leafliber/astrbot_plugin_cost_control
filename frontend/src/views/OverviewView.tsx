import { api } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { usePolling } from "../hooks/usePolling";
import {
  fmtCost,
  fmtNum,
  getCurrencyCode,
  shortModelName,
  shortUmo,
  windowLabel,
  windowToDays,
} from "../lib/format";
import type { AlertTab, Window } from "../lib/types";
import type { ChartColors } from "../hooks/useChartColors";
import { StatCardGrid } from "../components/StatCardGrid";
import { DeltaBadge } from "../components/DeltaBadge";
import { Panel } from "../components/Panel";
import { Loading, ErrorBox, Empty } from "../components/Feedback";
import { AlertsBar } from "../components/AlertsBar";
import { LineTrend } from "../components/charts/LineTrend";
import { ModelCostBar } from "../components/charts/ModelCostBar";
import { TokenStack } from "../components/charts/TokenStack";
import { TopSessionsBar } from "../components/charts/TopSessionsBar";

export function OverviewView({
  window: win,
  refreshNonce,
  colors,
  onNavigate,
}: {
  window: Window;
  refreshNonce: number;
  colors: ChartColors;
  onNavigate?: (tab: AlertTab) => void;
}) {
  const days = windowToDays(win);
  const overview = useApi(() => api.getOverview(win), [win, refreshNonce]);
  const timeline = useApi(() => api.getTimeline(windowToDays(win), "day"), [
    win,
    refreshNonce,
  ]);
  const compare = useApi(() => api.getCompare(win), [win, refreshNonce]);
  const alerts = useApi(() => api.getAlerts(win), [win, refreshNonce]);

  // 仅总览轮询：30s 刷新四路数据；组件卸载自动停。
  usePolling(
    () => {
      overview.refetch();
      timeline.refetch();
      compare.refetch();
      alerts.refetch();
    },
    30000,
    true,
  );

  if (overview.loading && !overview.data) return <Loading />;
  if (overview.error) return <ErrorBox message={`加载总览失败：${overview.error}`} />;

  const r = overview.data;
  const u = (r?.usage || {}) as {
    count?: number;
    token_input_other?: number;
    token_input_cached?: number;
    token_output?: number;
  };
  const wl = windowLabel(win);

  const cards = [
    {
      label: "成本",
      value: fmtCost(r?.cost),
      sub: `${getCurrencyCode()} · ${wl}`,
      delta: <DeltaBadge cmp={compare.data ?? null} field="cost" />,
    },
    {
      label: "调用次数",
      value: fmtNum(u.count),
      sub: wl,
      delta: <DeltaBadge cmp={compare.data ?? null} field="count" />,
    },
    {
      label: "平均缓存命中率",
      value: `${r?.cache_hit_rate || 0}%`,
      sub: `${r?.cache_samples || 0} 样本`,
    },
    {
      label: "平均上下文注入",
      value: fmtNum(r?.avg_injection),
      sub: `${r?.injection_samples || 0} 样本 · token`,
    },
  ];

  const byModel = (r?.cost_by_model || [])
    .slice(0, 8)
    .map((m) => ({ model: shortModelName(m.model), cost: m.cost }));
  const tOther = u.token_input_other || 0;
  const tCached = u.token_input_cached || 0;
  const tOut = u.token_output || 0;
  const hasTokens = tOther + tCached + tOut > 0;
  const top = (r?.top_sessions || [])
    .slice(0, 8)
    .reverse()
    .map((s) => ({ umo: shortUmo(s.umo), tokens: s.tokens, cost: s.cost || 0 }));
  const byCost = (r?.top_sessions_by_cost || [])
    .slice(0, 8)
    .map((s) => ({ model: shortUmo(s.umo), cost: s.cost || 0 }));
  const series = timeline.data?.series ?? [];
  const alertItems = alerts.data || [];

  return (
    <div>
      {alertItems.length > 0 && onNavigate && (
        <AlertsBar alerts={alertItems} onNavigate={onNavigate} />
      )}
      <StatCardGrid items={cards} />
      <div className="grid-2">
        <Panel title={`用量趋势（近 ${days} 天）`}>
          {series.length ? (
            <div className="chart-box">
              <LineTrend series={series} colors={colors} />
            </div>
          ) : (
            <Empty text="暂无时序数据" />
          )}
        </Panel>
        <Panel title="按模型成本">
          {byModel.length ? (
            <div className="chart-box">
              <ModelCostBar data={byModel} colors={colors} />
            </div>
          ) : (
            <Empty text="暂无模型成本数据" />
          )}
        </Panel>
      </div>
      <div className="grid-2">
        <Panel title="Top 会话（按成本）">
          {byCost.length ? (
            <div className="chart-box">
              <ModelCostBar data={byCost} colors={colors} />
            </div>
          ) : (
            <Empty text="暂无会话数据" />
          )}
        </Panel>
        <Panel title="Top 会话（按 token）">
          {top.length ? (
            <div className="chart-box">
              <TopSessionsBar data={top} colors={colors} />
            </div>
          ) : (
            <Empty text="暂无会话数据" />
          )}
        </Panel>
      </div>
      <div className="grid-2">
        <Panel title="Token 构成">
          {hasTokens ? (
            <div className="chart-box">
              <TokenStack
                data={[{ label: wl, other: tOther, cached: tCached, output: tOut }]}
                colors={colors}
              />
            </div>
          ) : (
            <Empty text="暂无 token 数据" />
          )}
        </Panel>
      </div>
    </div>
  );
}
