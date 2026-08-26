import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../hooks/useApi";
import { useAutoSave } from "../hooks/useAutoSave";
import { fmtNum } from "../lib/format";
import type {
  CandidateBrief,
  DeletedProviderInfo,
  MatchedDefault,
  PriceEntry,
  PricingCluster,
  PricingUnpriced,
  PriceSelection,
  ProviderModelInfo,
  SourceStatus,
  UserPricingEntry,
} from "../lib/types";
import { Panel } from "../components/Panel";
import { Button } from "../components/Button";
import { SaveToast } from "../components/SaveToast";
import { Loading, ErrorBox } from "../components/Feedback";
import { PricingCatalog } from "../components/PricingCatalog";
import {
  DraftEntry,
  ProviderPricingCard,
  draftToEntry,
  entryToDraft,
  isDraftEmpty,
  normalizeDefaultCurrency,
} from "../components/ProviderPricingCard";
import { SourceStatusBar } from "../components/price/SourceStatusBar";

interface PricingDisplayProvider {
  id: string;
  type?: string;
  candidates: string[];
  matchedDefault: MatchedDefault | null;
  isDeletedResidue?: boolean;
}

export function PricingView({ refreshNonce }: { refreshNonce: number }) {
  const res = useApi(() => api.getPricing(), [refreshNonce]);
  const data = res.data;
  const [drafts, setDrafts] = useState<Record<string, DraftEntry>>({});
  const [multiplierDrafts, setMultiplierDrafts] = useState<
    Record<string, string>
  >({});
  const [resetResult, setResetResult] = useState("");
  // 两段式确认：首次点击武装，4 秒内再次点击执行
  const [resetArmed, setResetArmed] = useState(false);
  const resetArmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [ready, setReady] = useState(false);
  // 跳转信号：点击未定价告警行时递增，传给对应 provider 卡片触发脉冲动画
  const [highlightTarget, setHighlightTarget] = useState<string | null>(null);
  const [highlightSignal, setHighlightSignal] = useState(0);
  const [selectedClusterId, setSelectedClusterId] = useState("");
  // 卡片展开态记忆（providerId 粒度），过滤/切聚类重挂载不丢
  const [expandedProviders, setExpandedProviders] = useState<Set<string>>(
    () => new Set(),
  );
  const toggleExpandedProvider = (pid: string, v: boolean) => {
    setExpandedProviders((prev) => {
      const next = new Set(prev);
      if (v) next.add(pid);
      else next.delete(pid);
      return next;
    });
  };
  // 局部未定价告警覆盖：保存后单独刷新，避免整页 refetch 导致闪烁
  const [unpricedOverride, setUnpricedOverride] = useState<
    PricingUnpriced[] | null
  >(null);
  // 多源：同步/选取状态
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState("");
  const [autoSync, setAutoSync] = useState(false);
  // 乐观更新覆盖：选取/取消后即时更新，避免整页 refetch 导致"等半天"
  const [selectionsOverride, setSelectionsOverride] = useState<
    Record<string, Record<string, PriceSelection>> | null
  >(null);
  const [candidatesOverride, setCandidatesOverride] = useState<
    Record<string, Record<string, CandidateBrief[]>> | null
  >(null);
  const [autoSelectedOverride, setAutoSelectedOverride] = useState<
    Record<string, Record<string, string>> | null
  >(null);
  // 正在选取/取消的 (provider|model) 键，按模型级 loading
  const [selectingKey, setSelectingKey] = useState<string | null>(null);

  // 新建草稿的默认计价货币：跟随主货币（后端 /pricing 已返回 currency_symbol）
  const defaultCurrency = normalizeDefaultCurrency(data?.currency_symbol);

  useEffect(() => {
    if (!data) return;
    const next: Record<string, DraftEntry> = {};
    const userPricing = data.user_pricing || {};
    for (const [pid, entry] of Object.entries(userPricing)) {
      next[pid] = entryToDraft(entry, defaultCurrency);
    }
    setDrafts(next);
    const nextMultipliers: Record<string, string> = {};
    const currentClusterIds = new Set(
      (data.pricing_clusters || []).map((cluster) => cluster.id),
    );
    for (const [clusterId, multiplier] of Object.entries(
      data.pricing_multipliers || {},
    )) {
      if (currentClusterIds.size > 0 && !currentClusterIds.has(clusterId)) {
        continue;
      }
      nextMultipliers[clusterId] = String(multiplier);
    }
    setMultiplierDrafts(nextMultipliers);
    setReady(true);
    setUnpricedOverride(null); // 新数据到达时清除覆盖
    setSelectionsOverride(null);
    setCandidatesOverride(null);
    setAutoSelectedOverride(null);
  }, [data, defaultCurrency]);

  // 读取 auto_sync 配置
  useEffect(() => {
    let mounted = true;
    void api
      .getConfig()
      .then((config) => {
        const priceSync = config.price_sync;
        const enabled =
          typeof priceSync === "object" &&
          priceSync !== null &&
          (priceSync as Record<string, unknown>).auto_enabled === true;
        if (mounted) setAutoSync(enabled);
      })
      .catch(() => {
        // 读取失败时保持默认关闭；不影响手动同步。
      });
    return () => {
      mounted = false;
    };
  }, [refreshNonce]);

  const providerModels: ProviderModelInfo[] = data?.provider_models || [];
  const defaults: Record<string, PriceEntry> = data?.defaults || {};
  const unpriced = unpricedOverride ?? data?.unpriced ?? [];
  const sources: Record<string, SourceStatus> = data?.sources || {};
  // 乐观更新：override 优先于 data
  const selections: Record<string, Record<string, PriceSelection>> =
    selectionsOverride ?? data?.selections ?? {};
  const candidateMap: Record<string, Record<string, CandidateBrief[]>> =
    candidatesOverride ?? data?.candidates ?? {};
  const autoSelected: Record<string, Record<string, string>> =
    autoSelectedOverride ?? data?.auto_selected ?? {};

  // 当前配置中的 provider ID 集合
  const configIds = useMemo(
    () => new Set(providerModels.map((p) => p.id)),
    [providerModels],
  );

  // 后端返回所有「已从当前配置删除，但仍有历史用量或旧定价」的 Provider。
  // 兼容旧后端：缺少 deleted_providers 字段时，仍从定价和未定价用量推导。
  const deletedProviders = useMemo<DeletedProviderInfo[]>(() => {
    if (data?.deleted_providers) return data.deleted_providers;
    const byId = new Map<string, DeletedProviderInfo>();
    for (const pid of Object.keys(drafts)) {
      if (!configIds.has(pid)) {
        byId.set(pid, {
          provider_id: pid,
          tokens: 0,
          count: 0,
          has_pricing: true,
        });
      }
    }
    for (const u of unpriced) {
      const pid = u.provider_id || "";
      if (!pid || configIds.has(pid)) continue;
      const item = byId.get(pid) || {
        provider_id: pid,
        tokens: 0,
        count: 0,
      };
      item.tokens += u.tokens || 0;
      item.count += u.count || 0;
      byId.set(pid, item);
    }
    return Array.from(byId.values()).sort(
      (a, b) => b.tokens - a.tokens || a.provider_id.localeCompare(b.provider_id),
    );
  }, [data?.deleted_providers, drafts, unpriced, configIds]);

  // 短名：取最后一个 / 后面的部分（如 newapi/image-ocr → image-ocr）
  const shortName = (id: string) => {
    const i = id.lastIndexOf("/");
    return i >= 0 ? id.slice(i + 1) : id;
  };

  const currentDisplayList = useMemo<PricingDisplayProvider[]>(
    () =>
      providerModels.map((p) => ({
        id: p.id,
        type: p.type,
        candidates: p.candidates,
        matchedDefault: p.matched_default ?? null,
      })),
    [providerModels],
  );

  const deletedDisplayList = useMemo<PricingDisplayProvider[]>(
    () =>
      deletedProviders.map((p) => ({
        id: p.provider_id,
        type: undefined,
        candidates: p.models || [],
        matchedDefault: p.matched_default ?? null,
        isDeletedResidue: true,
      })),
    [deletedProviders],
  );

  const pricingClusters = useMemo<PricingCluster[]>(() => {
    if (data?.pricing_clusters?.length) {
      return data.pricing_clusters
        .map((cluster) => ({
          ...cluster,
          provider_ids: (cluster.provider_ids || []).filter((id) =>
            configIds.has(id),
          ),
        }))
        .filter((cluster) => cluster.provider_ids.length > 0);
    }
    const grouped = new Map<string, PricingCluster>();
    for (const provider of providerModels) {
      const id = provider.supplier_id || provider.id;
      const cluster = grouped.get(id) || {
        id,
        name: provider.supplier_name || id,
        provider_ids: [],
      };
      cluster.provider_ids.push(provider.id);
      grouped.set(id, cluster);
    }
    return Array.from(grouped.values());
  }, [data?.pricing_clusters, providerModels, configIds]);

  useEffect(() => {
    if (!pricingClusters.some((cluster) => cluster.id === selectedClusterId)) {
      setSelectedClusterId(pricingClusters[0]?.id ?? "");
    }
  }, [pricingClusters, selectedClusterId]);

  const currentProviderById = useMemo(
    () => new Map(currentDisplayList.map((provider) => [provider.id, provider])),
    [currentDisplayList],
  );
  const clusterIdByProvider = useMemo(() => {
    const map = new Map<string, string>();
    for (const cluster of pricingClusters) {
      for (const providerId of cluster.provider_ids) map.set(providerId, cluster.id);
    }
    return map;
  }, [pricingClusters]);

  // 未定价告警按精确 provider_id 分组，用于可点击跳转
  const unpricedByProvider = useMemo(() => {
    type UGroup = { models: typeof unpriced; totalTokens: number };
    const map = new Map<string, UGroup>();
    for (const u of unpriced) {
      const pid = u.provider_id || "(未知)";
      const group = map.get(pid) || { models: [], totalTokens: 0 };
      group.models.push(u);
      group.totalTokens += u.tokens || 0;
      map.set(pid, group);
    }
    return Array.from(map.entries()).sort(
      (a, b) => b[1].totalTokens - a[1].totalTokens,
    );
  }, [unpriced]);

  // 存在未定价用量的精确 provider ID 集合，用于卡片背景色判定
  const unpricedIdSet = useMemo(() => {
    const s = new Set<string>();
    for (const u of unpriced) {
      const pid = u.provider_id || "";
      if (pid) s.add(pid);
    }
    return s;
  }, [unpriced]);

  const hasUnpricedUsage = (pid: string) => unpricedIdSet.has(pid);

  const updateDraft = (pid: string, patch: Partial<DraftEntry>) =>
    setDrafts((prev) => {
      const cur = prev[pid] ?? entryToDraft(undefined, defaultCurrency);
      return { ...prev, [pid]: { ...cur, ...patch } };
    });
  const clearDraft = (pid: string) =>
    setDrafts((prev) => {
      const next = { ...prev };
      delete next[pid];
      return next;
    });
  const ensureDraft = (pid: string): DraftEntry =>
    drafts[pid] ?? entryToDraft(undefined, defaultCurrency);

  const updateMultiplier = (clusterId: string, value: string) =>
    setMultiplierDrafts((prev) => ({ ...prev, [clusterId]: value }));

  const collect = (): Record<string, UserPricingEntry> => {
    const out: Record<string, UserPricingEntry> = {};
    for (const [pid, d] of Object.entries(drafts)) {
      if (isDraftEmpty(d)) continue;
      const entry = draftToEntry(d);
      if (entry) out[pid] = entry;
    }
    return out;
  };

  const collectMultipliers = (): Record<string, number> => {
    const out: Record<string, number> = {};
    for (const [clusterId, raw] of Object.entries(multiplierDrafts)) {
      const multiplier = Number(raw);
      if (
        !Number.isFinite(multiplier) ||
        multiplier < 0.01 ||
        multiplier > 100
      ) {
        throw new Error("聚类倍率必须在 0.01–100 之间");
      }
      if (Math.abs(multiplier - 1) > 1e-12) out[clusterId] = multiplier;
    }
    return out;
  };

  const payload = useMemo<{
    pricing: Record<string, UserPricingEntry> | null;
    pricing_multipliers: Record<string, number> | null;
    error?: string;
  }>(() => {
    try {
      return {
        pricing: collect(),
        pricing_multipliers: collectMultipliers(),
      };
    } catch (e) {
      return {
        pricing: null,
        pricing_multipliers: null,
        error: e instanceof Error ? e.message : String(e),
      };
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drafts, multiplierDrafts]);

  const { status, error, flush } = useAutoSave(
    payload,
    async (p) => {
      if (p.error) throw new Error(p.error);
      await api.postSaveConfig({
        pricing: p.pricing,
        pricing_multipliers: p.pricing_multipliers,
      });
      // 局部刷新未定价告警，避免整页 refetch 导致闪烁
      try {
        const fresh = await api.getPricing();
        setUnpricedOverride(fresh.unpriced ?? []);
      } catch {
        // 刷新失败不影响保存成功
      }
    },
    { enabled: ready },
  );

  const deleteResidualData = async (providerId: string) => {
    // 先落盘其它尚在防抖期内的价格修改，避免删除后刷新覆盖用户刚输入的内容。
    if (status === "saving") {
      throw new Error("价格配置正在保存，请稍后再试");
    }
    await flush();
    await api.postDeleteProviderData(providerId);
    setUnpricedOverride(null);
    res.refetch();
  };

  if (res.loading && !data) return <Loading />;
  if (res.error) return <ErrorBox message={`加载定价失败：${res.error}`} />;

  // 多源：同步
  const doSync = async (sourceIds?: string[]) => {
    setSyncing(true);
    setSyncMsg("同步中…");
    try {
      const r = await api.postPricingSync(sourceIds);
      const failed = r.results.filter((s) => s.status === "error");
      const updated = r.results.reduce((sum, s) => sum + s.models, 0);
      const tail = failed.length > 0 ? `；${failed.length} 个源失败` : "";
      setSyncMsg(`✅ 已同步 ${updated} 个模型${tail}`);
      res.refetch();
    } catch (e) {
      setSyncMsg(`❌ 同步失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSyncing(false);
    }
  };
  const toggleAutoSync = async (enabled: boolean) => {
    try {
      const cfg = await api.getConfig();
      const priceSync = { ...((cfg.price_sync as Record<string, unknown>) ?? {}) };
      priceSync.auto_enabled = enabled;
      await api.postSaveConfig({ price_sync: priceSync });
      setAutoSync(enabled);
      setSyncMsg(enabled ? "已启用每日自动同步" : "已关闭每日自动同步");
    } catch (e) {
      setSyncMsg(`❌ 更新自动同步失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };
  // 源开关：读最新 config → 改 price_sources → 保存 → 启用时立即拉该源
  const toggleSource = async (sourceId: string, enabled: boolean) => {
    try {
      const cfg = await api.getConfig();
      const ps = { ...((cfg.price_sources as Record<string, unknown>) ?? {}) };
      const entry = { ...((ps[sourceId] as Record<string, unknown>) ?? {}) };
      entry.enabled = enabled;
      if (sourceId.startsWith("newapi:") && !entry.provider_id) {
        entry.provider_id = sourceId.slice(7);
        entry.use_provider_key = true;
      }
      ps[sourceId] = entry;
      await api.postSaveConfig({ price_sources: ps });
      if (enabled) {
        await doSync([sourceId]);
      } else {
        res.refetch();
      }
    } catch (e) {
      setSyncMsg(`❌ 更新价格源失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };
  // 探测并启用 New API 源（F1.2/F6.2）
  const enableNewApiSource = async (pid: string) => {
    setSelectingKey(`${pid}|`);
    setSyncMsg(`探测 ${pid} …`);
    try {
      const det = await api.postPricingDetect(pid);
      if (!det.is_newapi) {
        setSyncMsg(`❌ ${pid} 未检测到 New API /api/pricing 接口`);
        return;
      }
      const cfg = await api.getConfig();
      const ps = { ...((cfg.price_sources as Record<string, unknown>) ?? {}) };
      // URL 去重：同 base_url 只建一个源；首次以 provider 短名作默认名，可后续重命名
      const sid = det.existing_source || `newapi:${shortName(pid)}`;
      ps[sid] = {
        ...(ps[sid] as Record<string, unknown> | undefined),
        enabled: true,
        provider_id: (ps[sid] as { provider_id?: string } | undefined)?.provider_id ?? pid,
        base_url: det.base_url ?? "",
        use_provider_key: true,
      };
      await api.postSaveConfig({ price_sources: ps });
      setSyncMsg(
        det.existing_source
          ? `✅ ${pid} 复用已有源 ${sid}，正在拉取价格…`
          : `✅ ${sid} 已启用为 New API 源，正在拉取价格…`,
      );
      await doSync([sid]);
    } catch (e) {
      setSyncMsg(`❌ 探测失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSelectingKey(null);
    }
  };
  const disableNewApiSource = async (pid: string) => {
    const src = newApiSourceFor(pid);
    if (!src) return;
    await toggleSource(src.sourceId, false);
    setSyncMsg(`已停用源 ${src.sourceId}（已确认的选择不受影响）`);
  };

  // 乐观更新：选取后用返回值即时更新本地 override，不调 res.refetch()
  const selectCandidate = async (
    pid: string,
    model: string,
    priceKey: string,
  ) => {
    const key = `${pid}|${model}`;
    setSelectingKey(key);
    try {
      const { selected } = await api.postPricingSelect({
        provider_id: pid,
        model,
        price_key: priceKey,
      });
      // 即时更新 override
      setSelectionsOverride((prev) => ({
        ...(prev ?? data?.selections ?? {}),
        [pid]: { ...((prev ?? data?.selections ?? {})[pid] ?? {}), [model]: selected },
      }));
      // 候选/自动匹配移除该 model
      setCandidatesOverride((prev) => {
        const base = prev ?? data?.candidates ?? {};
        const forPid = { ...(base[pid] ?? {}) };
        delete forPid[model];
        return { ...base, [pid]: forPid };
      });
      setAutoSelectedOverride((prev) => {
        const base = prev ?? data?.auto_selected ?? {};
        const forPid = { ...(base[pid] ?? {}) };
        delete forPid[model];
        return { ...base, [pid]: forPid };
      });
      // 后台静默对齐一次全量数据（聚合计数/未定价等由服务端重算），不阻塞交互
      res.refetch();
    } catch (e) {
      setSyncMsg(`❌ 选用失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSelectingKey(null);
    }
  };
  // 乐观更新：取消选择，用返回值即时移除
  const resetSelection = async (pid: string, model: string) => {
    const key = `${pid}|${model}`;
    setSelectingKey(key);
    try {
      await api.postPricingSelectReset({ provider_id: pid, model });
      setSelectionsOverride((prev) => {
        const base = prev ?? data?.selections ?? {};
        const forPid = { ...(base[pid] ?? {}) };
        delete forPid[model];
        return { ...base, [pid]: forPid };
      });
      // 取消后该 model 回到无选择状态；候选/自动匹配需 refetch 才能恢复，
      // 但为避免"等半天"，触发一次后台静默 refetch 对齐数据（不阻塞 UI）
      res.refetch();
    } catch (e) {
      setSyncMsg(`❌ 清除失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSelectingKey(null);
    }
  };

  // provider 对应的 New API 源状态（URL 去重后源 id 不再等于 newapi:<pid>，
  // 按 记录的 provider_id 或默认短名 双路匹配）
  const newApiSourceFor = (pid: string) => {
    for (const sid of [`newapi:${shortName(pid)}`, `newapi:${pid}`]) {
      const st = sources[sid];
      if (st) return { sourceId: sid, enabled: !!st.enabled };
    }
    for (const [sid, st] of Object.entries(sources)) {
      if (sid.startsWith("newapi:") && st.provider_id === pid) {
        return { sourceId: sid, enabled: !!st.enabled };
      }
    }
    return undefined;
  };

  const reset = async () => {
    // 两段式确认：首次点击武装，4 秒内再次点击执行（替代 confirm，兼容嵌入式 webview）
    if (!resetArmed) {
      setResetArmed(true);
      setResetResult("⚠ 再次点击以确认重置");
      if (resetArmTimer.current) clearTimeout(resetArmTimer.current);
      resetArmTimer.current = setTimeout(() => {
        setResetArmed(false);
        setResetResult("");
      }, 4000);
      return;
    }
    if (resetArmTimer.current) clearTimeout(resetArmTimer.current);
    setResetArmed(false);
    setResetResult("重置中…");
    try {
      await api.postSaveConfig({ pricing: {}, pricing_multipliers: {} });
      setResetResult("✅ 已重置，立即生效");
      res.refetch();
    } catch (e) {
      setResetResult(`❌ 重置失败：${e instanceof Error ? e.message : String(e)}`);
    }
  };

  // 点击未定价告警行 → 跳转到对应 provider 卡片
  const jumpToProvider = (pid: string) => {
    const clusterId = clusterIdByProvider.get(pid);
    if (clusterId) setSelectedClusterId(clusterId);
    setHighlightTarget(pid);
    setHighlightSignal((s) => s + 1);
  };

  const defaultKeys = Object.keys(defaults).sort();

  // 统计概要数字
  const totalProviders = currentDisplayList.length;
  const unmatchedCount = currentDisplayList.filter(
    (p) => !p.matchedDefault && isDraftEmpty(ensureDraft(p.id)),
  ).length;

  const renderProviderCard = (p: PricingDisplayProvider) => (
    <ProviderPricingCard
      key={p.id}
      providerId={p.id}
      type={p.type}
      candidates={p.candidates}
      draft={ensureDraft(p.id)}
      matchedDefault={p.matchedDefault}
      hasUserOverride={!isDraftEmpty(ensureDraft(p.id))}
      isDeletedResidue={p.isDeletedResidue}
      hasUsage={hasUnpricedUsage(p.id)}
      highlightSignal={highlightTarget === p.id ? highlightSignal : undefined}
      selections={selections[p.id]}
      autoPriceKeys={autoSelected[p.id]}
      priceCandidatesByModel={candidateMap[p.id]}
      newApiSource={p.isDeletedResidue ? undefined : newApiSourceFor(p.id)}
      selectingKey={selectingKey ?? undefined}
      expanded={expandedProviders.has(p.id)}
      onExpandedChange={(v) => toggleExpandedProvider(p.id, v)}
      onSelectCandidate={(m, pk) => selectCandidate(p.id, m, pk)}
      onResetSelection={(m) => resetSelection(p.id, m)}
      onEnableNewApiSource={
        p.isDeletedResidue ? undefined : () => enableNewApiSource(p.id)
      }
      onDisableNewApiSource={
        p.isDeletedResidue ? undefined : () => disableNewApiSource(p.id)
      }
      onChange={(patch) => updateDraft(p.id, patch)}
      onClear={() => clearDraft(p.id)}
      onDeleteData={
        p.isDeletedResidue ? () => deleteResidualData(p.id) : undefined
      }
    />
  );

  return (
    <div>
      <Panel className="source-panel">
        <h2>价格源</h2>
        <SourceStatusBar
          sources={sources}
          syncing={syncing}
          onSync={() => void doSync()}
          onToggleSource={(sid, en) => void toggleSource(sid, en)}
          autoSync={autoSync}
          onToggleAutoSync={(enabled) => void toggleAutoSync(enabled)}
        />
        {syncMsg && <div className="muted small source-sync-msg">{syncMsg}</div>}
      </Panel>

      {unpriced.length > 0 && (
        <Panel className="alert-panel">
          <h2>未定价告警（{unpricedByProvider.length} 个 Provider）</h2>
          <div className="alert-body">
            以下 Provider 有用量但无定价匹配，成本被计为 <strong>$0</strong>。
            点击行可快速跳转到对应 Provider 定价卡片。
          </div>
          <div className="unpriced-groups">
            {unpricedByProvider.map(([pid, group]) => {
              const isDeletedResidue = !configIds.has(pid);
              return (
                <div
                  key={pid}
                  className={`unpriced-group-row ${isDeletedResidue ? "is-deleted-residue" : ""}`}
                  onClick={() => jumpToProvider(pid)}
                  title={isDeletedResidue ? "该 Provider 已从当前配置删除，点击查看残留数据" : "点击跳转到定价卡片"}
                >
                  <span className="mono unpriced-pid">{shortName(pid) || "(未知)"}</span>
                  {isDeletedResidue && (
                    <span className="unpriced-residue-tag">已删除供应商残留</span>
                  )}
                  <span className="unpriced-models">
                    {group.models.length} 个模型
                  </span>
                  <span className="unpriced-tokens">
                    {fmtNum(group.totalTokens)} token
                  </span>
                  <span className="unpriced-jump-hint">点击跳转 ▸</span>
                </div>
              );
            })}
          </div>
        </Panel>
      )}

      <Panel className="pricing-catalog-panel">
        <div className="pricing-header">
          <h2>供应商定价</h2>
          <div className="pricing-header-stats">
            <span className="muted small">
              {pricingClusters.length} 个 AstrBot 供应商 · {totalProviders}{" "}
              个现有模型配置
            </span>
            {unmatchedCount > 0 && (
              <span className="pricing-unmatched-count">
                {unmatchedCount} 个未定价
              </span>
            )}
          </div>
        </div>
        <div className="muted small pricing-catalog-help">
          仅显示 AstrBot 当前配置中的 Provider/模型，并按{" "}
          <strong>provider_source_id</strong>{" "}
          聚类。同一供应商的模型集中在右侧；内置价格只作为现有模型的默认匹配，展开卡片即可直接覆盖。
        </div>
        {pricingClusters.length > 0 ? (
          <PricingCatalog
            clusters={pricingClusters}
            selectedId={selectedClusterId}
            onSelect={setSelectedClusterId}
            multipliers={multiplierDrafts}
            onMultiplierChange={updateMultiplier}
            renderProvider={(providerId) => {
              const provider = currentProviderById.get(providerId);
              return provider ? renderProviderCard(provider) : null;
            }}
          />
        ) : (
          <div className="muted small" style={{ margin: "8px 0" }}>
            未获取到当前 AstrBot 的 provider 配置。可在 AstrBot
            主配置添加 provider 后重载插件。
          </div>
        )}
        <div className="row" style={{ marginTop: 8, gap: 10, alignItems: "center" }}>
          <Button
            onClick={reset}
            title="清空自定义定价，恢复内置默认匹配"
            variant={resetArmed ? "danger" : "default"}
          >
            {resetArmed ? "⚠ 确认重置" : "重置全部"}
          </Button>
          <span className="muted">{resetResult}</span>
        </div>
      </Panel>

      {deletedDisplayList.length > 0 && (
        <Panel>
          <div className="pricing-header">
            <h2>已删除供应商残留</h2>
            <span className="muted small">
              {deletedDisplayList.length} 个已不在 AstrBot 配置中的 Provider
            </span>
          </div>
          <div className="pricing-residue-help">
            以下内容不属于当前供应商聚类，仅用于清理历史用量、补充记录和旧定价。
          </div>
          <div className="overrides-list">
            {deletedDisplayList.map(renderProviderCard)}
          </div>
        </Panel>
      )}

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
