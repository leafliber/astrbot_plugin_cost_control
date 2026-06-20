import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../hooks/useApi";
import type {
  BudgetOverrideRow,
  BudgetResponse,
  FallbackProvider,
  Metric,
  OnExceeded,
  OverrideTarget,
  Provider,
} from "../lib/types";
import { Panel } from "../components/Panel";
import { Segmented } from "../components/Segmented";
import { GlobalDefaultsPanel } from "../components/GlobalDefaultsPanel";
import { OverridesPanel } from "../components/OverridesPanel";
import { FallbackProvidersPanel } from "../components/FallbackProvidersPanel";
import { Loading, ErrorBox } from "../components/Feedback";

const OVERRIDE_PRESETS: Record<OverrideTarget, string> = {
  umo: "",
  provider: "",
  user: "",
};

function emptyOverride(targetType: OverrideTarget = "umo"): BudgetOverrideRow {
  return {
    id: `ov-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    enabled: true,
    target_type: targetType,
    target_value: OVERRIDE_PRESETS[targetType],
    token_limit: 0,
    cost_limit: 0,
    on_exceeded: "stop" as OnExceeded,
    stop_message: "",
    fallback_provider_ids: [],
    fallback_token_limit: 0,
    current: { token: { used: 0, ratio: 0, exceeded: false }, cost: { used: 0, ratio: 0, exceeded: false } },
  };
}

export function BudgetsView() {
  const budgetsRes = useApi(() => api.getBudgets(), []);
  const provsRes = useApi(() => api.getProviders(), []);
  const data = budgetsRes.data;

  const [metric, setMetric] = useState<Metric>("token");
  const [tokens, setTokens] = useState<Record<string, number>>({});
  const [cost, setCost] = useState<Record<string, number>>({});
  const [overrides, setOverrides] = useState<BudgetOverrideRow[]>([]);
  const [fallbacks, setFallbacks] = useState<FallbackProvider[]>([]);
  const [defaultOn, setDefaultOn] = useState<OnExceeded>("stop");
  const [saveResult, setSaveResult] = useState("");

  useEffect(() => {
    if (!data) return;
    setTokens({ ...(data.limits || {}) });
    setCost({ ...(data.limits_cost || {}) });
    setOverrides((data.overrides || []).map((o) => ({ ...o })));
    setFallbacks((data.fallback_providers || []).map((f) => ({ ...f })));
    setDefaultOn(data.global_default_on_exceeded || "stop");
  }, [data]);

  const provs: Provider[] = provsRes.data?.providers || [];

  const sortedFallbackIds = useMemo(
    () => fallbacks.filter((f) => f.enabled).map((f) => f.id),
    [fallbacks],
  );

  if (budgetsRes.loading && !data) return <Loading />;
  if (budgetsRes.error)
    return <ErrorBox message={`加载预算失败：${budgetsRes.error}`} />;

  const dimensions = (data as BudgetResponse | undefined)?.dimensions || {};

  const updateLimit = (key: string, raw: string) =>
    setTokens((prev) => ({ ...prev, [key]: Math.max(0, parseInt(raw, 10) || 0) }));
  const updateLimitCost = (key: string, raw: string) =>
    setCost((prev) => ({ ...prev, [key]: Math.max(0, +raw || 0) }));

  const updateOverride = (i: number, patch: Partial<BudgetOverrideRow>) =>
    setOverrides((prev) => prev.map((o, idx) => (idx === i ? { ...o, ...patch } : o)));
  const moveOverride = (i: number, dir: "up" | "down") =>
    setOverrides((prev) => {
      const next = prev.slice();
      if (dir === "up" && i > 0) {
        [next[i - 1], next[i]] = [next[i], next[i - 1]];
      } else if (dir === "down" && i < next.length - 1) {
        [next[i + 1], next[i]] = [next[i], next[i + 1]];
      }
      return next;
    });
  const deleteOverride = (i: number) =>
    setOverrides((prev) => prev.filter((_, idx) => idx !== i));
  const addOverride = () =>
    setOverrides((prev) => [...prev, emptyOverride("umo")]);

  const updateFallback = (i: number, patch: Partial<FallbackProvider>) =>
    setFallbacks((prev) => prev.map((f, idx) => (idx === i ? { ...f, ...patch } : f)));
  const deleteFallback = (i: number) =>
    setFallbacks((prev) => prev.filter((_, idx) => idx !== i));
  const addFallback = (id = "") =>
    setFallbacks((prev) => [
      ...prev,
      { id: id || `prov_${prev.length + 1}`, enabled: true, note: "" },
    ]);

  const save = async () => {
    setSaveResult("保存中…");
    try {
      // 清理 overrides 中 target_value 为空的非法行
      const cleaned = overrides
        .filter((o) => o.target_value && o.target_value.trim())
        .map(({ current: _c, id: _id, ...rest }) => rest);
      const cleanedFb = fallbacks.filter((f) => f.id && f.id.trim());
      const res = await api.postSaveConfig({
        budgets: tokens,
        budgets_cost: cost,
        budget_overrides: cleaned,
        fallback_providers: cleanedFb,
        default_on_exceeded: defaultOn,
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
          <h2>预算总览（5 维全局默认）</h2>
          <Segmented
            variant="weak"
            value={metric}
            onChange={(v) => setMetric(v as Metric)}
            options={[
              { value: "token", label: "Token" },
              { value: "cost", label: "花费 $" },
            ]}
          />
        </div>
        <div className="muted small" style={{ marginBottom: 8 }}>
          切换顶部指标后，每张卡片的 token / cost 两行分别高亮。
          <code> per_*_daily </code>类维度显示的是本周期消耗最多的代表会话 / 模型，并非该维度的全量聚合（运行时按当前请求实时拦截）。
        </div>
        <GlobalDefaultsPanel
          limits={tokens}
          limitsCost={cost}
          dimensions={dimensions}
          metric={metric}
          onChangeLimit={updateLimit}
          onChangeLimitCost={updateLimitCost}
        />
      </Panel>

      <Panel>
        <h2>局部阈值（优先级高于全局）</h2>
        <OverridesPanel
          overrides={overrides}
          metric={metric}
          providers={provs}
          fallbackProviders={sortedFallbackIds.map((id) => ({ id, enabled: true }))}
          onChange={updateOverride}
          onMove={moveOverride}
          onDelete={deleteOverride}
          onAdd={addOverride}
        />
      </Panel>

      <Panel>
        <h2>备用 Provider 库</h2>
        <FallbackProvidersPanel
          providers={fallbacks}
          realProviders={provs}
          onChange={updateFallback}
          onDelete={deleteFallback}
          onAdd={addFallback}
        />
      </Panel>

      <Panel>
        <div className="budget-head">
          <h2>全局默认超限处理</h2>
        </div>
        <div className="muted small" style={{ marginBottom: 6 }}>
          当 override 未命中且全局 5 维超限时，按此选项处理。
        </div>
        <select
          value={defaultOn}
          onChange={(e) => setDefaultOn(e.target.value as OnExceeded)}
        >
          <option value="stop">硬拦截</option>
          <option value="fallback">切换备用 Provider（按备用库顺序）</option>
          <option value="warn">仅警告（不中断）</option>
        </select>
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
