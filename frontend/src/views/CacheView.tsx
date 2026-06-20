import { api } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { fmtNum } from "../lib/format";
import type { Window } from "../lib/types";
import { StatCardGrid } from "../components/StatCardGrid";
import { Panel } from "../components/Panel";
import { StackedBar } from "../components/StackedBar";
import { CacheEventRow } from "../components/CacheEventRow";
import { Loading, ErrorBox, Empty } from "../components/Feedback";

export function CacheView({
  window: win,
  refreshNonce,
}: {
  window: Window;
  refreshNonce: number;
}) {
  const res = useApi(() => api.getCache(win), [win, refreshNonce]);
  if (res.loading && !res.data) return <Loading />;
  if (res.error) return <ErrorBox message={`加载缓存诊断失败：${res.error}`} />;

  const r = res.data;
  const events = r?.events || [];
  const tCached = r?.total_input_cached || 0;
  const tOther = r?.total_input_other || 0;
  const tOut = r?.total_output || 0;
  const totalTokens = tCached + tOther + tOut;
  const segs = [
    { label: "缓存命中", value: tCached, color: "var(--ok)" },
    { label: "缓存未命中", value: tOther, color: "var(--warn)" },
    { label: "输出", value: tOut, color: "var(--accent)" },
  ];
  return (
    <div>
      <StatCardGrid
        items={[
          {
            label: "平均缓存命中率",
            value: `${r?.cache_hit_rate || 0}%`,
            sub: `${r?.samples || 0} 样本`,
          },
          { label: "破坏事件", value: fmtNum(events.length) },
          {
            label: "非缓存输入 token",
            value: fmtNum(tOther),
            sub: "可经提升命中率优化",
          },
        ]}
      />

      <Panel>
        <h2>Token 占比</h2>
        {totalTokens > 0 ? (
          <StackedBar segments={segs} />
        ) : (
          <Empty text="暂无 token 数据" />
        )}
      </Panel>

      <Panel className="potential">
        <h2>优化潜力</h2>
        <div className="alert-body">
          缓存命中单价通常为非缓存的 <strong>1/10</strong>。当前非缓存输入{" "}
          <strong>{fmtNum(r?.total_input_other || 0)}</strong>{" "}
          token，提升命中率可直接降低这部分输入成本。重点排查：system prompt
          稳定性、上下文是否被重置、工具定义是否频繁变化。
        </div>
      </Panel>

      <Panel>
        <h2>缓存破坏事件（最近）</h2>
        {events.length === 0 ? (
          <Empty text="未检测到缓存破坏事件" />
        ) : (
          <table className="cache-events">
            <thead>
              <tr>
                <th>类型</th>
                <th>严重度</th>
                <th>会话</th>
                <th>时间</th>
                <th>前后变化</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {events.map((ev, i) => (
                <CacheEventRow key={i} ev={ev} />
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
