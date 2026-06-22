import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { useAutoSave } from "../hooks/useAutoSave";
import { fmtNum } from "../lib/format";
import type { PriceEntry, ProviderModelInfo, UserPricingEntry } from "../lib/types";
import { Panel } from "../components/Panel";
import { Button } from "../components/Button";
import { SaveToast } from "../components/SaveToast";
import { Loading, ErrorBox } from "../components/Feedback";
import {
  DraftEntry,
  ProviderPricingCard,
  draftToEntry,
  entryToDraft,
  isDraftEmpty,
} from "../components/ProviderPricingCard";

export function PricingView() {
  const res = useApi(() => api.getPricing(), []);
  const data = res.data;
  const [drafts, setDrafts] = useState<Record<string, DraftEntry>>({});
  const [resetResult, setResetResult] = useState("");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!data) return;
    const next: Record<string, DraftEntry> = {};
    const userPricing = data.user_pricing || {};
    for (const [pid, entry] of Object.entries(userPricing)) {
      next[pid] = entryToDraft(entry);
    }
    setDrafts(next);
    setReady(true);
  }, [data]);

  if (res.loading && !data) return <Loading />;
  if (res.error) return <ErrorBox message={`加载定价失败：${res.error}`} />;

  const providerModels: ProviderModelInfo[] = data?.provider_models || [];
  const defaults: Record<string, PriceEntry> = data?.defaults || {};
  const unpriced = data?.unpriced || [];

  // 显示集合：当前 provider 列表 + 手动设过价但不在列表的「孤儿」provider
  const orphanIds = Object.keys(drafts).filter(
    (pid) => !providerModels.some((p) => p.id === pid),
  );
  const displayList: { id: string; type?: string; candidates: string[] }[] = [
    ...providerModels.map((p) => ({ id: p.id, type: p.type, candidates: p.candidates })),
    ...orphanIds.map((id) => ({ id, type: undefined, candidates: [] })),
  ];

  const updateDraft = (pid: string, patch: Partial<DraftEntry>) =>
    setDrafts((prev) => {
      const cur = prev[pid] ?? entryToDraft();
      return { ...prev, [pid]: { ...cur, ...patch } };
    });
  const clearDraft = (pid: string) =>
    setDrafts((prev) => {
      const next = { ...prev };
      delete next[pid];
      return next;
    });
  // 为未在 drafts 中的 provider 补一个空 draft（首次编辑时创建）
  const ensureDraft = (pid: string): DraftEntry =>
    drafts[pid] ?? entryToDraft(undefined);

  const collect = (): Record<string, UserPricingEntry> => {
    const out: Record<string, UserPricingEntry> = {};
    for (const [pid, d] of Object.entries(drafts)) {
      if (isDraftEmpty(d)) continue;
      const entry = draftToEntry(d);
      if (entry) out[pid] = entry;
    }
    return out;
  };

  // 自动保存 payload。draftToEntry 对非法数值会抛错——包裹后把错误塞进 payload，
  // onSave 见到即抛出，由 useAutoSave 转成错误浮层提示用户修正。
  const payload = useMemo<{ pricing: Record<string, UserPricingEntry> | null; error?: string }>(() => {
    try {
      return { pricing: collect() };
    } catch (e) {
      return { pricing: null, error: e instanceof Error ? e.message : String(e) };
    }
    // eslint-disable-next-line react-hooks/exhausting-deps
  }, [drafts]);

  const { status, error } = useAutoSave(
    payload,
    async (p) => {
      if (p.error) throw new Error(p.error);
      void (await api.postSaveConfig({ pricing: p.pricing }));
    },
    { enabled: ready },
  );

  const reset = async () => {
    if (!confirm("确定清空所有自定义定价、恢复内置默认匹配？")) return;
    setResetResult("重置中…");
    try {
      await api.postSaveConfig({ pricing: {} });
      setResetResult("✅ 已重置，立即生效");
      res.refetch();
    } catch (e) {
      setResetResult(`❌ 重置失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const defaultKeys = Object.keys(defaults).sort();

  return (
    <div>
      {unpriced.length > 0 && (
        <Panel className="alert-panel">
          <h2>未定价告警</h2>
          <div className="alert-body">
            以下 provider+模型有用量但解析不到定价，其成本被计为 <strong>$0</strong>
            ，会导致成本统计偏低。请为对应 provider 设置定价，或确认模型名能匹配内置默认。
          </div>
          <table>
            <thead>
              <tr>
                <th>Provider</th>
                <th>模型</th>
                <th>用量 token</th>
                <th>调用</th>
              </tr>
            </thead>
            <tbody>
              {unpriced.map((u, i) => (
                <tr key={`${u.provider_id || ""}-${u.model}-${i}`}>
                  <td className="mono">{u.provider_id || "-"}</td>
                  <td className="mono">{u.model}</td>
                  <td>{fmtNum(u.tokens)}</td>
                  <td>{fmtNum(u.count)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      <Panel>
        <h2>Provider 定价</h2>
        <div className="muted small" style={{ marginBottom: 8 }}>
          按 <strong>provider_id</strong> 设置定价：命中则按其计费模式生效；未设置时按模型名
          匹配下方内置默认（per_token）。修改后自动保存、即时热生效。
        </div>
        {displayList.length === 0 && (
          <div className="muted small" style={{ margin: "8px 0" }}>
            未获取到当前 AstrBot 的 provider 配置。可在 AstrBot 主配置添加 provider 后重载插件。
          </div>
        )}
        <div className="overrides-list">
          {displayList.map((p) => (
            <ProviderPricingCard
              key={p.id}
              providerId={p.id}
              type={p.type}
              candidates={p.candidates}
              draft={ensureDraft(p.id)}
              defaults={defaults}
              onChange={(patch) => updateDraft(p.id, patch)}
              onClear={() => clearDraft(p.id)}
            />
          ))}
        </div>
        <div className="row" style={{ marginTop: 8, gap: 10, alignItems: "center" }}>
          <Button onClick={reset} title="清空自定义定价，恢复内置默认匹配">
            重置全部
          </Button>
          <span className="muted">{resetResult}</span>
        </div>
      </Panel>

      {defaultKeys.length > 0 && (
        <details className="panel">
          <summary>
            内置默认单价（参考 OpenRouter，共 {defaultKeys.length} 个模型，per_token，只读）
          </summary>
          <div className="muted small" style={{ margin: "6px 0" }}>
            随插件版本更新；按模型名模糊匹配（前缀 / 子串），作为未设置 provider 定价时的回退基准。
          </div>
          <table>
            <thead>
              <tr>
                <th>模型</th>
                <th>输入</th>
                <th>缓存命中</th>
                <th>输出</th>
                <th>缓存写入</th>
              </tr>
            </thead>
            <tbody>
              {defaultKeys.map((k) => {
                const p = defaults[k] || {};
                return (
                  <tr key={k}>
                    <td className="mono">{k}</td>
                    <td>{p.input != null ? p.input : "-"}</td>
                    <td>{p.input_cached != null ? p.input_cached : "-"}</td>
                    <td>{p.output != null ? p.output : "-"}</td>
                    <td>{p.cache_creation != null ? p.cache_creation : "-"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </details>
      )}

      <SaveToast status={status} error={error} />
    </div>
  );
}
