import { useState } from "react";
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
import type { Window } from "./lib/types";

const TABS = [
  { key: "overview", label: "总览" },
  { key: "records", label: "明细" },
  { key: "budgets", label: "预算" },
  { key: "cache", label: "缓存" },
  { key: "attribution", label: "归因" },
  { key: "pricing", label: "定价" },
  { key: "settings", label: "设置" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

export function App() {
  const { ready, ctx, failed } = useBridge();
  useTheme(ctx);
  const colors = useChartColors(!!ctx?.isDark);

  const [tab, setTab] = useState<TabKey>("overview");
  const [win, setWin] = useState<Window>("daily");
  const [refreshNonce, setRefreshNonce] = useState(0);

  const refresh = () => setRefreshNonce((n) => n + 1);
  const bridgeInfo = ctx
    ? `${ctx.displayName || "插件"} · ${ctx.locale || ""}`
    : "";

  const status = failed ? "未连接" : ready ? "已连接" : "连接中…";

  return (
    <>
      <header className="topbar">
        <div className="title-group">
          <h1>成本控制</h1>
          {tab === "overview" && (
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
          <OverviewView window={win} refreshNonce={refreshNonce} colors={colors} />
        ) : tab === "records" ? (
          <RecordsView />
        ) : tab === "budgets" ? (
          <BudgetsView />
        ) : tab === "cache" ? (
          <CacheView />
        ) : tab === "attribution" ? (
          <AttributionView />
        ) : tab === "pricing" ? (
          <PricingView />
        ) : tab === "settings" ? (
          <SettingsView />
        ) : (
          <div className="empty">「{TABS.find((t) => t.key === tab)?.label}」开发中…</div>
        )}
      </main>

      <footer className="footer">{bridgeInfo}</footer>
    </>
  );
}
