import { useCallback, useEffect, useState } from "react";
import { useBridge } from "./hooks/useBridge";
import { useTheme } from "./hooks/useTheme";
import { useChartColors } from "./hooks/useChartColors";
import { Segmented } from "./components/Segmented";
import { Loading, ErrorBox } from "./components/Feedback";
import { OverviewView } from "./views/OverviewView";
import { RecordsView } from "./views/RecordsView";
import { BudgetsView } from "./views/BudgetsView";
import { CacheView } from "./views/CacheView";
import { AttributionView } from "./views/AttributionView";
import { PricingView } from "./views/PricingView";
import { SettingsView } from "./views/SettingsView";
import { api } from "./lib/api";
import { setCurrencyCode } from "./lib/format";
import type { Window } from "./lib/types";

const TABS = [
  { key: "overview", label: "总览" },
  { key: "records", label: "明细" },
  { key: "budgets", label: "预算" },
  { key: "cache", label: "缓存" },
  { key: "attribution", label: "上下文" },
  { key: "pricing", label: "定价" },
  { key: "settings", label: "设置" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export function App() {
  const { ready, ctx, failed } = useBridge();
  useTheme(ctx);
  const colors = useChartColors(!!ctx?.isDark);

  const [tab, setTab] = useState<TabKey>("overview");
  const [win, setWin] = useState<Window>("weekly");
  const [refreshNonce, setRefreshNonce] = useState(0);

  // 挂载后拉取 config，注入主货币代码到 format 模块（全局 fmtCost 用）。
  // 依赖 refreshNonce：每次刷新（含主货币切换）都重新拉取，确保货币代码同步。
  useEffect(() => {
    if (!ready) return;
    api
      .getConfig()
      .then((cfg) => {
        const cur = (cfg as Record<string, unknown>)?.currency_symbol;
        if (typeof cur === "string" && cur) setCurrencyCode(cur);
      })
      .catch(() => {});
  }, [ready, refreshNonce]);

  const refresh = () => setRefreshNonce((n) => n + 1);

  // 主货币切换回调：先重新拉取 config 更新货币代码，再刷新所有页面数据，
  // 确保其他页面重新渲染时 fmtCost 已使用新的货币符号。
  const handleCurrencyChanged = useCallback(async () => {
    try {
      const cfg = await api.getConfig();
      const cur = (cfg as Record<string, unknown>)?.currency_symbol;
      if (typeof cur === "string" && cur) setCurrencyCode(cur);
    } catch {
      /* 忽略，下方 refresh 仍会通过 effect 重试 */
    }
    setRefreshNonce((n) => n + 1);
  }, []);
  const bridgeInfo = ctx
    ? `${ctx.displayName || "插件"} · ${ctx.locale || ""}`
    : "";

  const status = failed ? "未连接" : ready ? "已连接" : "连接中…";

  return (
    <>
      <header className="topbar">
        <div className="title-group">
          <h1>成本控制</h1>
          {(tab === "overview" || tab === "attribution" || tab === "cache") && (
            <Segmented
              value={win}
              onChange={(v) => setWin(v)}
              options={[
                { value: "daily", label: "日" },
                { value: "weekly", label: "周" },
                { value: "monthly", label: "月" },
              ]}
            />
          )}
        </div>
        <div className="topbar-right">
          <span className="status">{status}</span>
          <button className="btn" title="刷新" onClick={refresh}>
            ↻
          </button>
        </div>
      </header>

      <nav className="tabs">
        {TABS.map((t) => (
          <button
            key={t.key}
            className={`tab ${t.key === tab ? "active" : ""}`.trim()}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main className="content">
        {failed ? (
          <ErrorBox message="bridge SDK 未注入（请在 AstrBot WebUI 插件页打开本页面）" />
        ) : !ready ? (
          <Loading />
        ) : tab === "overview" ? (
          <OverviewView
            window={win}
            refreshNonce={refreshNonce}
            colors={colors}
            onNavigate={(t) => setTab(t)}
          />
        ) : tab === "records" ? (
          <RecordsView refreshNonce={refreshNonce} />
        ) : tab === "budgets" ? (
          <BudgetsView refreshNonce={refreshNonce} />
        ) : tab === "cache" ? (
          <CacheView window={win} refreshNonce={refreshNonce} />
        ) : tab === "attribution" ? (
          <AttributionView window={win} refreshNonce={refreshNonce} />
        ) : tab === "pricing" ? (
          <PricingView refreshNonce={refreshNonce} />
        ) : tab === "settings" ? (
          <SettingsView onCurrencyChanged={handleCurrencyChanged} />
        ) : (
          <div className="empty">「{TABS.find((t) => t.key === tab)?.label}」开发中…</div>
        )}
      </main>

      <footer className="footer">{bridgeInfo}</footer>
    </>
  );
}
