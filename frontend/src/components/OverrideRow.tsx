import { ProgressBar } from "./ProgressBar";
import { fmtCost, fmtNum } from "../lib/format";
import type {
  BudgetOverrideRow,
  FallbackProvider,
  OnExceeded,
  OverrideTarget,
  Provider,
} from "../lib/types";

const TARGET_LABELS: Record<OverrideTarget, string> = {
  umo: "会话（umo）",
  provider: "Provider",
  user: "用户（user_id）",
};

const ON_EXCEEDED_LABELS: Record<OnExceeded, string> = {
  stop: "硬拦截",
  fallback: "切换备用 Provider",
  warn: "仅警告（不中断）",
};

// 单条 override 规则的 inline 编辑行。
// head 行：启用 / 序号 / 目标类型 / 目标值 / 超限处理 —— 突出「匹配谁 → 怎么处理」。
// limits 行：Token / Cost 阈值并排。extra 行：处理方案的条件字段。progress 行：实时消耗。
export function OverrideRow({
  row,
  index,
  total,
  providers,
  fallbackProviders,
  onChange,
  onMove,
  onDelete,
}: {
  row: BudgetOverrideRow;
  index: number;
  total: number;
  providers?: Provider[];
  fallbackProviders: FallbackProvider[];
  onChange: (patch: Partial<BudgetOverrideRow>) => void;
  onMove: (dir: "up" | "down") => void;
  onDelete: () => void;
}) {
  const tgt = row.target_type;
  const onExc = row.on_exceeded;

  return (
    <div className={`override-row ${row.enabled ? "" : "is-disabled"}`}>
      <div className="override-head">
        <input
          type="checkbox"
          checked={row.enabled}
          onChange={(e) => onChange({ enabled: e.target.checked })}
          title="启用"
        />
        <span className="override-idx">{index + 1}</span>
        <select
          className="override-target"
          value={tgt}
          onChange={(e) => onChange({ target_type: e.target.value as OverrideTarget, target_value: "" })}
        >
          {(Object.keys(TARGET_LABELS) as OverrideTarget[]).map((t) => (
            <option key={t} value={t}>
              {TARGET_LABELS[t]}
            </option>
          ))}
        </select>
        {tgt === "provider" ? (
          <select
            className="override-value"
            value={row.target_value}
            onChange={(e) => onChange({ target_value: e.target.value })}
          >
            <option value="">选择 Provider</option>
            {(providers || []).map((p) => (
              <option key={p.id} value={p.id}>
                {p.id}
                {p.model ? ` (${p.model})` : ""}
              </option>
            ))}
          </select>
        ) : (
          <input
            className="override-value"
            value={row.target_value}
            onChange={(e) => onChange({ target_value: e.target.value })}
            placeholder={
              tgt === "umo"
                ? "如 qq:123456 或 platform:session_id"
                : "发送者 ID（QQ/微信/钉钉 等）"
            }
            style={{ flex: 1, minWidth: 200 }}
          />
        )}
        <span className="override-on-label muted small">超限处理</span>
        <select
          className="override-on"
          value={onExc}
          onChange={(e) => onChange({ on_exceeded: e.target.value as OnExceeded })}
        >
          {(Object.keys(ON_EXCEEDED_LABELS) as OnExceeded[]).map((a) => (
            <option key={a} value={a}>
              {ON_EXCEEDED_LABELS[a]}
            </option>
          ))}
        </select>
      </div>

      <div className="override-limits">
        <label className="limit-cell">
          <span className="muted small">Token 上限</span>
          <input
            type="number"
            min="0"
            className="budget-input"
            value={row.token_limit || 0}
            onChange={(e) => onChange({ token_limit: Math.max(0, +e.target.value || 0) })}
            style={{ width: 110 }}
          />
        </label>
        <label className="limit-cell">
          <span className="muted small">花费上限 $</span>
          <input
            type="number"
            min="0"
            step="0.01"
            className="budget-input"
            value={row.cost_limit || 0}
            onChange={(e) => onChange({ cost_limit: Math.max(0, +e.target.value || 0) })}
            style={{ width: 110 }}
          />
        </label>
      </div>

      {onExc === "stop" && (
        <div className="override-extra">
          <span className="muted small">拦截文案</span>
          <input
            className="budget-input"
            value={row.stop_message || ""}
            placeholder="留空 = 默认文案（含 dim / used / limit）"
            onChange={(e) => onChange({ stop_message: e.target.value })}
            style={{ flex: 1, minWidth: 240 }}
          />
        </div>
      )}
      {onExc === "fallback" && (
        <div className="override-extra">
          <span className="muted small">备用 Provider（按序）</span>
          <FallbackProviderPicker
            selected={row.fallback_provider_ids}
            candidates={fallbackProviders}
            onChange={(ids) => onChange({ fallback_provider_ids: ids })}
          />
          <label className="limit-cell" style={{ marginLeft: 8 }}>
            <span className="muted small">history token 截断</span>
            <input
              type="number"
              min="0"
              className="budget-input"
              value={row.fallback_token_limit || 0}
              onChange={(e) =>
                onChange({ fallback_token_limit: Math.max(0, +e.target.value || 0) })
              }
              style={{ width: 80 }}
            />
            <span className="muted small">0=不限</span>
          </label>
        </div>
      )}

      <div className="override-progress">
        <div className="progress-cell">
          <div className="muted small">
            token {fmtNum(row.current?.token?.used || 0)} / {fmtNum(row.token_limit || 0)}
          </div>
          {row.token_limit > 0 ? (
            <ProgressBar ratio={row.current?.token?.ratio || 0}>
              {row.current?.token?.ratio || 0}%
            </ProgressBar>
          ) : (
            <div className="muted small">不限</div>
          )}
        </div>
        <div className="progress-cell">
          <div className="muted small">
            cost {fmtCost(row.current?.cost?.used || 0)} / {fmtCost(row.cost_limit || 0)}
          </div>
          {row.cost_limit > 0 ? (
            <ProgressBar ratio={row.current?.cost?.ratio || 0}>
              {row.current?.cost?.ratio || 0}%
            </ProgressBar>
          ) : (
            <div className="muted small">不限</div>
          )}
        </div>
      </div>

      <div className="override-move">
        <button
          type="button"
          className="move-btn"
          disabled={index === 0}
          onClick={() => onMove("up")}
          title="上移"
        >
          ↑
        </button>
        <button
          type="button"
          className="move-btn"
          disabled={index === total - 1}
          onClick={() => onMove("down")}
          title="下移"
        >
          ↓
        </button>
        <button type="button" className="move-btn del" onClick={onDelete} title="删除">
          ✕
        </button>
      </div>
    </div>
  );
}

function FallbackProviderPicker({
  selected,
  candidates,
  onChange,
}: {
  selected: string[];
  candidates: FallbackProvider[];
  onChange: (ids: string[]) => void;
}) {
  const empty = candidates.length === 0;
  return (
    <div className="fb-picker">
      {selected.length === 0 && (
        <span className="muted small" style={{ marginRight: 6 }}>
          {empty ? "（未配备用，将降级为 stop）" : "（空，将降级为 stop）"}
        </span>
      )}
      {selected.map((id, j) => (
        <span key={`${id}-${j}`} className="provider-tag">
          {id}
          <button
            type="button"
            className="tag-del"
            onClick={() => {
              const next = selected.slice();
              next.splice(j, 1);
              onChange(next);
            }}
          >
            ✕
          </button>
        </span>
      ))}
      {empty ? (
        <select className="fb-add" disabled value="">
          <option value="">请先在下方「备用 Provider 库」添加</option>
        </select>
      ) : (
        <select
          className="fb-add"
          value=""
          onChange={(e) => {
            const v = e.target.value;
            if (v && !selected.includes(v)) {
              onChange([...selected, v]);
            }
            e.target.value = "";
          }}
        >
          <option value="">+ 从备用库添加</option>
          {candidates.map((c) => (
            <option key={c.id} value={c.id}>
              {c.id}
              {c.note ? ` · ${c.note}` : ""}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
