import type { SourceStatus } from "../../lib/types";
import { Button } from "../Button";

// 价格源状态条（F1/F6）：各源启用状态、模型数量、最近同步和手动同步入口。

const SOURCE_LABELS: Record<string, string> = {
  modelsdev: "models.dev",
  litellm: "LiteLLM",
  openrouter: "OpenRouter",
};

export function sourceLabel(source: string): string {
  if (source.startsWith("newapi:")) {
    return `New API (${source.slice("newapi:".length)})`;
  }
  return SOURCE_LABELS[source] || source;
}

function fmtTime(iso?: string): string {
  if (!iso) return "未同步";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function statusLabel(status: SourceStatus["status"]): string {
  if (status === "ok") return "正常";
  if (status === "error") return "失败";
  return "待同步";
}

export function SourceStatusBar({
  sources,
  syncing,
  onSync,
  onToggleSource,
  autoSync,
  onToggleAutoSync,
}: {
  sources: Record<string, SourceStatus>;
  syncing: boolean;
  onSync: () => void;
  onToggleSource: (source: string, enabled: boolean) => void;
  autoSync: boolean;
  onToggleAutoSync: (enabled: boolean) => void;
}) {
  const ids = Object.keys(sources);
  return (
    <div className="source-bar">
      <div className="source-bar-chips">
        {ids.length === 0 && (
          <span className="muted small">尚未同步任何价格源，点击右侧按钮拉取。</span>
        )}
        {ids.map((sourceId) => {
          const source = sources[sourceId];
          const title = source.error
            ? `错误：${source.error}`
            : `${source.models} 个模型 · 最近同步 ${fmtTime(source.updated_at)}`;
          return (
            <span
              key={sourceId}
              className={`source-chip source-chip--${source.status} ${source.enabled ? "" : "is-disabled"}`}
            >
              <button
                type="button"
                className="source-chip-toggle"
                title={`${sourceLabel(sourceId)} · ${title}（点击${source.enabled ? "停用" : "启用"}该源）`}
                onClick={() => onToggleSource(sourceId, !source.enabled)}
              >
                <span className="source-status-text">{statusLabel(source.status)}</span>
                {sourceLabel(sourceId)}
                {source.models > 0 && <span className="muted small"> {source.models}</span>}
              </button>
            </span>
          );
        })}
      </div>
      <div className="source-bar-actions">
        <label className="source-auto-sync" title="启用后按插件配置的 Cron 定时拉取价格；默认关闭">
          <input
            type="checkbox"
            checked={autoSync}
            disabled={syncing}
            onChange={(event) => onToggleAutoSync(event.target.checked)}
          />
          每日自动同步
        </label>
        <Button onClick={onSync} disabled={syncing} variant="primary">
          {syncing ? "同步中…" : "拉取最新价格"}
        </Button>
      </div>
    </div>
  );
}
