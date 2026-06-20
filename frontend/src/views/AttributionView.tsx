import { api } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { fmtNum, shortTime } from "../lib/format";
import { StatCardGrid } from "../components/StatCardGrid";
import { Panel } from "../components/Panel";
import { StackedBar } from "../components/StackedBar";
import { Loading, ErrorBox, Empty } from "../components/Feedback";

export function AttributionView() {
  const res = useApi(() => api.getAttribution(), []);
  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorBox message={`加载归因失败：${res.error}`} />;

  const r = res.data;
  const avg = r?.avg_components || {};
  const segments = [
    { label: "system", value: avg.system || 0, color: "var(--accent)" },
    { label: "tools", value: avg.tools || 0, color: "#8ab4ff" },
    { label: "history", value: avg.history || 0, color: "var(--warn)" },
    { label: "user", value: avg.user || 0, color: "var(--ok)" },
  ];
  const totalAttr = segments.reduce((s, c) => s + c.value, 0);
  const histPct =
    totalAttr > 0 ? Math.round(((avg.history || 0) * 100) / totalAttr) : 0;
  const recent = r?.recent || [];

  return (
    <div>
      <StatCardGrid
        items={[
          { label: "system 平均", value: fmtNum(avg.system) },
          { label: "tools 平均", value: fmtNum(avg.tools) },
          { label: "history 平均", value: fmtNum(avg.history) },
          { label: "user 平均", value: fmtNum(avg.user) },
        ]}
      />

      <Panel>
        <h2>组件占比（平均）</h2>
        {totalAttr > 0 ? (
          <>
            <StackedBar segments={segments} />
            {histPct >= 40 && (
              <div className="alert-body" style={{ marginTop: 10 }}>
                history 占注入的 <strong>{histPct}%</strong>
                ，是可优化的主要部分——精简历史可显著降低每轮输入 token。
              </div>
            )}
          </>
        ) : (
          <Empty text="暂无组件数据" />
        )}
      </Panel>

      <Panel>
        <h2>最近请求归因</h2>
        {recent.length === 0 ? (
          <Empty text="暂无归因数据" />
        ) : (
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>会话</th>
                <th>注入 token</th>
                <th>system</th>
                <th>tools</th>
                <th>history</th>
                <th>user</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((it, i) => {
                const a = it.attribution || {};
                return (
                  <tr key={i}>
                    <td>{shortTime(it.created_at)}</td>
                    <td className="mono" title={it.umo || ""}>
                      {it.umo || "-"}
                    </td>
                    <td>{it.injection_total == null ? "-" : fmtNum(it.injection_total)}</td>
                    <td>{fmtNum(a.system)}</td>
                    <td>{fmtNum(a.tools)}</td>
                    <td>{fmtNum(a.history)}</td>
                    <td>{fmtNum(a.user)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
