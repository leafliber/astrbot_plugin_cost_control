import type { AlertItem, AlertTab } from "../lib/types";

const TAB_LABEL: Record<AlertTab, string> = {
  cache: "缓存诊断",
  pricing: "定价",
  budgets: "预算",
};

// 总览页顶部黄色告警条：点击跳转到对应页查看详情或调整设置
export function AlertsBar({
  alerts,
  onNavigate,
}: {
  alerts: AlertItem[];
  onNavigate: (tab: AlertTab) => void;
}) {
  if (alerts.length === 0) return null;
  return (
    <div className="alerts-bar">
      {alerts.map((a, i) => (
        <div key={`${a.code}-${i}`} className="alert-item">
          <span className="alert-icon" aria-hidden>
            ⚠
          </span>
          <div className="alert-content">
            <span className="alert-title">{a.title}</span>
            <span className="alert-detail">{a.detail}</span>
          </div>
          <button
            className="alert-link"
            onClick={() => onNavigate(a.tab)}
            title={`前往「${TAB_LABEL[a.tab]}」页`}
          >
            前往{TAB_LABEL[a.tab]} →
          </button>
        </div>
      ))}
    </div>
  );
}
