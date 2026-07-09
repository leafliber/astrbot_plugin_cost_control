import { api } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { fmtNum, shortTime } from "../lib/format";
import type { Window } from "../lib/types";
import { StatCardGrid } from "../components/StatCardGrid";
import { Panel } from "../components/Panel";
import { StackedBar } from "../components/StackedBar";
import { Loading, ErrorBox, Empty } from "../components/Feedback";

export function AttributionView({
  window: win,
  refreshNonce,
}: {
  window: Window;
  refreshNonce: number;
}) {
  const res = useApi(() => api.getAttribution(win), [win, refreshNonce]);
  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorBox message={`加载上下文失败：${res.error}`} />;

  const r = res.data;
  const avg = r?.avg_components || {};
  const segments = [
    {
      label: "system",
      value: avg.system || 0,
      color: "var(--accent)",
      tooltip: "系统提示词，定义 LLM 的角色与行为规则。来源：AstrBot 全局配置、插件注入的系统指令。",
    },
    {
      label: "tools",
      value: avg.tools || 0,
      color: "#8ab4ff",
      tooltip: "工具/函数定义（function calling），声明 LLM 可调用的工具。来源：已注册的函数工具、插件提供的工具。",
    },
    {
      label: "history",
      value: avg.history || 0,
      color: "var(--warn)",
      tooltip: "对话历史，即之前的多轮消息上下文。来源：会话记录中的历史消息，随轮次累积增长。",
    },
    {
      label: "user",
      value: avg.user || 0,
      color: "var(--ok)",
      tooltip: "当前轮用户输入，包括文本与图片/音频等媒体。来源：用户的原始发言。",
    },
    {
      label: "extra",
      value: avg.extra || 0,
      color: "#c084fc",
      tooltip: "插件注入的额外用户内容块。来源：其他插件通过 extra_user_content_parts 追加的指令、提醒、上下文等。",
    },
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
          { label: "extra 平均", value: fmtNum(avg.extra) },
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
            {r?.estimation_note && (
              <div className="muted small" style={{ marginTop: 8 }}>
                {r.estimation_note}
              </div>
            )}
          </>
        ) : (
          <Empty text="暂无组件数据" />
        )}
      </Panel>

      <Panel>
        <h2>最近请求上下文</h2>
        {recent.length === 0 ? (
          <Empty text="暂无上下文数据" />
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
                <th>extra</th>
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
                    <td>{fmtNum(a.extra)}</td>
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
