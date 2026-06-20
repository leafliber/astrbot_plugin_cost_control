import { useMemo, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { fmtCost, fmtNum, shortModelName, shortTime, shortUmo } from "../lib/format";
import type {
  RecordsFilter,
  RecordsOrderBy,
  RecordsOrderDir,
  RecordsPreset,
} from "../lib/types";
import { Panel } from "../components/Panel";
import { Segmented } from "../components/Segmented";
import { ProgressBar } from "../components/ProgressBar";
import { Loading, ErrorBox, Empty } from "../components/Feedback";

const DEFAULT_FILTER: RecordsFilter = {
  preset: "7d",
  start: "",
  end: "",
  model: "",
  umo: "",
  provider: "",
  order_by: "created_at",
  order_dir: "desc",
};

function rangeParams(filter: RecordsFilter): { start: string; end: string } {
  const now = new Date();
  const end = now.toISOString().slice(0, 10);
  if (filter.preset === "today") return { start: end, end };
  if (filter.preset === "7d") {
    const d = new Date(now);
    d.setDate(d.getDate() - 6);
    return { start: d.toISOString().slice(0, 10), end };
  }
  if (filter.preset === "30d") {
    const d = new Date(now);
    d.setDate(d.getDate() - 29);
    return { start: d.toISOString().slice(0, 10), end };
  }
  return { start: filter.start || "", end: filter.end || "" };
}

export function RecordsView() {
  const [filter, setFilter] = useState<RecordsFilter>(DEFAULT_FILTER);
  const [aggMode, setAggMode] = useState<"model" | "umo">("model");

  const range = useMemo(
    () => rangeParams(filter),
    [filter.preset, filter.start, filter.end],
  );

  // 模型下拉选项：从 overview 的 cost_by_model 取（一次性，不随筛选变）
  const modelsReq = useApi(() => api.getOverview("daily"), []);
  const models = (modelsReq.data?.cost_by_model || []).map((m) => m.model);

  const aggRes = useApi(
    () =>
      api.getRecordsAggregate({
        by: aggMode,
        umo: filter.umo,
        provider: filter.provider,
        model: filter.model,
        start: range.start,
        end: range.end,
      }),
    [
      aggMode,
      filter.umo,
      filter.provider,
      filter.model,
      range.start,
      range.end,
    ],
  );

  const rowsRes = useApi(
    () =>
      api.getRecords({
        umo: filter.umo,
        provider: filter.provider,
        model: filter.model,
        start: range.start,
        end: range.end,
        order_by: filter.order_by,
        order_dir: filter.order_dir,
        limit: 300,
      }),
    [
      filter.umo,
      filter.provider,
      filter.model,
      range.start,
      range.end,
      filter.order_by,
      filter.order_dir,
    ],
  );

  const update = (patch: Partial<RecordsFilter>) => setFilter((f) => ({ ...f, ...patch }));

  const groups = aggRes.data?.groups || [];
  const rows = rowsRes.data || [];
  const sums = rows.reduce<{ input: number; cached: number; output: number; creation: number; cost: number }>(
    (acc, r) => {
      acc.input += r.token_input_other || 0;
      acc.cached += r.token_input_cached || 0;
      acc.output += r.token_output || 0;
      acc.creation += r.cache_creation || 0;
      acc.cost += r.cost || 0;
      return acc;
    },
    { input: 0, cached: 0, output: 0, creation: 0, cost: 0 },
  );

  return (
    <div>
      <div className="toolbar records-toolbar">
        <Segmented
          value={filter.preset}
          onChange={(v) => update({ preset: v as RecordsPreset })}
          options={[
            { value: "today", label: "今日" },
            { value: "7d", label: "7日" },
            { value: "30d", label: "30日" },
            { value: "custom", label: "自定义" },
          ]}
        />
        {filter.preset === "custom" && (
          <span className="custom-range">
            <input
              type="date"
              value={filter.start}
              onChange={(e) => update({ start: e.target.value, preset: "custom" })}
            />
            {" ~ "}
            <input
              type="date"
              value={filter.end}
              onChange={(e) => update({ end: e.target.value, preset: "custom" })}
            />
          </span>
        )}
        <select value={filter.model} onChange={(e) => update({ model: e.target.value })}>
          <option value="">全部模型</option>
          {models.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <input
          defaultValue={filter.umo}
          placeholder="按会话 UMO 筛选"
          onBlur={(e) => update({ umo: e.target.value.trim() })}
          onKeyDown={(e) => {
            if (e.key === "Enter") update({ umo: (e.target as HTMLInputElement).value.trim() });
          }}
        />
        <input
          defaultValue={filter.provider}
          placeholder="Provider ID"
          onBlur={(e) => update({ provider: e.target.value.trim() })}
          onKeyDown={(e) => {
            if (e.key === "Enter")
              update({ provider: (e.target as HTMLInputElement).value.trim() });
          }}
        />
        <select
          value={filter.order_by}
          onChange={(e) => update({ order_by: e.target.value as RecordsOrderBy })}
        >
          <option value="created_at">按时间</option>
          <option value="token_input_other">按输入</option>
          <option value="token_output">按输出</option>
        </select>
        <button
          className="btn"
          title="升降序"
          onClick={() =>
            update({
              order_dir: (filter.order_dir === "desc" ? "asc" : "desc") as RecordsOrderDir,
            })
          }
        >
          {filter.order_dir === "desc" ? "↓" : "↑"}
        </button>
      </div>

      <Panel className="agg-panel">
        <div className="agg-head">
          <h2 style={{ margin: 0 }}>交叉聚合</h2>
          <Segmented
            variant="weak"
            value={aggMode}
            onChange={(v) => setAggMode(v as "model" | "umo")}
            options={[
              { value: "model", label: "按模型" },
              { value: "umo", label: "按会话" },
            ]}
          />
        </div>
        {aggRes.loading && !aggRes.data ? (
          <Loading message="加载聚合…" />
        ) : aggRes.error ? (
          <div className="muted">聚合失败：{aggRes.error}</div>
        ) : groups.length === 0 ? (
          <Empty text="暂无聚合数据" />
        ) : (
          <table>
            <thead>
              <tr>
                <th>{aggMode === "model" ? "模型" : "会话"}</th>
                <th>调用</th>
                <th>token 合计</th>
                <th>成本</th>
                <th>占比</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <tr key={g.key}>
                  <td className="mono">
                    {aggMode === "model" ? shortModelName(g.key) : shortUmo(g.key)}
                  </td>
                  <td>{fmtNum(g.count)}</td>
                  <td>{fmtNum(g.tokens)}</td>
                  <td>{fmtCost(g.cost)}</td>
                  <td style={{ minWidth: 160 }}>
                    <ProgressBar ratio={g.pct} warnAt={25} badAt={50}>
                      {g.pct}%
                    </ProgressBar>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel>
        {rowsRes.loading && !rowsRes.data ? (
          <Loading />
        ) : rowsRes.error ? (
          <ErrorBox message={`加载失败：${rowsRes.error}`} />
        ) : rows.length === 0 ? (
          <Empty text="暂无明细记录" />
        ) : (
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>会话</th>
                <th>模型</th>
                <th>Provider</th>
                <th>输入</th>
                <th>缓存</th>
                <th>输出</th>
                <th>cache写入</th>
                <th>注入</th>
                <th>成本</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td>{shortTime(r.created_at)}</td>
                  <td className="mono" title={r.umo || ""}>
                    {shortUmo(r.umo)}
                  </td>
                  <td className="mono" title={r.provider_model || ""}>
                    {r.provider_model || "-"}
                  </td>
                  <td className="mono">{r.provider_id || "-"}</td>
                  <td>{fmtNum(r.token_input_other)}</td>
                  <td>{fmtNum(r.token_input_cached)}</td>
                  <td>{fmtNum(r.token_output)}</td>
                  <td>{fmtNum(r.cache_creation)}</td>
                  <td>{r.injection_total == null ? "-" : fmtNum(r.injection_total)}</td>
                  <td>{fmtCost(r.cost)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="sum-row">
                <td colSpan={4}>合计（{rows.length} 条）</td>
                <td>{fmtNum(sums.input)}</td>
                <td>{fmtNum(sums.cached)}</td>
                <td>{fmtNum(sums.output)}</td>
                <td>{fmtNum(sums.creation)}</td>
                <td></td>
                <td>{fmtCost(sums.cost)}</td>
              </tr>
            </tfoot>
          </table>
        )}
      </Panel>
    </div>
  );
}
