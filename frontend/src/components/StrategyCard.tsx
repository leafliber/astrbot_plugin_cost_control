import type { Strategy } from "../lib/types";

// 超限策略链卡片：action 切换（拦截 / 切换备用 Provider）+ 启用 + 上下移 + 删除；
// fallback 模式下编辑 provider 列表（回车添加）与 token 上限。
export function StrategyCard({
  strategy,
  index,
  total,
  onChange,
  onMove,
  onDelete,
}: {
  strategy: Strategy;
  index: number;
  total: number;
  onChange: (patch: Partial<Strategy>) => void;
  onMove: (dir: "up" | "down") => void;
  onDelete: () => void;
}) {
  const fb = strategy.action === "fallback_provider";
  return (
    <div className={`strategy-card ${strategy.enabled ? "" : "is-disabled"}`}>
      <div className="strategy-head">
        <span className="strategy-idx">{index + 1}</span>
        <select
          className="s-action"
          value={strategy.action}
          onChange={(e) => onChange({ action: e.target.value })}
        >
          <option value="stop_llm">拦截 LLM 请求</option>
          <option value="fallback_provider">切换备用 Provider</option>
        </select>
        <label className="s-enabled">
          <input
            type="checkbox"
            checked={strategy.enabled}
            onChange={(e) => onChange({ enabled: e.target.checked })}
          />{" "}
          启用
        </label>
        <span className="strategy-move">
          <button
            type="button"
            className="move-btn"
            disabled={index === 0}
            onClick={() => onMove("up")}
          >
            ↑
          </button>
          <button
            type="button"
            className="move-btn"
            disabled={index === total - 1}
            onClick={() => onMove("down")}
          >
            ↓
          </button>
          <button type="button" className="move-btn del" onClick={onDelete}>
            ✕
          </button>
        </span>
      </div>
      <div className="strategy-field">
        {fb ? (
          <>
            <div className="field-row">
              <span className="muted small">备用 Provider（按序尝试）</span>
            </div>
            <div className="provider-tags">
              {strategy.provider_ids.map((pid, j) => (
                <span key={`${pid}-${j}`} className="provider-tag">
                  {pid}
                  <button
                    type="button"
                    className="tag-del"
                    onClick={() => {
                      const next = strategy.provider_ids.slice();
                      next.splice(j, 1);
                      onChange({ provider_ids: next });
                    }}
                  >
                    ✕
                  </button>
                </span>
              ))}
              {strategy.provider_ids.length === 0 && (
                <span className="muted small">（空，此策略将被跳过）</span>
              )}
            </div>
            <div className="field-row">
              <input
                type="text"
                list="prov-opts"
                className="pid-input"
                placeholder="选择或输入 Provider ID 后回车添加"
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    const el = e.target as HTMLInputElement;
                    const v = el.value.trim();
                    if (v) {
                      onChange({ provider_ids: [...strategy.provider_ids, v] });
                      el.value = "";
                    }
                  }
                }}
              />
            </div>
            <div className="field-row">
              <label>
                token 上限{" "}
                <input
                  type="number"
                  min="0"
                  className="s-token"
                  value={strategy.token_limit || 0}
                  style={{ width: 100 }}
                  onChange={(e) => onChange({ token_limit: +e.target.value || 0 })}
                />{" "}
                <span className="muted small">截断历史，0=不限</span>
              </label>
            </div>
          </>
        ) : (
          <div className="field-row">
            <label>
              拦截文案{" "}
              <input
                type="text"
                className="s-message"
                style={{ flex: 1 }}
                placeholder="留空=默认文案"
                value={strategy.message}
                onChange={(e) => onChange({ message: e.target.value })}
              />
            </label>
          </div>
        )}
      </div>
    </div>
  );
}
