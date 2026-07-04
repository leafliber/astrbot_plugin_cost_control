import {
  CURRENCY_OPTIONS,
  currencyToSymbol,
  fmtCost,
  fmtNum,
  getCurrencyCode,
} from "../lib/format";
import type {
  BudgetOverrideRow,
  FallbackProvider,
  OnExceeded,
  OverrideTarget,
  Provider,
} from "../lib/types";

const TARGET_LABELS: Record<OverrideTarget, string> = {
  umo: "会话",
  provider: "Provider",
  user: "用户",
};

const TARGET_PLACEHOLDERS: Record<OverrideTarget, string> = {
  umo: "如 qq:12345 / platform:session_id",
  provider: "",
  user: "发送者 ID（QQ / 微信 / 钉钉）",
};

const ON_EXCEEDED_LABELS: Record<OnExceeded, string> = {
  stop: "硬拦截",
  fallback: "切备用",
  warn: "仅警告",
};

// 单条 override 规则的紧凑编辑卡。
// 主行按自然语序「当 [谁] · Token≤[x] $≤[y] → [处理]」排列，操作按钮内联到行末。
// 状态行：仅当设了上限时显示紧凑 mini bar。extra 行：仅当选中对应处理时显示其条件字段。
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
  const hasTokenLimit = (row.token_limit || 0) > 0;
  const hasCostLimit = (row.cost_limit || 0) > 0;
  const showStatus = hasTokenLimit || hasCostLimit;

  return (
    <div className={`override-card ${row.enabled ? "" : "is-disabled"}`}>
      <div className="override-main">
        <input
          type="checkbox"
          className="ov-enable"
          checked={row.enabled}
          onChange={(e) => onChange({ enabled: e.target.checked })}
          title="启用"
        />
        <span className="override-idx">{index + 1}</span>
        <span className="ov-sep">当</span>
        <select
          className="override-target"
          value={tgt}
          onChange={(e) =>
            onChange({ target_type: e.target.value as OverrideTarget, target_value: "" })
          }
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
            placeholder={TARGET_PLACEHOLDERS[tgt]}
          />
        )}

        <span className="ov-sep ov-sep-dot">·</span>
        <label className="ov-limit">
          <span className="muted small">Token≤</span>
          <input
            type="number"
            min="0"
            className="budget-input"
            value={row.token_limit || 0}
            onChange={(e) => onChange({ token_limit: Math.max(0, +e.target.value || 0) })}
            placeholder="0"
          />
        </label>
        <label className="ov-limit">
          <span className="muted small">{currencyToSymbol(row.cost_currency || getCurrencyCode())}≤</span>
          <input
            type="number"
            min="0"
            step="0.01"
            className="budget-input"
            value={row.cost_limit || 0}
            onChange={(e) => onChange({ cost_limit: Math.max(0, +e.target.value || 0) })}
            placeholder="0"
          />
          <select
            className="budget-input"
            value={row.cost_currency || ""}
            onChange={(e) => onChange({ cost_currency: e.target.value })}
            title="花费货币（留空=主货币）"
          >
            <option value="">主货币</option>
            {CURRENCY_OPTIONS.map((c) => (
              <option key={c} value={c}>
                {c} ({currencyToSymbol(c)})
              </option>
            ))}
          </select>
        </label>

        <span className="ov-sep">→</span>
        <select
          className="override-on"
          value={onExc}
          onChange={(e) => onChange({ on_exceeded: e.target.value as OnExceeded })}
          title="超限处理"
        >
          {(Object.keys(ON_EXCEEDED_LABELS) as OnExceeded[]).map((a) => (
            <option key={a} value={a}>
              {ON_EXCEEDED_LABELS[a]}
            </option>
          ))}
        </select>

        <div className="override-ops">
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

      {showStatus && (
        <div className="override-status">
          {hasTokenLimit && (
            <StatLine
              label="token"
              used={row.current?.token?.used || 0}
              limit={row.token_limit || 0}
              ratio={row.current?.token?.ratio || 0}
              exceeded={!!row.current?.token?.exceeded}
              fmt={fmtNum}
            />
          )}
          {hasCostLimit && (
            <StatLine
              label="cost"
              used={row.current?.cost?.used || 0}
              limit={row.cost_limit || 0}
              ratio={row.current?.cost?.ratio || 0}
              exceeded={!!row.current?.cost?.exceeded}
              fmt={fmtCost}
              prefix={currencyToSymbol(row.cost_currency || getCurrencyCode())}
            />
          )}
        </div>
      )}

      {onExc === "stop" && (
        <div className="override-extra">
          <span className="muted small">拦截文案</span>
          <input
            className="budget-input"
            value={row.stop_message || ""}
            placeholder="留空 = 默认文案（含维度 / used / limit）"
            onChange={(e) => onChange({ stop_message: e.target.value })}
          />
        </div>
      )}
      {onExc === "fallback" && (
        <div className="override-extra">
          <span className="muted small">备用（按序）</span>
          <FallbackProviderPicker
            selected={row.fallback_provider_ids}
            candidates={fallbackProviders}
            onChange={(ids) => onChange({ fallback_provider_ids: ids })}
          />
          <label className="ov-limit" style={{ marginLeft: "auto" }}>
            <span className="muted small">history 截断</span>
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
    </div>
  );
}

function StatLine({
  label,
  used,
  limit,
  ratio,
  exceeded,
  fmt,
  prefix = "",
}: {
  label: string;
  used: number;
  limit: number;
  ratio: number;
  exceeded: boolean;
  fmt: (n: number) => string;
  prefix?: string;
}) {
  const pct = Math.min(100, Math.max(0, ratio || 0));
  const cls = exceeded ? "bad" : pct >= 80 ? "warn" : "";
  return (
    <span className={`ov-stat ${cls}`}>
      <span className="muted small">{label}</span>
      <i className="ov-bar" style={{ backgroundSize: `${pct}% 100%` }} />
      <span className="ov-pct">{pct}%</span>
      <span className="muted small">
        {prefix}
        {fmt(used)} / {prefix}
        {fmt(limit)}
      </span>
    </span>
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
        <span className="muted small">请先在下方「备用 Provider 库」添加（否则将降级为硬拦截）</span>
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
