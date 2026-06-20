import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { fmtCost, fmtNum } from "../lib/format";
import type { BudgetMetricKey, RawStrategy, Strategy } from "../lib/types";
import { Panel } from "../components/Panel";
import { Segmented } from "../components/Segmented";
import { ProgressBar } from "../components/ProgressBar";
import { StrategyCard } from "../components/StrategyCard";
import { Loading, ErrorBox } from "../components/Feedback";

const DIM_META: [string, string][] = [
  ["per_session_daily", "单会话每日"],
  ["per_user_daily", "单用户每日"],
  ["per_model_daily", "单模型每日"],
  ["global_daily", "全局每日"],
  ["global_monthly", "全局每月"],
];

function normalizeStrategy(s: RawStrategy): Strategy {
  return {
    action: s.action || "stop_llm",
    provider_ids: Array.isArray(s.provider_ids) ? s.provider_ids.slice() : [],
    token_limit: s.token_limit || 0,
    message: s.message || "",
    enabled: s.enabled !== false,
  };
}

export function BudgetsView() {
  const budgetsRes = useApi(() => api.getBudgets(), []);
  const provsRes = useApi(() => api.getProviders(), []);
  const data = budgetsRes.data;

  const [tokens, setTokens] = useState<Record<string, number>>({});
  const [cost, setCost] = useState<Record<string, number>>({});
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [metric, setMetric] = useState<BudgetMetricKey>("token");
  const [saveResult, setSaveResult] = useState("");

  useEffect(() => {
    if (!data) return;
    setTokens({ ...(data.limits || {}) });
    setCost({ ...(data.limits_cost || {}) });
    setStrategies((data.strategies || []).map(normalizeStrategy));
  }, [data]);

  if (budgetsRes.loading && !data) return <Loading />;
  if (budgetsRes.error) return <ErrorBox message={`加载预算失败：${budgetsRes.error}`} />;

  const dims = data?.dimensions || {};
  const provs = provsRes.data?.providers || [];
  const state = metric === "cost" ? cost : tokens;
  const setState = metric === "cost" ? setCost : setTokens;
  const provHint = provs.map((p) => p.id + (p.model ? `(${p.model})` : "")).join("、");

  const updateLimit = (key: string, raw: string) =>
    setState((prev) => ({
      ...prev,
      [key]:
        metric === "cost"
          ? Math.max(0, +raw || 0)
          : Math.max(0, parseInt(raw, 10) || 0),
    }));

  const updateStrategy = (i: number, patch: Partial<Strategy>) =>
    setStrategies((prev) => prev.map((s, idx) => (idx === i ? { ...s, ...patch } : s)));
  const moveStrategy = (i: number, dir: "up" | "down") =>
    setStrategies((prev) => {
      const next = prev.slice();
      if (dir === "up" && i > 0) {
        [next[i - 1], next[i]] = [next[i], next[i - 1]];
      } else if (dir === "down" && i < next.length - 1) {
        [next[i + 1], next[i]] = [next[i], next[i + 1]];
      }
      return next;
    });
  const deleteStrategy = (i: number) =>
    setStrategies((prev) => prev.filter((_, idx) => idx !== i));
  const addStrategy = () =>
    setStrategies((prev) => [
      ...prev,
      { action: "stop_llm", provider_ids: [], token_limit: 0, message: "", enabled: true },
    ]);

  const save = async () => {
    setSaveResult("保存中…");
    try {
      const res = await api.postSaveConfig({
        budgets: tokens,
        budgets_cost: cost,
        over_limit_strategies: strategies,
      });
      setSaveResult(`✅ 已保存（${(res.saved || []).join(", ")}），立即生效`);
      budgetsRes.refetch();
    } catch (e) {
      setSaveResult(`❌ 保存失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  return (
    <div>
      <Panel>
        <div className="budget-head">
          <h2>预算阈值</h2>
          <Segmented
            variant="weak"
            value={metric}
            onChange={(v) => setMetric(v as BudgetMetricKey)}
            options={[
              { value: "token", label: "Token" },
              { value: "cost", label: "花费 $" },
            ]}
          />
        </div>
        <table>
          <thead>
            <tr>
              <th>维度</th>
              <th>上限 {metric === "cost" ? "($)" : "(token)"}，0=不限</th>
              <th>当前消耗</th>
              <th>进度</th>
            </tr>
          </thead>
          <tbody>
            {DIM_META.map(([key, label]) => {
              const limit = state[key] || 0;
              const dim = (dims[key] || {})[metric] || {};
              const used = dim.used || 0;
              const ratio = dim.ratio || 0;
              const topKey = dim.top_key || "";
              const note = dim.note || "";
              return (
                <tr key={key}>
                  <td>{label}</td>
                  <td>
                    <input
                      type="number"
                      min="0"
                      step={metric === "cost" ? "0.01" : undefined}
                      className="budget-input"
                      style={{ width: 120 }}
                      value={limit}
                      onChange={(e) => updateLimit(key, e.target.value)}
                    />
                  </td>
                  <td>
                    {metric === "cost" ? fmtCost(used) : fmtNum(used)}
                    {topKey && <span className="muted"> ({topKey})</span>}
                    {note && <div className="muted small">{note}</div>}
                  </td>
                  <td style={{ minWidth: 200 }}>
                    {limit <= 0 ? (
                      <span className="muted">未设上限</span>
                    ) : (
                      <ProgressBar ratio={ratio} warnAt={80} badAt={100}>
                        {fmtNum(used)} / {fmtNum(limit)} ({ratio || 0}%)
                      </ProgressBar>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Panel>

      <Panel>
        <h2>超限处理策略（按序尝试）</h2>
        <div className="muted small" style={{ marginBottom: 8 }}>
          超限时从上到下依次求值：<b>切换备用 Provider</b>{" "}
          按其列表逐个尝试，首个成功即返回响应；全部失败或遇到 <b>拦截</b> 则终止。
        </div>
        {provHint && (
          <div className="muted small" style={{ marginBottom: 8 }}>
            可用 Provider：{provHint}
          </div>
        )}
        <datalist id="prov-opts">
          {provs.map((p) => (
            <option key={p.id} value={p.id}>
              {p.id + (p.model ? ` (${p.model})` : "")}
            </option>
          ))}
        </datalist>
        <div className="strategy-list">
          {strategies.length === 0 ? (
            <div className="muted small">暂无策略（超限时默认拦截）</div>
          ) : (
            strategies.map((s, i) => (
              <StrategyCard
                key={i}
                strategy={s}
                index={i}
                total={strategies.length}
                onChange={(patch) => updateStrategy(i, patch)}
                onMove={(dir) => moveStrategy(i, dir)}
                onDelete={() => deleteStrategy(i)}
              />
            ))
          )}
        </div>
        <button className="btn" style={{ marginTop: 8 }} onClick={addStrategy}>
          + 添加策略
        </button>
      </Panel>

      <div className="row" style={{ alignItems: "center", gap: 12, marginTop: 4 }}>
        <button className="btn primary" onClick={save}>
          保存（热生效）
        </button>
        <span className="muted">{saveResult}</span>
      </div>
    </div>
  );
}
